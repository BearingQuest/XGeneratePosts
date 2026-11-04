"""R55: credentials never follow a redirect to another origin. Redirects go
through the real opener, served by FakeWeb."""

import urllib.request

import pytest

from xgenerateposts import net

TOKEN = "Bearer tok_SENTINEL_0123456789"  # noqa: S105 - fake token for assertions


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
