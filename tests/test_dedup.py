"""R17: dedup by URL or normalized title."""

import pytest

from tests.conftest import MODEL_OUTPUT
from xgenerateposts import cli, dedup, search


class CannedProvider:
    name = "canned"
    model = "m"
    usage = None  # as AnthropicProvider: no token counts

    def __init__(self, text):
        self.text = text
        self.prompts = []

    def generate(self, prompt, native_search=True):
        self.prompts.append(prompt)
        return self.text


PROFILE = {"beat": "b", "voice": "v"}


def test_normalize_title_casefolds_and_collapses_whitespace():
    assert dedup.normalize_title("  A  Real\tRELEASE\n") == "a real release"


def test_title_match_with_different_url_is_rejected():
    history_titles = ["a real   release"]
    with pytest.raises(SystemExit) as exc:
        cli.generate_item(
            CannedProvider(MODEL_OUTPUT),
            search.NativeSearch(),
            PROFILE,
            "topic",
            ["https://other.example/same-story"],
            history_titles,
        )
    assert "already covered" in exc.value.code


def test_url_match_is_rejected():
    with pytest.raises(SystemExit):
        cli.generate_item(
            CannedProvider(MODEL_OUTPUT),
            search.NativeSearch(),
            PROFILE,
            "topic",
            ["https://vendor.example/post"],
            [],
        )


def test_new_item_passes_and_prompt_lists_used_urls_and_titles():
    provider = CannedProvider(MODEL_OUTPUT)
    item = cli.generate_item(
        provider, search.NativeSearch(), PROFILE, "topic", ["https://old.example/1"], ["Old Story"]
    )
    assert item["url"] == "https://vendor.example/post"
    assert "- https://old.example/1" in provider.prompts[0]
    assert "- Old Story" in provider.prompts[0]


def test_used_from_reads_history():
    history = [
        {"url": "https://a", "title": "A"},
        {"url": "https://b", "title": "B", "topic": "t"},
    ]
    assert dedup.used_from(history) == (["https://a", "https://b"], ["A", "B"])
