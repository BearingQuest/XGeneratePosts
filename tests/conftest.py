"""Shared fixtures. No test may touch the network: sockets are blocked, and
HTTP goes through FakeWeb, a urllib handler that serves canned responses."""

import email.message
import io
import json
import socket
import urllib.request
import urllib.response

import pytest

from xgenerateposts import net, progress, storage

ENV_VARS = (
    "AI_POST_PROVIDER",
    "AI_POST_SEARCH",
    "AI_POST_PROVIDER_TIMEOUT",
    "AI_POST_RSS_PER_FEED",
    "AI_POST_RSS_MAX_ENTRIES",
    "AI_POST_RATE_LIMIT_WAIT",
    "AI_POST_TOKEN_BUDGET",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_MODEL",
    "OPENAI_COMPAT_API_KEY",
)

SENTINEL_KEY = "gsk_SENTINEL_do_not_print_0123456789abcdef"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Clean environment and a throwaway data directory for every test."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # config reads these at call time, so the real ~/XPosts and ~/.ai_post_env
    # are never touched.
    monkeypatch.setenv("AI_POST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_POST_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setattr(storage, "RUN_VIA", "")
    return tmp_path


class FakeClock:
    """progress's clock and sleep: sleeping moves the clock, instantly."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """No unit test really waits on a retry countdown (R51)."""
    fake = FakeClock()
    monkeypatch.setattr(progress, "monotonic", fake.monotonic)
    monkeypatch.setattr(progress, "sleep", fake.sleep)
    return fake


class FakeWeb(urllib.request.HTTPSHandler):
    """Serves registered URLs; anything else fails the test.

    Responses travel through the real opener, so status codes and redirects
    are handled by urllib itself, including NoDowngradeRedirectHandler.
    """

    # build_opener still adds urllib's HTTPHandler (500), and on a tie the one
    # added first wins, so this sorts ahead of it for http:// URLs.
    handler_order = 499

    def __init__(self):
        super().__init__()
        self.routes = {}
        self.requests = []

    def add(self, url, body=b"", status=200, headers=None):
        """body may be bytes, a dict (sent as JSON) or an exception to raise."""
        self.routes[url] = [(body, status, headers or {})]

    def add_sequence(self, url, responses):
        """(body, status, headers) tuples served in order; the last one repeats."""
        self.routes[url] = list(responses)

    def redirect(self, url, location, status=301):
        self.add(url, status=status, headers={"Location": location})

    def https_open(self, req):
        self.requests.append(req)
        if req.full_url not in self.routes:
            raise AssertionError(f"unexpected request to {req.full_url}")
        route = self.routes[req.full_url]
        body, status, headers = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(body, BaseException):
            raise body
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        msg = email.message.Message()
        for name, value in headers.items():
            msg[name] = value
        response = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, status)
        response.msg = "fake"
        return response

    http_open = https_open  # for a local openai-compat base URL


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(
        net, "HTTP_OPENER", urllib.request.build_opener(fake, net.NoDowngradeRedirectHandler)
    )
    return fake


def completion(text):
    """An OpenAI-compatible chat completion response carrying `text`."""
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}


MODEL_OUTPUT = """searched snippets the model saw first
===URL===
https://vendor.example/post
===TITLE===
A Real Release
===SOURCE===
Vendor
===TWEET===
Vendor shipped a thing that matters.
===LINKEDIN===
A longer take on why it matters.

What would you ask first?

#Tag
===WHY===
It is substantive.
===END===
"""


@pytest.fixture
def profile_file(tmp_path):
    path = tmp_path / "profile.md"
    path.write_text(
        "## Topics\n- Cloud\n- Security\n\n"
        "## Beat\nPractitioners.\n\n"
        "## Voice\nPlain.\n\n"
        "## Feeds\nNotes about feeds are ignored.\n"
        "- https://feeds.example/a.xml\n"
        "- https://feeds.example/b.xml\n"
    )
    return path
