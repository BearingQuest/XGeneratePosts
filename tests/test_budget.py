"""R53: a token budget for --search rss, and the provider's usage figures
on the run.log OK line."""

import json
import sys

import pytest

from tests.conftest import MODEL_OUTPUT, SENTINEL_KEY, completion
from tests.test_rss import FEED_A, FEED_B, PROFILE, dated, rss
from xgenerateposts import cli, config, dedup, feeds, prompt, providers, search

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
FULL_PROFILE = {"beat": "Practitioners.", "voice": "Plain.", **PROFILE}


def test_estimate_is_utf8_bytes_over_4_rounded_up():
    assert prompt.estimate_tokens("") == 0
    assert prompt.estimate_tokens("abcd") == 1
    assert prompt.estimate_tokens("abcde") == 2
    assert prompt.estimate_tokens("—") == 1  # 3 bytes
    assert prompt.estimate_tokens("— — —") == 3  # 11 bytes, counted above its 5 characters


def test_prompt_lists_only_the_20_newest_used_urls_and_titles():
    history = [
        {
            "date": "2026-10-01",
            "url": f"https://used.example/{n:02d}",
            "title": f"Used title {n:02d}",
        }
        for n in range(30)
    ]  # oldest first, as history.json is kept
    used_urls, used_titles = dedup.used_from(history)
    text = prompt.build_prompt(FULL_PROFILE, "Cloud", used_urls, used_titles)
    assert config.PROMPT_USED_LIMIT == 20
    for n in range(10, 30):
        assert f"- https://used.example/{n:02d}\n" in text
        assert f"- Used title {n:02d}\n" in text
    for n in range(10):
        assert f"https://used.example/{n:02d}" not in text
        assert f"Used title {n:02d}" not in text
    # Dedup still uses the whole history after the answer.
    assert dedup.is_used("https://used.example/00", None, used_urls, used_titles)


def test_default_budget_is_7000():
    assert config.token_budget() == 7000


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_bad_budget_exits(monkeypatch, value):
    monkeypatch.setenv("AI_POST_TOKEN_BUDGET", value)
    with pytest.raises(SystemExit) as exc:
        config.token_budget()
    assert "AI_POST_TOKEN_BUDGET" in exc.value.code


@pytest.fixture
def five(web):
    """Five candidates from feed A, ranked newest first: A14 … A10."""
    web.add(FEED_A, rss(*[(f"A{n}", f"https://a.example/{n}", dated(n)) for n in range(10, 15)]))
    web.add(FEED_B, b"x", status=503)


def candidates(monkeypatch, per_feed="5"):
    monkeypatch.setenv("AI_POST_RSS_PER_FEED", per_feed)
    rss_search = search.RssSearch(FULL_PROFILE)
    rss_search.candidates([], [], "")
    return rss_search


def build_for(rss_search):
    return lambda block: prompt.build_prompt(rss_search.profile, "Cloud", [], [], block)


def test_under_budget_nothing_is_dropped(monkeypatch, five):
    rss_search = candidates(monkeypatch)
    block = rss_search.fit_budget(build_for(rss_search))
    assert block == feeds.format_candidates(rss_search.picked)
    assert len(rss_search.sent) == 5


def test_lowest_ranked_are_dropped_first_and_links_match(monkeypatch, five):
    rss_search = candidates(monkeypatch)
    build = build_for(rss_search)
    # A budget that fits the prompt with exactly three of the five entries.
    three = prompt.estimate_tokens(build(feeds.format_candidates(rss_search.picked[:3])))
    monkeypatch.setenv("AI_POST_TOKEN_BUDGET", str(three))
    block = rss_search.fit_budget(build)
    assert [e["title"] for e in rss_search.sent] == ["A14", "A13", "A12"]
    assert rss_search.links == {
        "https://a.example/14",
        "https://a.example/13",
        "https://a.example/12",
    }
    assert "A11" not in block
    assert prompt.estimate_tokens(build(block)) <= three


