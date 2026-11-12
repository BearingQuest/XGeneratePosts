"""Configuration: constants, and every environment variable the tool reads.

Environment variables are read at call time, never at import, so tests can
set them per test and the env file can be loaded first.
"""

import math
import os
from pathlib import Path

MODEL = "claude-sonnet-4-6"

CONSOLE_BILLING = "https://console.anthropic.com/settings/billing"
CONSOLE_KEYS = "https://console.anthropic.com/settings/keys"

PROVIDERS = ("anthropic", "groq", "openai-compat")
DEFAULT_PROVIDER = "groq"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"  # groq/compound was shut down 21 Sep 2026
GROQ_KEYS = "https://console.groq.com/keys"

# Server-side search can take a minute or more, so provider calls get a long
# timeout. Applies to groq and openai-compat; the anthropic SDK keeps its own.
DEFAULT_PROVIDER_TIMEOUT = 120.0  # seconds, override with AI_POST_PROVIDER_TIMEOUT

# R51: on a 429, wait the provider's suggested time plus a margin and retry
# once, if the suggestion is at most AI_POST_RATE_LIMIT_WAIT (0 turns it off).
DEFAULT_RATE_LIMIT_WAIT = 60  # seconds
RATE_LIMIT_MARGIN = 1.0  # seconds added to the provider's suggestion

HTTP_USER_AGENT = "XGeneratePosts/3 (+https://github.com/BearingQuest/questcast)"
MAX_RESPONSE_BYTES = 2_000_000  # cap on any one provider response we read
MAX_FEED_BYTES = 10_000_000  # feeds get their own cap; some run past 2 MB (R23)

# Where the item comes from: the provider's own server-side search, or RSS
# feeds listed in the profile's '## Feeds' section.
SEARCH_SOURCES = ("native", "rss")
NATIVE_SEARCH_PROVIDERS = ("anthropic", "groq")  # default to native; others to rss
FEED_TIMEOUT = 15.0  # seconds per feed request (R20)
LINKEDIN_TIMEOUT = 30.0  # seconds for the one --post-linkedin request (R56)
DEFAULT_RSS_PER_FEED = 3  # newest entries per feed, override with AI_POST_RSS_PER_FEED
# Total candidates sent, best topic match first, override with
# AI_POST_RSS_MAX_ENTRIES. Keeps one call under Groq's free 8,000 TPM (R22).
DEFAULT_RSS_MAX_ENTRIES = 20
# R53: with --search rss, the lowest-ranked candidates are dropped until the
# whole prompt's estimated size is within AI_POST_TOKEN_BUDGET. 7,000 leaves
# 1,000 of Groq's free 8,000 tokens per minute for the chat template and for
# text that tokenizes denser than the estimate. The arithmetic is in
# docs/decisions.md.
DEFAULT_TOKEN_BUDGET = 7000
# UTF-8 bytes per token for the estimate. English prose runs nearer 4.2 with
# GPT-style tokenizers; on the 3 Nov rss request, bytes / 4 came to 8,811
# against Groq's reported 8,344.
BYTES_PER_TOKEN = 4
# The prompt lists only the newest used URLs and titles. Each history entry
# adds about 43 estimated tokens to every prompt, so at 60 an rss prompt
# would pass the budget on its own after about 24 more runs. Dedup is not
# weakened: is_used checks a pick against the whole history, and rss
# candidates are filtered against it before they are sent (R53).
PROMPT_USED_LIMIT = 20

MAX_TWEET_TEXT = 255  # 280 minus ~24 chars X reserves for a t.co link
MAX_LINKEDIN_TEXT = 1100  # hard ceiling; the prompt targets 750-950

# Shipped inside the package; xgp init copies it to the data directory (R33).
EXAMPLE_PROFILE = "author_profile.example.md"

NOTHING_LOST = "Nothing was saved, so today's topic is still available when you re-run."

