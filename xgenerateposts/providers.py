"""Providers: each takes the assembled prompt and returns the model's raw text."""

import json
import re
import urllib.error
import urllib.request

from xgenerateposts import config, net, progress
from xgenerateposts.config import (
    CONSOLE_BILLING,
    CONSOLE_KEYS,
    DEFAULT_PROVIDER,
    DEFAULT_RSS_MAX_ENTRIES,
    DEFAULT_RSS_PER_FEED,
    GROQ_BASE_URL,
    GROQ_DEFAULT_MODEL,
    GROQ_KEYS,
    HTTP_USER_AGENT,
    MODEL,
    NOTHING_LOST,
    PROVIDERS,
)


def friendly_api_error(exc: Exception) -> str:
    """Turn an Anthropic SDK exception into something worth reading.

    The raw exceptions are accurate but buried in a traceback; every case here
    has a specific thing the user should go do about it.
    """
    import anthropic  # only reached after AnthropicProvider imported it

    detail = str(exc)

    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Your Anthropic API key was rejected.\n\n"
            f"  Check the key in {config.env_file()} — a real key is 100+ characters\n"
            "  and starts with 'sk-ant-'. The console only shows a masked\n"
            "  preview after creation, and pasting that masked value is the\n"
            "  usual cause. Create a fresh key and copy it from the dialog:\n"
            f"  {CONSOLE_KEYS}\n\n"
            "  Also check that a stale ANTHROPIC_API_KEY is not set in your\n"
            "  shell — an existing environment variable overrides the file.\n"
            "  Run 'unset ANTHROPIC_API_KEY' or open a new terminal."
        )

    if isinstance(exc, anthropic.PermissionDeniedError):
        return (
            "Your API key does not have permission for this request.\n\n"
            f"  Check the key's workspace and scopes: {CONSOLE_KEYS}"
        )

    if isinstance(exc, anthropic.RateLimitError):
        return (
            "Rate limited by the Anthropic API.\n\n"
            "  Wait a minute and re-run. If this keeps happening, check\n"
            f"  your usage tier and limits: {CONSOLE_BILLING}\n\n"
            f"  {NOTHING_LOST}"
        )

    if isinstance(exc, anthropic.BadRequestError):
        if "credit balance" in detail.lower():
            return (
                "Your Anthropic API credit balance is empty.\n\n"
                f"  Add credits here: {CONSOLE_BILLING}\n\n"
                "  API credits are prepaid and separate from any Claude\n"
                "  subscription. Each run costs a few cents, so a small\n"
                "  top-up lasts a long time. Setting a low-balance alert on\n"
                "  that page prevents the surprise next time.\n\n"
                f"  {NOTHING_LOST}"
            )
        return (
            f"The API rejected the request:\n\n  {detail}\n\n"
            "  This usually means the model name or a tool definition in\n"
            "  the script needs updating against current API docs."
        )

    if isinstance(exc, anthropic.APIConnectionError):
        return (
            "Could not reach the Anthropic API.\n\n"
            "  Check your network connection, then re-run. If you are on\n"
            "  a VPN or a corporate network, that is the first thing to\n"
            f"  rule out.\n\n  {NOTHING_LOST}"
        )

    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", "unknown")
        if status in (500, 502, 503, 529):
            return (
                f"The Anthropic API returned a server error ({status}).\n\n"
                "  This is on their end, not yours. Wait a few minutes and\n"
                f"  re-run.\n\n  {NOTHING_LOST}"
            )
        return f"The Anthropic API returned an error ({status}):\n\n  {detail}"

    return f"Unexpected API error:\n\n  {detail}"


