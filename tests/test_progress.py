"""R52: progress lines on stderr, in place on a terminal, start and end
lines otherwise. The ticker thread always stops and leaves the line clean."""

import io
import os
import pty
import select
import signal
import subprocess
import sys
import threading
import time
import urllib.error

import pytest

from xgenerateposts import config, progress

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Terminal(io.StringIO):
    """A stream that says it is a terminal, safe to write from two threads."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()

    def isatty(self):
        return True

    def write(self, text):
        with self.lock:
            return super().write(text)

    def text(self):
        with self.lock:
            return self.getvalue()


@pytest.fixture
def fast_ticks(monkeypatch):
    monkeypatch.setattr(progress, "TICK", 0.01)


def ticker_threads():
    return [t for t in threading.enumerate() if t.name == "xgp-progress"]


def wait_for_redraws(stream, count, timeout=5.0):
    deadline = time.monotonic() + timeout
    while stream.text().count(progress.CLEAR) < count:
        if time.monotonic() > deadline:
            pytest.fail(f"the ticker redrew fewer than {count} times")
        time.sleep(0.005)


def test_terminal_redraws_in_place_then_prints_the_end_line(fast_ticks):
    stream = Terminal()
    with progress.waiting_for("Groq", stream):
        wait_for_redraws(stream, 3)
        assert "\n" not in stream.text()  # one line, rewritten
    out = stream.text()
    assert "Waiting for Groq's answer…" in out
    assert out.endswith(" s.\n")
    assert "Groq answered in" in out.rsplit(progress.CLEAR, 1)[1]
    assert not ticker_threads()


@pytest.mark.parametrize(
    "error",
    [
        SystemExit("\nsomething failed\n"),
        urllib.error.HTTPError("https://x", 500, "boom", {}, None),
        KeyboardInterrupt(),
    ],
)
def test_ticker_stops_and_clears_the_line_on_error_and_ctrl_c(fast_ticks, error):
    stream = Terminal()
    with pytest.raises(type(error)), progress.waiting_for("Groq", stream):
        wait_for_redraws(stream, 2)
        raise error
    assert stream.text().endswith(progress.CLEAR)  # no end line, nothing left behind
    assert "answered" not in stream.text()
    assert not ticker_threads()


def test_not_a_terminal_prints_a_start_and_an_end_line_only():
    stream = io.StringIO()
    before = threading.active_count()
    with progress.waiting_for("openai-compat", stream):
        assert threading.active_count() == before  # no ticker thread
    lines = stream.getvalue().splitlines()
    assert lines[0] == "Waiting for openai-compat's answer…"
    assert lines[1].startswith("openai-compat answered in ")
    assert len(lines) == 2
    assert "\r" not in stream.getvalue()


def test_not_a_terminal_prints_no_end_line_on_error():
    stream = io.StringIO()
    with pytest.raises(SystemExit), progress.waiting_for("Groq", stream):
        raise SystemExit("failed")
    assert stream.getvalue() == "Waiting for Groq's answer…\n"


def test_countdown_names_the_reason_and_sleeps_the_whole_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(progress, "sleep", slept.append)
    stream = io.StringIO()
    progress.countdown(27.2, "Groq's per-minute token limit", stream)
    assert slept == [28]
    assert stream.getvalue().splitlines() == [
        "Groq's per-minute token limit: retrying in 28 s (Ctrl+C to cancel)",
        "Retrying now.",
    ]


def test_countdown_on_a_terminal_counts_down(monkeypatch, fast_ticks):
    clock = [100.0]
    monkeypatch.setattr(progress, "monotonic", lambda: clock[0])
    stream = Terminal()

    def fake_sleep(seconds):
        drawn = stream.text().count(progress.CLEAR)
        clock[0] += 10
        wait_for_redraws(stream, drawn + 2)  # at least one full redraw after the clock moved

    monkeypatch.setattr(progress, "sleep", fake_sleep)
    progress.countdown(27.2, "Groq's per-minute token limit", stream)
    out = stream.text()
    assert "retrying in 28 s" in out
    assert "retrying in 18 s" in out
    assert out.endswith("Retrying now.\n")
    assert not ticker_threads()


def test_progress_is_never_written_to_run_log(fast_ticks):
    with progress.waiting_for("Groq", Terminal()):
        pass
    with progress.waiting_for("Groq", io.StringIO()):
        pass
    assert not config.log_file().exists()


CHILD = """
import sys, time
from xgenerateposts import cli, progress

def run():
    with progress.waiting_for("Groq"):
        sys.stderr.write("READY\\n")
        sys.stderr.flush()
        time.sleep(30)

cli.run = run
cli.main()
"""


def test_ctrl_c_on_a_real_terminal_cancels_and_leaves_the_line_clean(tmp_path):
    """A real SIGINT, with stderr on a pty so the ticker thread runs."""
    master, slave = pty.openpty()
    child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", CHILD],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=slave,
        env={**os.environ, "AI_POST_DATA_DIR": str(tmp_path)},
    )
    # The parent keeps its copy of the slave open until the output is read:
    # macOS drops unread pty output once every slave descriptor is closed.
    seen = b""
    try:
        deadline = time.monotonic() + 10
        while b"READY" not in seen:
            left = deadline - time.monotonic()
            if left <= 0:
                pytest.fail(f"the child never reported READY; pty output: {seen!r}")
            ready, _, _ = select.select([master], [], [], left)
            if ready:
                seen += os.read(master, 1024)
        child.send_signal(signal.SIGINT)
        stdout, _ = child.communicate(timeout=10)
        # The child has exited, so everything it wrote is already buffered.
        while select.select([master], [], [], 1)[0]:
            seen += os.read(master, 1024)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(slave)
        os.close(master)
    assert child.returncode == 1
    assert b"Cancelled." in stdout
    assert seen.endswith(progress.CLEAR.encode())
    assert b"Traceback" not in seen
