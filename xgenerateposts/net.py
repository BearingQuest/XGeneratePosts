"""HTTP plumbing shared by the providers, the feed reader and LinkedIn."""

import urllib.error
import urllib.parse
import urllib.request

from xgenerateposts.config import MAX_RESPONSE_BYTES

DEFAULT_PORTS = {"http": 80, "https": 443}


class InsecureRedirectError(urllib.error.URLError):
    """An HTTPS request was redirected to a non-HTTPS URL."""


def origin(url: str) -> tuple[str, str, int | None] | None:
    """Scheme, lower-cased host and port, with default ports filled in.

    None if the port can't be parsed; same_origin treats that as another origin.
    """
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    return scheme, parts.hostname or "", port


def same_origin(a: str, b: str) -> bool:
    origin_a = origin(a)
    return origin_a is not None and origin_a == origin(b)


class NoDowngradeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, except from HTTPS to anything that is not HTTPS (R18).

    A downgraded feed can be altered in transit. urllib copies every request
    header except Content-Length and Content-Type to the redirect target,
    whatever its host, so credentials are removed when the origin changes
    (R55), as the requests library does.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.lower().startswith("https://") and not newurl.lower().startswith(
            "https://"
        ):
            raise InsecureRedirectError(f"refused redirect from HTTPS to {newurl}")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not same_origin(req.full_url, newurl):
            # Request.add_header stores names capitalize()d, in new.headers.
            # ProxyHandler re-adds Proxy-authorization when the target is
            # proxied, so this only drops it for a target that bypasses the proxy.
            new.remove_header("Authorization")
            new.remove_header("Proxy-authorization")
        return new


HTTP_OPENER = urllib.request.build_opener(NoDowngradeRedirectHandler)


def read_capped(response, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most `limit` bytes; anything larger is refused, not truncated."""
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"response is larger than {limit:,} bytes")
    return body


def redact(text: str, *secrets: str) -> str:
    """Remove secret values from text that is about to be shown or logged."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text