def test_over_budget_with_no_entries_names_the_profile_size(monkeypatch, five):
    rss_search = candidates(monkeypatch)
    monkeypatch.setenv("AI_POST_TOKEN_BUDGET", "100")
    with pytest.raises(SystemExit) as exc:
        rss_search.fit_budget(build_for(rss_search))
    message = exc.value.code
    size = len("Practitioners.") + len("Plain.")
    assert f"come to {size:,} characters" in message
    assert "AI_POST_TOKEN_BUDGET (100)" in message
    assert config.NOTHING_LOST in message


def test_count_cap_still_applies_before_the_budget(monkeypatch, five):
    monkeypatch.setenv("AI_POST_RSS_MAX_ENTRIES", "2")
    rss_search = candidates(monkeypatch)
    rss_search.fit_budget(build_for(rss_search))
    assert [e["title"] for e in rss_search.picked] == ["A14", "A13"]
    assert len(rss_search.sent) == 2


@pytest.fixture
def rss_run(monkeypatch, web, profile_file):
    web.add(
        "https://feeds.example/a.xml",
        rss(*[(f"A{n}", f"https://a.example/{n}", dated(n)) for n in range(10, 15)]),
    )
    web.add("https://feeds.example/b.xml", b"x", status=503)
    monkeypatch.setenv("GROQ_API_KEY", SENTINEL_KEY)
    monkeypatch.setenv("AI_POST_RSS_PER_FEED", "5")
    monkeypatch.setattr(
        sys,
        "argv",
        ["daily_post.py", "--search", "rss", "--topic", "Cloud", "--profile", str(profile_file)],
    )
    return web


def sent_prompt(web):
    return json.loads(web.requests[-1].data)["messages"][0]["content"]


def test_run_says_how_many_entries_were_sent(monkeypatch, rss_run, isolated, capsys):
    rss_run.add(GROQ_URL, completion(MODEL_OUTPUT))
    monkeypatch.setenv("AI_POST_TOKEN_BUDGET", "1000000")
    cli.run()
    assert "feed entries to fit" not in capsys.readouterr().err
    assert sent_prompt(rss_run).count("Title: ") == 5

    budget = prompt.estimate_tokens(sent_prompt(rss_run)) - 1  # one entry too many
    (isolated / "history.json").unlink()  # so the same pick isn't refused as used
    monkeypatch.setenv("AI_POST_TOKEN_BUDGET", str(budget))
    cli.run()
    assert "Sending 4 of 5 feed entries to fit the token budget" in capsys.readouterr().err
    assert sent_prompt(rss_run).count("Title: ") == 4
    assert prompt.estimate_tokens(sent_prompt(rss_run)) <= budget


def test_ok_line_records_usage_when_the_provider_returns_it(rss_run, isolated):
    payload = completion(MODEL_OUTPUT)
    payload["usage"] = {"prompt_tokens": 5723, "completion_tokens": 812, "total_tokens": 6535}
    rss_run.add(GROQ_URL, payload)
    cli.run()
    ok = [line for line in (isolated / "run.log").read_text().splitlines() if " OK — " in line]
    assert ok[-1].count("tokens: prompt 5723, completion 812, total 6535 [via groq/") == 1


def test_ok_line_is_unchanged_without_usage(rss_run, isolated):
    rss_run.add(GROQ_URL, completion(MODEL_OUTPUT))
    cli.run()
    log = (isolated / "run.log").read_text()
    assert "tokens:" not in log
    assert "(https://vendor.example/post) [via groq/" in log


@pytest.mark.parametrize(
    ("payload", "usage"),
    [
        (
            {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
            {"prompt": 1, "completion": 2, "total": 3},
        ),
        ({"usage": {"prompt_tokens": 1, "total_tokens": "3"}}, {"prompt": 1}),
        ({"usage": {"prompt_tokens": True}}, None),
        ({"usage": "lots"}, None),
        ({}, None),
    ],
)
def test_token_usage(payload, usage):
    assert providers.token_usage(payload) == usage