# The env file can't name its own location, so AI_POST_ENV_FILE comes only
# from the process environment. Everything else may be set in the file.
PROCESS_ONLY = ("AI_POST_ENV_FILE",)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def env_file() -> Path:
    return Path(os.environ.get("AI_POST_ENV_FILE", Path.home() / ".ai_post_env"))


def data_dir() -> Path:
    return Path(os.environ.get("AI_POST_DATA_DIR", Path.home() / "XPosts"))


def profile_file() -> Path:
    return data_dir() / "author_profile.md"  # your voice — never in the repo


def history_file() -> Path:
    return data_dir() / "history.json"  # URLs already used (dedup)


def drafts_file() -> Path:
    return data_dir() / "drafts.md"  # running log of drafts


def log_file() -> Path:
    return data_dir() / "run.log"  # one line per run


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=value lines (with or without a leading 'export ') into
    os.environ. Variables already set in the environment win. The env file's
    path is read at call time, so tests can point it at a throwaway file."""
    path = env_file() if path is None else path
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and key not in os.environ and key not in PROCESS_ONLY:
            os.environ[key] = value.strip().strip("'\"")


# ---------------------------------------------------------------------------
# Provider and search selection
# ---------------------------------------------------------------------------


def provider_setting() -> str:
    return os.environ.get("AI_POST_PROVIDER", "")


def search_setting() -> str:
    return os.environ.get("AI_POST_SEARCH", "")


def provider_timeout() -> float:
    """Seconds to wait for a groq or openai-compat response (R20)."""
    raw = os.environ.get("AI_POST_PROVIDER_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_PROVIDER_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(
            f"\nAI_POST_PROVIDER_TIMEOUT must be a positive number of seconds; got {raw!r}.\n"
            f"  Remove it to use the default of {DEFAULT_PROVIDER_TIMEOUT:g} seconds.\n"
        )
    return value


def rate_limit_wait() -> int:
    """Longest suggested wait, in seconds, that a 429 is retried after (R51)."""
    raw = os.environ.get("AI_POST_RATE_LIMIT_WAIT", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_WAIT
    if not (raw.isascii() and raw.isdigit()):
        raise SystemExit(
            "\nAI_POST_RATE_LIMIT_WAIT must be a whole number of seconds, 0 or more\n"
            f"  (0 turns the retry off); got {raw!r}.\n"
        )
    return int(raw)


def env_whole_number(var: str, default: int) -> int:
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise SystemExit(f"\n{var} must be a whole number of 1 or more; got {raw!r}.\n")
    return int(raw)


def rss_per_feed() -> int:
    return env_whole_number("AI_POST_RSS_PER_FEED", DEFAULT_RSS_PER_FEED)


def rss_max_entries() -> int:
    return env_whole_number("AI_POST_RSS_MAX_ENTRIES", DEFAULT_RSS_MAX_ENTRIES)


def token_budget() -> int:
    return env_whole_number("AI_POST_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET)


# ---------------------------------------------------------------------------
# Provider settings and credentials
# ---------------------------------------------------------------------------


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", "").strip()


def openai_compat_base_url() -> str:
    return os.environ.get("OPENAI_COMPAT_BASE_URL", "").strip().rstrip("/")


def openai_compat_model() -> str:
    return os.environ.get("OPENAI_COMPAT_MODEL", "").strip()


def api_key(var: str) -> str:
    """GROQ_API_KEY or OPENAI_COMPAT_API_KEY, whichever the provider names."""
    return os.environ.get(var, "").strip()


# ---------------------------------------------------------------------------
# Publishing credentials
# ---------------------------------------------------------------------------


def x_credentials() -> dict:
    return {
        k: os.environ.get(k)
        for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    }


def linkedin_access_token() -> str | None:
    return os.environ.get("LINKEDIN_ACCESS_TOKEN")


def linkedin_author_urn() -> str | None:
    return os.environ.get("LINKEDIN_AUTHOR_URN")


def linkedin_api_version() -> str:
    return os.environ.get("LINKEDIN_API_VERSION", "202601")
