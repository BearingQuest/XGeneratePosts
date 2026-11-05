"""R58 to R61: why a reply had no text, the token estimate on run.log lines,
and low reasoning effort on groq rss requests."""

from xgenerateposts import providers


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