class AnthropicProvider:
    """Claude with Anthropic's server-side web_search tool. Paid."""

    name = "anthropic"
    label = "Anthropic"

    def __init__(self) -> None:
        self.model = MODEL

    def require_credentials(self) -> None:
        if not config.anthropic_api_key():
            raise SystemExit(
                f"\nNo ANTHROPIC_API_KEY found (checked {config.env_file()} and the "
                f"environment).\n\n  Create one at {CONSOLE_KEYS}, then:\n\n"
                f"    echo 'export ANTHROPIC_API_KEY=sk-ant-...' > {config.env_file()}\n"
                f"    chmod 600 {config.env_file()}\n"
            )

    def generate(self, prompt: str, native_search: bool = True) -> str:
        try:
            import anthropic  # imported lazily so other providers need no SDK
        except ImportError:
            raise SystemExit(
                "\n--provider anthropic needs the anthropic package (the 'anthropic' extra).\n"
                "From the repo clone, install it one of two ways:\n\n"
                "  installed xgp:      uv tool install --reinstall '.[anthropic]'\n"
                "  the clone's venv:   ~/.ai_post_venv/bin/pip install -e '.[anthropic]'\n"
            ) from None

        extra = {}
        if native_search:
            extra["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
        try:
            with progress.waiting_for(self.label):
                response = anthropic.Anthropic().messages.create(
                    model=self.model,
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                    **extra,
                )
        except anthropic.AnthropicError as exc:
            raise SystemExit(f"\n{friendly_api_error(exc)}\n") from None

        # Web-search responses contain several block types; keep only text blocks.
        return "\n".join(b.text for b in response.content if b.type == "text")


class OpenAICompatProvider:
    """Any OpenAI-compatible chat completions endpoint, via the standard
    library. Base URL and model come from the environment (R4)."""

    name = "openai-compat"
    label = "openai-compat"  # for progress lines (R52)
    key_var = "OPENAI_COMPAT_API_KEY"
    keys_url = ""  # depends on the service behind the base URL

    def __init__(self) -> None:
        self.base_url = config.openai_compat_base_url()
        self.model = config.openai_compat_model()

    def api_key(self) -> str:
        return config.api_key(self.key_var)

    def require_credentials(self) -> None:
        missing = [
            var
            for var, value in (
                ("OPENAI_COMPAT_BASE_URL", self.base_url),
                ("OPENAI_COMPAT_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                f"\n--provider openai-compat needs {' and '.join(missing)} in "
                f"{config.env_file()}.\n\n"
                "  OPENAI_COMPAT_BASE_URL is the endpoint up to /v1, for example\n"
                "  http://localhost:11434/v1 for a local Ollama server.\n"
                "  OPENAI_COMPAT_MODEL is the model name that service expects.\n"
                "  OPENAI_COMPAT_API_KEY is the service's API key; a local\n"
                "  Ollama server needs none. Get it from that service's console.\n"
            )
        local = re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", self.base_url)
        if not self.base_url.startswith("https://") and not local:
            raise SystemExit(
                "\nOPENAI_COMPAT_BASE_URL must start with https:// (plain http is\n"
                "  allowed only for localhost, e.g. a local Ollama server).\n"
            )

    def request_body(self, prompt: str, native_search: bool) -> dict:
        # No server-side search here; select_search refuses native for this provider.
        return {"model": self.model, "messages": [{"role": "user", "content": prompt}]}

    def generate(self, prompt: str, native_search: bool = False) -> str:
        payload = chat_completion(self, self.request_body(prompt, native_search), native_search)
        try:
            text = payload["choices"][0]["message"]["content"]
        except KeyError, IndexError, TypeError:
            text = None
        if not isinstance(text, str) or not text.strip():
            raise SystemExit(
                f"\n{self.name} returned no text for model {self.model}.\n\n"
                f"  Re-run — this is usually a one-off.\n\n  {NOTHING_LOST}\n"
            )
        return text


class GroqProvider(OpenAICompatProvider):
    """Groq's free tier, with its server-side browser_search tool."""

    name = "groq"
    label = "Groq"
    key_var = "GROQ_API_KEY"
    keys_url = GROQ_KEYS

    def __init__(self) -> None:
        self.base_url = GROQ_BASE_URL
        self.model = config.groq_model() or GROQ_DEFAULT_MODEL

    def require_credentials(self) -> None:
        if not self.api_key():
            raise SystemExit(
                f"\nNo GROQ_API_KEY found for --provider groq (checked {config.env_file()} and "
                f"the environment).\n\n  Create one at {GROQ_KEYS}, then:\n\n"
                f"    echo 'export GROQ_API_KEY=gsk_...' >> {config.env_file()}\n"
                f"    chmod 600 {config.env_file()}\n\n"
                "  Or pick another provider with --provider.\n"
            )

    def request_body(self, prompt: str, native_search: bool) -> dict:
        body = super().request_body(prompt, native_search)
        if not native_search:
            return body
        body["tools"] = [{"type": "browser_search"}]
        body["tool_choice"] = "required"
        body["reasoning_effort"] = "low"  # higher means much longer browsing
        return body


def chat_completion(provider: OpenAICompatProvider, body: dict, native_search: bool) -> dict:
    """POST to {base_url}/chat/completions and return the parsed JSON.

    Every expected failure exits with a remedy; the API key is never shown.
    """
    key = provider.api_key()
    headers = {"Content-Type": "application/json", "User-Agent": HTTP_USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # noqa: S310 - scheme checked in require_credentials
        f"{provider.base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    timeout = config.provider_timeout()
    try:
        with (
            progress.waiting_for(provider.label),
            net.HTTP_OPENER.open(request, timeout=timeout) as response,
        ):
            raw = net.read_capped(response)
    except urllib.error.HTTPError as exc:
        message = friendly_http_error(provider, exc, native_search, error_body(exc))
        raise SystemExit(f"\n{net.redact(message, key)}\n") from None
    except TimeoutError:
        raise SystemExit(timeout_message(provider, timeout)) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SystemExit(timeout_message(provider, timeout)) from None
        raise SystemExit(
            net.redact(
                f"\nCould not reach {provider.name} at {provider.base_url}: {exc.reason}\n\n"
                "  Check your network connection, then re-run.\n\n"
                f"  {NOTHING_LOST}\n",
                key,
            )
        ) from None
    except ValueError as exc:
        raise SystemExit(f"\n{provider.name} response refused: {exc}.\n") from None

    try:
        return json.loads(raw)
    except ValueError:
        raise SystemExit(
            f"\n{provider.name} returned something that is not JSON.\n\n"
            "  Check the base URL points at an OpenAI-compatible /v1 endpoint.\n"
        ) from None


def timeout_message(provider: OpenAICompatProvider, timeout: float) -> str:
    return (
        f"\n{provider.name} sent no response within {timeout:g} seconds.\n\n"
        "  Server-side search can be slow. Raise AI_POST_PROVIDER_TIMEOUT,\n"
        f"  or use --search rss so the model only writes.\n\n  {NOTHING_LOST}\n"
    )


def error_body(exc: urllib.error.HTTPError) -> str:
    """The error response's body, read once so it can be both parsed and shown."""
    try:
        return exc.read(4000).decode("utf-8", "replace")
    except OSError, AttributeError:  # no body attached
        return ""


# R54: sentences that sell a paid plan, which the $0 rule rules out anyway.
UPSELL = re.compile(
    r"need more tokens\?|\bupgrade to\b|\bupgrade your\b|\bdev tier\b|/settings/billing",
    re.IGNORECASE,
)
MAX_DETAIL = 300


def clean_detail(text: str, key: str = "", limit: int = MAX_DETAIL) -> str:
    """Provider error text fit to show and log (R54): organization IDs
    masked, upgrade offers dropped, the key redacted, then shortened at a
    sentence or word boundary. Redaction comes before shortening, so a key
    can't be cut in half and slip past it."""
    text = re.sub(r"org_[A-Za-z0-9]+", "org_…", text.strip())
    sentences = re.split(r"(?<=[.?!])\s+", text)
    text = " ".join(s for s in sentences if not UPSELL.search(s))
    text = net.redact(text, key)
    if len(text) <= limit:
        return text
    head = text[: limit - 2]  # room for the " …"
    ends = [m.end() for m in re.finditer(r"[.?!](?=\s)", head)]
    if ends and ends[-1] >= limit // 2:
        return head[: ends[-1]] + " …"
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip() + "…"


def friendly_http_error(
    provider: OpenAICompatProvider, exc: urllib.error.HTTPError, native_search: bool, body: str
) -> str:
    """Turn an HTTP error from an OpenAI-compatible API into a remedy.

    Error bodies look like {"error": {"message": ..., "type": ...}}.
    """
    try:
        detail = str(json.loads(body)["error"]["message"])
    except ValueError, KeyError, TypeError:
        detail = body
    detail = clean_detail(detail, provider.api_key()) or "(no detail)"
    code = exc.code
    name = provider.name
    where = f" Create a new one at {provider.keys_url}." if provider.keys_url else ""

    if code == 401:
        return (
            f"{name} rejected the API key ({code}).\n\n"
            f"  Check {provider.key_var} in {config.env_file()}.{where}\n"
            f"  A stale {provider.key_var} set in your shell overrides the file."
        )
    if code == 403:
        return (
            f"{name} refused the request ({code}): {detail}\n\n"
            "  The key may lack access to this model or tool. If the free plan\n"
            "  does not allow server-side search, re-run with --search rss."
        )
    if code == 404:
        return (
            f"{name} could not find the model or endpoint ({code}): {detail}\n\n"
            f"  Check the model name ({provider.model}) and base URL ({provider.base_url})."
        )
    if code == 413:  # R24: waiting won't shrink the request
        if native_search:
            remedy = (
                "  Use --search rss with a smaller AI_POST_RSS_MAX_ENTRIES (for example\n"
                "  10), so the model only writes from a short list of feed entries."
            )
        else:
            remedy = (
                "  Send fewer feed entries: lower AI_POST_RSS_MAX_ENTRIES\n"
                f"  (default {DEFAULT_RSS_MAX_ENTRIES}) or AI_POST_RSS_PER_FEED"
                f" (default {DEFAULT_RSS_PER_FEED})."
            )
        return (
            f"{name} says the request is too large for the plan's per-minute\n"
            f"  token limit ({code}): {detail}\n\n{remedy}\n\n  {NOTHING_LOST}"
        )
    if code == 429:
        retry = exc.headers.get("retry-after") if exc.headers else None
        wait = f" {name} suggests waiting {retry} seconds." if retry else ""
        hint = ""
        if native_search:  # in rss mode there is no smaller search to switch to
            hint = (
                " If one call is over the\n"
                "  tokens-per-minute limit, --search rss sends a smaller request."
            )
        return (
            f"{name} rate limit hit ({code}): {detail}\n\n"
            f"  Wait a minute and re-run.{wait}{hint}\n\n"
            f"  {NOTHING_LOST}"
        )
    if code == 498 or code >= 500:
        return (
            f"{name} returned a server error ({code}).\n\n"
            "  This is on their end, not yours. Wait a few minutes and\n"
            f"  re-run.\n\n  {NOTHING_LOST}"
        )
    return f"{name} returned an error ({code}): {detail}"


def select_provider(flag: str | None) -> str:
    """--provider, then AI_POST_PROVIDER, then the default (R1)."""
    name = (flag or config.provider_setting() or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise SystemExit(
            f"\nUnknown provider {name!r} (from AI_POST_PROVIDER). "
            f"Choose one of: {', '.join(PROVIDERS)}.\n"
        )
    return name


def make_provider(name: str):
    return {
        "anthropic": AnthropicProvider,
        "groq": GroqProvider,
        "openai-compat": OpenAICompatProvider,
    }[name]()
