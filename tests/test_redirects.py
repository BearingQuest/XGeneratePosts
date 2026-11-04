"""R55: credentials never follow a redirect to another origin. R56: LinkedIn
goes through the shared opener. Redirects go through the real opener, served
by FakeWeb."""

import urllib.error
import urllib.request

import pytest

from xgenerateposts import config, net, publish

TOKEN = "Bearer tok_SENTINEL_0123456789"  # noqa: S105 - fake token for assertions
LINKEDIN_POSTS = "https://api.linkedin.com/rest/posts"
LINKEDIN_TOKEN = "li_SENTINEL_do_not_print_0123456789"  # noqa: S105 - fake token


def header_names(req):
    return {name.lower() for name, _ in req.header_items()}


def fetch(url, headers=None):
    headers = {"Authorization": TOKEN, **(headers or {})}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - test URLs, FakeWeb only
    with net.HTTP_OPENER.open(request, timeout=5) as response:
        return response.read()


@pytest.mark.parametrize(
    "start, target",
    [
        ("https://a.example/x", "https://b.example/y"),  # another host
        ("https://a.example/x", "https://a.example:8443/y"),  # another port
        ("http://a.example/x", "https://a.example/y"),  # another scheme
    ],
)
def test_cross_origin_redirect_drops_authorization(web, start, target):
    web.redirect(start, target, status=302)
    web.add(target, b"ok")
    assert fetch(start) == b"ok"
    first, second = web.requests
    assert "authorization" in header_names(first)
    assert "authorization" not in header_names(second)


def test_same_origin_redirect_keeps_authorization(web):
    """Host case and an explicit default port don't make another origin."""
    web.redirect("https://a.example/x", "https://A.example:443/y")
    web.add("https://A.example:443/y", b"ok")
    assert fetch("https://a.example/x") == b"ok"
    assert web.requests[1].get_header("Authorization") == TOKEN


def test_cross_origin_redirect_drops_proxy_authorization(web, monkeypatch):
    """With no proxy for the target, Proxy-Authorization would reach it directly."""
    monkeypatch.setattr(
        net,
        "HTTP_OPENER",
        urllib.request.build_opener(
            urllib.request.ProxyHandler({}), web, net.NoDowngradeRedirectHandler
        ),
    )
    web.redirect("https://a.example/x", "https://b.example/y")
    web.add("https://b.example/y", b"ok")
    fetch("https://a.example/x", {"Proxy-Authorization": "Basic dXNlcjpwYXNz"})
    first, second = web.requests
    assert "proxy-authorization" in header_names(first)
    assert {"authorization", "proxy-authorization"}.isdisjoint(header_names(second))


def test_https_to_http_is_still_refused(web):
    web.redirect("https://a.example/x", "http://a.example/y")
    with pytest.raises(net.InsecureRedirectError, match="refused redirect from HTTPS to http://"):
        fetch("https://a.example/x")
    assert len(web.requests) == 1


def test_unparseable_port_counts_as_another_origin():
    assert net.origin("https://a.example:99999999/") is None
    assert not net.same_origin("https://a.example:99999999/", "https://a.example:99999999/")
    assert net.same_origin("HTTPS://A.example/x", "https://a.example:443/y")


@pytest.fixture
def linkedin(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", LINKEDIN_TOKEN)
    monkeypatch.setenv("LINKEDIN_AUTHOR_URN", "urn:li:person:TEST")


def test_linkedin_uses_the_shared_opener_with_the_timeout(web, linkedin):
    web.add(LINKEDIN_POSTS, status=201, headers={"x-restli-id": "urn:li:share:1"})
    assert publish.post_to_linkedin("hello") == "urn:li:share:1"
    (request,) = web.requests
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {LINKEDIN_TOKEN}"
    assert request.timeout == config.LINKEDIN_TIMEOUT


def test_linkedin_oversized_response_is_refused(web, linkedin):
    web.add(LINKEDIN_POSTS, b"x" * (config.MAX_RESPONSE_BYTES + 1), status=201)
    with pytest.raises(SystemExit) as exc:
        publish.post_to_linkedin("hello")
    assert "LinkedIn response refused: response is larger than 2,000,000 bytes" in exc.value.code
    assert "may have published the post" in exc.value.code


def test_linkedin_read_timeout_says_the_post_may_be_live(web, linkedin):
    web.add(LINKEDIN_POSTS, TimeoutError("timed out"))
    with pytest.raises(SystemExit) as exc:
        publish.post_to_linkedin("hello")
    assert "within 30 seconds" in exc.value.code
    assert "may have published the post" in exc.value.code


def test_linkedin_cross_origin_redirect_sends_no_token(web, linkedin, capsys):
    web.redirect(LINKEDIN_POSTS, "https://elsewhere.example/landing", status=302)
    web.add("https://elsewhere.example/landing", b"ok")
    publish.post_to_linkedin("hello")
    first, second = web.requests
    assert first.get_header("Authorization") == f"Bearer {LINKEDIN_TOKEN}"
    assert "authorization" not in header_names(second)
    assert all(LINKEDIN_TOKEN not in value for _, value in second.header_items())
    assert LINKEDIN_TOKEN not in "".join(capsys.readouterr())


def test_linkedin_error_messages_are_unchanged(web, linkedin):
    web.add(LINKEDIN_POSTS, b'{"message": "bad version"}', status=426)
    with pytest.raises(SystemExit) as exc:
        publish.post_to_linkedin("hello")
    assert exc.value.code.startswith('\nLinkedIn rejected the post (426):\n\n  {"message"')
    assert "LINKEDIN_API_VERSION" in exc.value.code

    web.add(LINKEDIN_POSTS, urllib.error.URLError("no route"))
    with pytest.raises(SystemExit) as exc:
        publish.post_to_linkedin("hello")
    assert exc.value.code == "\nCould not reach LinkedIn: no route\n"
