"""No key in exit messages, logs, drafts or output. Provider errors map to
remedies (R12)."""

import json
import os
import urllib.error
from pathlib import Path

import pytest

from tests.conftest import MODEL_OUTPUT, SENTINEL_KEY, completion
from xgenerateposts import config, net, providers

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SENTINEL_KEY)
    return providers.GroqProvider()


def error_body(message):
    return json.dumps({"error": {"message": message, "type": "invalid_request_error"}}).encode()


def test_key_is_sent_only_in_the_authorization_header(web, groq):
    web.add(GROQ_URL, completion(MODEL_OUTPUT))
    groq.generate("prompt", native_search=True)
    request = web.requests[0]
    assert request.get_header("Authorization") == f"Bearer {SENTINEL_KEY}"
    assert SENTINEL_KEY not in request.full_url
    assert SENTINEL_KEY.encode() not in request.data


def test_401_names_variable_and_redacts_echoed_key(web, groq):
    web.add(GROQ_URL, error_body(f"Invalid API Key {SENTINEL_KEY}"), status=401)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert "GROQ_API_KEY" in exc.value.code
    assert "https://console.groq.com/keys" in exc.value.code
    assert SENTINEL_KEY not in exc.value.code


def test_echoed_key_in_other_errors_is_redacted(web, groq):
    web.add(GROQ_URL, error_body(f"bad request for {SENTINEL_KEY}"), status=400)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert SENTINEL_KEY not in exc.value.code
    assert "[redacted]" in exc.value.code


def test_connection_error_is_redacted(web, groq):
    web.add(GROQ_URL, urllib.error.URLError(f"proxy said {SENTINEL_KEY}"))
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert SENTINEL_KEY not in exc.value.code
    assert "Could not reach groq" in exc.value.code


def test_429_suggests_waiting_and_rss(web, groq):
    web.add(GROQ_URL, error_body("Rate limit reached"), status=429, headers={"retry-after": "7"})
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=True)
    assert "waiting 7 seconds" in exc.value.code
    assert "--search rss" in exc.value.code


def without_nothing_lost(message):
    assert config.NOTHING_LOST in message
    return message.replace(config.NOTHING_LOST, "")


@pytest.mark.parametrize("native", [True, False])
def test_429_says_wait_with_retry_after(web, groq, native):
    """R24"""
    web.add(
        GROQ_URL,
        error_body(f"Rate limit reached for {SENTINEL_KEY}"),
        status=429,
        headers={"retry-after": "12"},
    )
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=native)
    message = exc.value.code
    assert "Wait a minute" in message
    assert "waiting 12 seconds" in message
    assert SENTINEL_KEY not in message
    without_nothing_lost(message)
    assert ("--search rss" in message) is native  # nothing to switch to in rss mode


def test_429_without_retry_after_still_says_wait(web, groq):
    web.add(GROQ_URL, error_body("Rate limit reached"), status=429)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert "Wait a minute" in exc.value.code
    assert "suggests waiting" not in exc.value.code


@pytest.mark.parametrize(
    ("native", "remedies"),
    [
        (False, ["AI_POST_RSS_MAX_ENTRIES", "AI_POST_RSS_PER_FEED"]),
        (True, ["--search rss", "AI_POST_RSS_MAX_ENTRIES"]),
    ],
)
def test_413_says_too_large_and_never_wait(web, groq, native, remedies):
    """R24: waiting can't help a request that is over the limit on its own."""
    web.add(
        GROQ_URL,
        error_body(f"Request too large for {SENTINEL_KEY}"),
        status=413,
        headers={"retry-after": "5"},
    )
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=native)
    message = exc.value.code
    assert "too large for the plan's per-minute" in message
    for remedy in remedies:
        assert remedy in message
    assert SENTINEL_KEY not in message
    rest = without_nothing_lost(message).lower()
    assert "wait" not in rest
    assert "re-run" not in rest
    if not native:
        assert "--search rss" not in rest


@pytest.mark.parametrize(
    ("status", "phrase"),
    [
        (403, "--search rss"),
        (404, "openai/gpt-oss-120b"),
        (413, "per-minute"),
        (503, "server error"),
    ],
)
def test_http_errors_map_to_remedies(web, groq, status, phrase):
    web.add(GROQ_URL, error_body("detail"), status=status)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert phrase in exc.value.code


