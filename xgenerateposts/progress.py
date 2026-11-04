"""Progress while the user waits (R52), on stderr only and never in run.log.

On a terminal, one line is redrawn in place about once a second. Otherwise a
start line and an end line are printed, with no ticker.
"""

import math
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TextIO

# Injection points: the unit tests replace these, so nothing really waits.
monotonic = time.monotonic
sleep = time.sleep
TICK = 1.0  # seconds between redraws on a terminal

CLEAR = "\r\033[K"  # back to the start of the line, then erase it


@contextmanager
def status(
    start: str,
    tick: Callable[[float], str],
    end: Callable[[float], str] | None = None,
    stream: TextIO | None = None,
) -> Iterator[None]:
    """Show progress for the duration of the block.

    `tick` and `end` get the seconds elapsed. The end line is printed only if
    the block succeeds; on an error or Ctrl+C the line is cleared and the
    caller's message follows.
    """
    out = stream or sys.stderr
    began = monotonic()

    def write(text: str) -> None:
        out.write(text)
        out.flush()

    if not out.isatty():
        write(f"{start}\n")
        yield
        if end:
            write(f"{end(monotonic() - began)}\n")
        return

    stop = threading.Event()

    def redraw() -> None:
        while not stop.wait(TICK):
            write(CLEAR + tick(monotonic() - began))

    write(CLEAR + tick(0.0))
    ticker = threading.Thread(target=redraw, name="xgp-progress", daemon=True)
    ticker.start()
    try:
        yield
    finally:
        stop.set()
        ticker.join()
        write(CLEAR)
    if end:
        write(f"{end(monotonic() - began)}\n")


def waiting_for(label: str, stream: TextIO | None = None) -> AbstractContextManager[None]:
    """Elapsed seconds while a provider works on its answer."""
    waiting = f"Waiting for {label}'s answer…"
    return status(
        waiting,
        lambda s: f"{waiting} {s:.0f} s",
        lambda s: f"{label} answered in {s:.1f} s.",
        stream,
    )


def countdown(seconds: float, reason: str, stream: TextIO | None = None) -> None:
    """Wait `seconds`, counting down, before a retry. Ctrl+C cancels."""
    total = math.ceil(seconds)
    with status(
        f"{reason}: retrying in {total} s (Ctrl+C to cancel)",
        lambda s: f"{reason}: retrying in {max(math.ceil(total - s), 0)} s (Ctrl+C to cancel)",
        lambda s: "Retrying now.",
        stream,
    ):
        sleep(total)
