"""R51: on a 429 with a short suggested wait, wait it out once and retry.
The autouse `clock` fixture makes every wait instant."""

import json

import pytest

from tests.conftest import MODEL_OUTPUT, SENTINEL_KEY, completion
from xgenerateposts import config, progress, providers

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TPM = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01fake` "
    "service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 5473, "
    "Requested 6153. Please try again in 27.195s."
)


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SENTINEL_KEY)
    return providers.GroqProvider()


def limited(message="Rate limit reached", headers=None):
    body = json.dumps({"error": {"message": message, "type": "tokens"}}).encode()
    return (body, 429, headers or {})


OK = (completion(MODEL_OUTPUT), 200, {})


def test_retry_after_then_success(web, groq, clock, capsys):
    web.add_sequence(GROQ_URL, [limited(headers={"retry-after": "7"}), OK])
    assert "A Real Release" in groq.generate("prompt", native_search=True)
    assert len(web.requests) == 2
    assert clock.slept == [8]  # 7 s plus the 1 s margin
    assert web.requests[1].data == web.requests[0].data  # the same request again
    err = capsys.readouterr().err
    assert "Groq's rate limit: retrying in 8 s (Ctrl+C to cancel)" in err
    assert "Retrying now." in err
    assert "Groq answered in" in err


def test_wait_from_the_body_names_the_limit(web, groq, clock, capsys):
    web.add_sequence(GROQ_URL, [limited(TPM), OK])
    groq.generate("prompt", native_search=True)
    assert clock.slept == [29]  # ceil(27.195 + 1)
    assert "Groq's per-minute token limit: retrying in 29 s" in capsys.readouterr().err


def test_second_429_stops_with_the_r24_message(web, groq, clock):
    web.add_sequence(GROQ_URL, [limited(headers={"retry-after": "7"})] * 3)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=True)
    assert len(web.requests) == 2  # retried once, never looped
    assert clock.slept == [8]
    assert "rate limit hit (429)" in exc.value.code
    assert "Wait a minute" in exc.value.code
    assert SENTINEL_KEY not in exc.value.code


def test_wait_over_the_limit_is_not_retried(web, groq, clock):
    web.add_sequence(GROQ_URL, [limited(headers={"retry-after": "3600"})])
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert len(web.requests) == 1
    assert clock.slept == []
    assert "Groq asked for 3600 s, longer than AI_POST_RATE_LIMIT_WAIT (60)" in exc.value.code


def test_wait_at_the_limit_is_retried(monkeypatch, web, groq, clock):
    monkeypatch.setenv("AI_POST_RATE_LIMIT_WAIT", "10")
    web.add_sequence(GROQ_URL, [limited(headers={"retry-after": "10"}), OK])
    groq.generate("prompt")
    assert clock.slept == [11]


@pytest.mark.parametrize("message", ["Rate limit reached", "Please try again in 1m30s."])
def test_no_usable_wait_is_not_retried(web, groq, clock, message):
    web.add_sequence(GROQ_URL, [limited(message)])
    with pytest.raises(SystemExit):
        groq.generate("prompt")
    assert len(web.requests) == 1
    assert clock.slept == []


def test_setting_0_turns_the_retry_off(monkeypatch, web, groq, clock):
    monkeypatch.setenv("AI_POST_RATE_LIMIT_WAIT", "0")
    web.add_sequence(GROQ_URL, [limited(TPM, {"retry-after": "5"}), OK])
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert len(web.requests) == 1
    assert clock.slept == []
    assert "didn't wait" not in exc.value.code


def test_413_is_never_retried(web, groq, clock):
    web.add_sequence(
        GROQ_URL,
        [(json.dumps({"error": {"message": TPM}}).encode(), 413, {"retry-after": "5"}), OK],
    )
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt")
    assert len(web.requests) == 1
    assert clock.slept == []
    assert "too large" in exc.value.code


@pytest.mark.parametrize("status", [500, 503])
def test_server_errors_are_never_retried(web, groq, clock, status):
    web.add_sequence(GROQ_URL, [(b"{}", status, {"retry-after": "5"}), OK])
    with pytest.raises(SystemExit):
        groq.generate("prompt")
    assert len(web.requests) == 1
    assert clock.slept == []


def test_openai_compat_retries_too(monkeypatch, web, clock, capsys):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    url = "https://llm.example/v1/chat/completions"
    web.add_sequence(url, [limited(headers={"retry-after": "2"}), OK])
    providers.OpenAICompatProvider().generate("prompt")
    assert clock.slept == [3]
    assert "openai-compat's rate limit: retrying in 3 s" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["-1", "soon", "1.5", "\N{FULLWIDTH DIGIT SIX}"])
def test_bad_rate_limit_wait_exits(monkeypatch, value):
    monkeypatch.setenv("AI_POST_RATE_LIMIT_WAIT", value)
    with pytest.raises(SystemExit) as exc:
        config.rate_limit_wait()
    assert "AI_POST_RATE_LIMIT_WAIT" in exc.value.code


def test_rate_limit_wait_defaults_to_60():
    assert config.rate_limit_wait() == 60


@pytest.mark.parametrize(
    ("headers", "body", "wait"),
    [
        ({"retry-after": "7"}, "", 7.0),
        ({"retry-after": "7"}, "try again in 27.195s", 7.0),  # the header wins
        ({}, TPM, 27.195),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, TPM, 27.195),
        ({}, "Please try again in 450ms.", None),
        ({}, "Please try again in 2h5m.", None),
        ({}, "", None),
    ],
)
def test_suggested_wait(headers, body, wait):
    assert providers.suggested_wait(headers, body) == wait


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (TPM, "per-minute token limit"),
        ("on requests per minute (RPM)", "per-minute request limit"),
        ("on tokens per day (TPD)", "daily token limit"),
        ("on requests per day (RPD)", "daily request limit"),
        ("slow down", "rate limit"),
    ],
)
def test_limit_reason(body, reason):
    assert providers.limit_reason(body) == reason


def test_ctrl_c_during_the_countdown_propagates(web, groq, clock, monkeypatch):
    """main() turns this into its usual "Cancelled." exit."""

    def interrupted(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(progress, "sleep", interrupted)
    web.add_sequence(GROQ_URL, [limited(headers={"retry-after": "7"}), OK])
    with pytest.raises(KeyboardInterrupt):
        groq.generate("prompt")
    assert len(web.requests) == 1