FAKE_ORG = "org_01fakeorgid0000000000000000"
UPSELL = "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing"
# The two real 3 Nov messages, with a fake organization ID. The upgrade
# sentence's URL tail is reconstructed: the original was cut off at 300.
REAL_429 = (
    f"Rate limit reached for model `openai/gpt-oss-120b` in organization `{FAKE_ORG}` "
    "service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 5473, "
    f"Requested 6153. Please try again in 27.195s. {UPSELL}"
)
REAL_413 = (
    f"Request too large for model `openai/gpt-oss-120b` in organization `{FAKE_ORG}` "
    "service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8344, "
    f"please reduce your message size and try again. {UPSELL}"
)


@pytest.mark.parametrize(
    ("status", "message", "figures"),
    [
        (429, REAL_429, ["Limit 8000", "Used 5473", "Requested 6153", "27.195s"]),
        (413, REAL_413, ["Limit 8000", "Requested 8344"]),
    ],
)
def test_real_groq_errors_lose_org_id_and_upsell(monkeypatch, web, groq, status, message, figures):
    """R54"""
    monkeypatch.setenv("AI_POST_RATE_LIMIT_WAIT", "0")
    web.add(GROQ_URL, error_body(message), status=status)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=True)
    shown = exc.value.code
    assert FAKE_ORG not in shown
    assert "org_…" in shown
    assert "Need more tokens" not in shown
    assert "Upgrade" not in shown
    assert "console.groq.com/settings/billing" not in shown
    for figure in figures:
        assert figure in shown
    detail = providers.clean_detail(message)
    assert detail.endswith(".")
    assert len(detail) <= providers.MAX_DETAIL


def test_long_detail_is_cut_at_a_word_boundary_not_mid_url():
    """R54"""
    text = "x" * 200 + " see https://status.example/" + "a" * 80 + " for details and more words"
    detail = providers.clean_detail(text)
    assert len(detail) <= providers.MAX_DETAIL
    assert detail.endswith("…")
    assert "https://status.example" not in detail  # the URL is dropped whole, not cut


def test_long_detail_prefers_a_sentence_boundary():
    text = "First sentence is long enough. " * 6 + "Second part " * 20
    detail = providers.clean_detail(text)
    assert len(detail) <= providers.MAX_DETAIL
    assert detail.endswith("enough. …")


def test_key_straddling_the_cut_is_still_redacted(monkeypatch, web, groq):
    """R54: redaction runs before shortening, so no fragment of the key survives."""
    message = "y" * (providers.MAX_DETAIL - 10) + SENTINEL_KEY + " trailing"
    web.add(GROQ_URL, error_body(message), status=400)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert SENTINEL_KEY[:10] not in exc.value.code


def test_response_without_text_exits(web, groq):
    web.add(GROQ_URL, {"choices": []})
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert "returned no text" in exc.value.code


def test_non_json_response_exits(web, groq):
    web.add(GROQ_URL, b"<html>login</html>")
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert "not JSON" in exc.value.code


def test_provider_response_over_2_mb_is_refused(web, groq):
    """R23: feeds get 10 MB, provider responses keep the 2 MB cap."""
    web.add(GROQ_URL, b" " * (config.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert "larger than 2,000,000 bytes" in exc.value.code


def test_env_file_path_is_read_at_call_time(monkeypatch, tmp_path):
    """The AI_POST_ENV_FILE in force at call time is read, never the real
    ~/.ai_post_env."""
    var = "XGP_TEST_ENV_FILE_VALUE"
    monkeypatch.setenv(var, "placeholder")
    monkeypatch.delenv(var)  # restored to unset after the test
    env_file = tmp_path / "test.env"
    env_file.write_text(f"export {var}='from the patched file'\n")
    monkeypatch.setenv("AI_POST_ENV_FILE", str(env_file))
    read = []
    real_read_text = Path.read_text

    def spy(self, *args, **kwargs):
        read.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    config.load_env_file()
    assert os.environ[var] == "from the patched file"
    assert read == [env_file]
    assert Path.home() / ".ai_post_env" not in read


def test_redact_ignores_empty_and_short_values():
    assert net.redact("abc", "", "ab") == "abc"
