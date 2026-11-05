"""R58 to R61: why a reply had no text, the token estimate on run.log lines,
and low reasoning effort on groq rss requests."""

import contextlib
import json
import re
import sys

import pytest

from tests.conftest import MODEL_OUTPUT, SENTINEL_KEY, completion
from tests.test_rss import FEED_A, FEED_B, dated, rss
from xgenerateposts import cli, config, prompt, providers


def test_groq_rss_body_sends_low_reasoning_effort_for_gpt_oss():
    body = providers.GroqProvider().request_body("p", native_search=False)
    assert body["reasoning_effort"] == "low"
    assert "tools" not in body


def test_groq_rss_body_leaves_it_out_for_another_model(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    body = providers.GroqProvider().request_body("p", native_search=False)
    assert "reasoning_effort" not in body


def test_groq_native_body_is_unchanged(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    body = providers.GroqProvider().request_body("p", native_search=True)
    assert body["reasoning_effort"] == "low"
    assert body["tools"] == [{"type": "browser_search"}]
    assert body["tool_choice"] == "required"


def test_openai_compat_body_never_sends_it(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "openai/gpt-oss-120b")
    body = providers.OpenAICompatProvider().request_body("p", native_search=False)
    assert body == {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "p"}]}


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
REASONING = "REASONING-MARKER the model's private thinking " + SENTINEL_KEY
ARGS = json.dumps({"query": "ARGS-MARKER", "key": SENTINEL_KEY})


def call(name):
    return {"id": "c1", "type": "function", "function": {"name": name, "arguments": ARGS}}


def reply(finish="stop", **message):
    return {"choices": [{"index": 0, "finish_reason": finish, "message": message}]}


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SENTINEL_KEY)
    return providers.GroqProvider()


def assert_clean(text):
    for marker in ("REASONING-MARKER", "private thinking", "ARGS-MARKER", SENTINEL_KEY):
        assert marker not in text
    assert "one-off" not in text


@pytest.mark.parametrize(
    ("payload", "facts"),
    [
        (reply(content=""), 'finish_reason "stop"; no reasoning; no tool calls'),
        (
            reply("length", content=None, reasoning=REASONING),
            f'finish_reason "length"; reasoning {len(REASONING)} characters; no tool calls',
        ),
        (
            reply("tool_calls", content=None, tool_calls=[call("browser.search"), call("open")]),
            'finish_reason "tool_calls"; no reasoning; 2 tool calls: browser.search, open',
        ),
        (
            {"choices": [{"message": {"content": " ", "reasoning": REASONING}}]},
            f"finish_reason none; reasoning {len(REASONING)} characters; no tool calls",
        ),
        ({"choices": []}, "no choices in the reply"),
        ({}, "no choices in the reply"),
        (json.dumps([1, 2]).encode(), "no choices in the reply"),
        ({"choices": ["text"]}, "no choices in the reply"),
        ({"choices": [{"finish_reason": "stop"}]}, 'finish_reason "stop"; no message'),
        (
            {"choices": [{"finish_reason": "stop", "message": "hi"}]},
            'finish_reason "stop"; no message',
        ),
        (
            reply(tool_calls={"function": {"name": "x"}}),
            'finish_reason "stop"; no reasoning; no tool calls',
        ),
        (
            reply(tool_calls=[{"id": "c1"}, {"function": {}}, {"function": {"name": 7}}]),
            'finish_reason "stop"; no reasoning; 3 tool calls: ?, ?, ?',
        ),
        (
            reply(tool_calls=["x", call(""), call("a"), call("b"), call("c")]),
            'finish_reason "stop"; no reasoning; 5 tool calls: ?, ?, a +2 more',
        ),
        (reply(reasoning=42), 'finish_reason "stop"; no reasoning; no tool calls'),
        (reply(reasoning=""), 'finish_reason "stop"; no reasoning; no tool calls'),
        (reply(3, content=""), "finish_reason none; no reasoning; no tool calls"),
        (reply("\r\n"), "finish_reason none; no reasoning; no tool calls"),
        (
            reply("len\ngth\r", tool_calls=[call("browser\r\nsearch\u2028x\x85y")]),
            'finish_reason "len gth"; no reasoning; 1 tool call: browser search x y',
        ),
        (
            reply(tool_calls=[call(SENTINEL_KEY), call("z" * 80)]),
            'finish_reason "stop"; no reasoning; 2 tool calls: [redacted], ' + "z" * 38 + "…",
        ),
    ],
)
def test_empty_reply_says_what_the_reply_held(web, groq, payload, facts):
    web.add(GROQ_URL, payload)
    with pytest.raises(SystemExit) as exc:
        groq.generate("prompt", native_search=False)
    message = exc.value.code
    first = message.strip().splitlines()[0]
    assert first == f"groq returned no text for model openai/gpt-oss-120b ({facts})."
    assert "Re-run. If it happens again, send the FAILED line from" in message
    assert_clean(message)


