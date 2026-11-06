"""R45: .claude/settings.json keeps every deny and ask rule, and allows nothing.

The mode lock (5 Nov 2026) adds the option-first forms, the review, comment and
publish gates, and disables auto and bypass-permissions modes.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
SCHEMA = "https://json.schemastore.org/claude-code-settings.json"

MODE_LOCKS = ["disableAutoMode", "disableBypassPermissionsMode"]

REQUIRED_DENY = [
    "Bash(gh pr merge *)",
    "Bash(gh * pr merge)",
    "Bash(gh * pr merge *)",
    "Bash(gh pr review *)",
    "Bash(gh * pr review)",
    "Bash(gh * pr review *)",
    "Bash(gh pr comment *)",
    "Bash(gh * pr comment)",
    "Bash(gh * pr comment *)",
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(git push * --force*)",
    "Bash(git push * -f*)",
    "Bash(git push * +*)",
    "Bash(git * push --force*)",
    "Bash(git * push -f*)",
    "Bash(git * push * --force*)",
    "Bash(git * push * -f*)",
    "Bash(git * push * +*)",
    "Bash(git commit --amend*)",
    "Bash(git commit * --amend*)",
    "Bash(git * commit --amend*)",
    "Bash(git * commit * --amend*)",
    "Bash(git rebase *)",
    "Bash(git * rebase)",
    "Bash(git * rebase *)",
    "Bash(git pull --rebase*)",
    "Bash(git pull * --rebase*)",
    "Bash(git * pull --rebase*)",
    "Bash(git * pull * --rebase*)",
    "Bash(git pull -r*)",
    "Bash(git pull * -r*)",
    "Bash(git * pull -r*)",
    "Bash(git * pull * -r*)",
    "Bash(git tag *)",
    "Bash(git * tag)",
    "Bash(git * tag *)",
    "Bash(*xgp --post*)",
    "Bash(*xgp * --post*)",
    "Bash(*daily_post.py --post*)",
    "Bash(*daily_post.py * --post*)",
    "Bash(rm -rf *)",
    "Bash(rm -fr *)",
    "Bash(rm -Rf *)",
    "Bash(rm -fR *)",
    "Edit(/tests/acceptance/**)",
    "Edit(/.claude/settings.json)",
    "Read(~/.ai_post_env)",
]

REQUIRED_ASK = [
    "Bash(git push *)",
    "Bash(git * push)",
    "Bash(git * push *)",
    "Bash(gh pr *)",
    "Bash(gh * pr)",
    "Bash(gh * pr *)",
    "Bash(gh api *)",
    "Bash(gh * api)",
    "Bash(gh * api *)",
    "Bash(python3 *)",
    "Bash(*.ai_post_env*)",
]


@pytest.fixture(scope="module")
def settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_settings_declares_schema(settings):
    assert settings["$schema"] == SCHEMA


@pytest.mark.parametrize("key", MODE_LOCKS)
def test_mode_disabled(settings, key):
    assert settings["permissions"][key] == "disable"


@pytest.mark.parametrize("rule", REQUIRED_DENY)
def test_deny_rule_present(settings, rule):
    assert rule in settings["permissions"]["deny"]


@pytest.mark.parametrize("rule", REQUIRED_ASK)
def test_ask_rule_present(settings, rule):
    assert rule in settings["permissions"]["ask"]


def test_no_allow_rules(settings):
    assert "allow" not in settings
    assert "allow" not in settings["permissions"]