def test_empty_reply_facts_never_raises():
    for payload in (None, "x", 3, {"choices": None}, {"choices": [None]}, {"choices": [[]]}):
        assert providers.empty_reply_facts(payload) == "no choices in the reply"


def test_openai_compat_empty_reply_has_the_same_format(monkeypatch, web):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m1")
    web.add("https://llm.example/v1/chat/completions", reply("length", content=""))
    with pytest.raises(SystemExit) as exc:
        providers.OpenAICompatProvider().generate("prompt")
    assert exc.value.code.strip().splitlines()[0] == (
        'openai-compat returned no text for model m1 (finish_reason "length"; '
        "no reasoning; no tool calls)."
    )


@pytest.fixture
def run_groq(monkeypatch, web, profile_file):
    """cli.main() with groq in the given search mode; returns the new run.log lines."""
    web.add(FEED_A, rss(*[(f"A{n}", f"https://a.example/{n}", dated(n)) for n in range(3)]))
    web.add(FEED_B, b"x", status=503)
    monkeypatch.setenv("GROQ_API_KEY", SENTINEL_KEY)

    def run(search, payload, status=200):
        web.add(GROQ_URL, payload, status=status)
        argv = ["daily_post.py", "--search", search, "--topic", "Cloud"]
        monkeypatch.setattr(sys, "argv", [*argv, "--profile", str(profile_file)])
        log = config.log_file()
        before = log.read_text().splitlines() if log.exists() else []
        with contextlib.suppress(SystemExit):
            cli.main()
        return log.read_text().splitlines()[len(before) :]

    return run


@pytest.mark.parametrize("search", ["rss", "native"])
def test_empty_reply_failed_line_is_one_line_without_reply_text(run_groq, capsys, search):
    payload = reply(
        "len\ngth",
        content=None,
        reasoning=REASONING,
        tool_calls=[call("browser\nsearch"), call("x\r\n2026-11-05T09:00:00 OK — forged")],
    )
    added = run_groq(search, payload)
    assert all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d (WARNING|FAILED) — ", x) for x in added)
    lines = [line for line in added if " FAILED — " in line]
    assert len(lines) == 1
    assert (
        f'(finish_reason "len gth"; reasoning {len(REASONING)} characters; '
        "2 tool calls: browser search, x 2026-11-05T09:00:00 OK — forged)." in lines[0]
    )
    assert f"[via groq/openai/gpt-oss-120b, search {search}]" in lines[0]
    err = capsys.readouterr().err
    assert_clean(err + config.log_file().read_text())


USAGE = {"prompt_tokens": 6412, "completion_tokens": 1750, "total_tokens": 8162}


def sent(web):
    return json.loads(web.requests[-1].data)["messages"][0]["content"]


def with_usage(payload):
    return {**payload, "usage": USAGE}


@pytest.mark.parametrize("search", ["rss", "native"])
def test_ok_line_has_the_estimate_beside_the_counts(run_groq, web, search):
    added = run_groq(search, with_usage(completion(MODEL_OUTPUT)))
    estimate = prompt.estimate_tokens(sent(web))
    ok = [line for line in added if " OK — " in line]
    assert len(ok) == 1
    assert ok[0].endswith(
        "(https://vendor.example/post) — tokens: prompt 6412, completion 1750, total 8162, "
        f"estimate {estimate} [via groq/openai/gpt-oss-120b, search {search}]"
    )


@pytest.mark.parametrize("search", ["rss", "native"])
def test_empty_reply_failed_line_has_the_counts_and_estimate(run_groq, web, search):
    added = run_groq(search, with_usage(reply("length", content="", reasoning=REASONING)))
    estimate = prompt.estimate_tokens(sent(web))
    failed = [line for line in added if " FAILED — " in line]
    assert len(failed) == 1
    assert failed[0].endswith(
        f'(finish_reason "length"; reasoning {len(REASONING)} characters; no tool calls). '
        f"— tokens: prompt 6412, completion 1750, total 8162, estimate {estimate} "
        f"[via groq/openai/gpt-oss-120b, search {search}]"
    )


def test_failure_after_a_reply_keeps_the_counts(run_groq, web):
    added = run_groq("rss", with_usage(completion("no fields here")))
    failed = [line for line in added if " FAILED — " in line]
    assert len(failed) == 1
    assert "could not be parsed" in failed[0]
    estimate = prompt.estimate_tokens(sent(web))
    assert (
        f"— tokens: prompt 6412, completion 1750, total 8162, estimate {estimate} [via"
        in (failed[0])
    )


def test_http_error_failed_line_has_no_tokens(run_groq, web):
    added = run_groq("rss", with_usage({"error": {"message": "boom"}}), status=500)
    failed = [line for line in added if " FAILED — " in line]
    assert len(failed) == 1
    assert "server error (500)" in failed[0]
    assert "tokens:" not in failed[0]


def test_ok_line_without_counts_has_no_estimate(run_groq):
    added = run_groq("native", completion(MODEL_OUTPUT))
    ok = [line for line in added if " OK — " in line]
    assert len(ok) == 1
    assert "tokens:" not in ok[0]
    assert "estimate" not in ok[0]
