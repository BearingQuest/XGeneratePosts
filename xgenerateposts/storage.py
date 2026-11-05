"""Files: the author profile, and the history, drafts and run log in the data
directory."""

import json
import os
import sys
from datetime import date, datetime

# The rule applies only below Python 3.7 and requires-python is >=3.14.
# nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2
from importlib import resources
from pathlib import Path

from xgenerateposts import config
from xgenerateposts.author_profile import parse_profile

RUN_VIA = ""  # "provider/model, search source" once main() has chosen them (R13)
RUN_TOKENS = ""  # " — tokens: …" once the provider has given counts (R53, R59)


def token_note(usage: dict[str, int] | None, estimate: int) -> str:
    """The provider's token counts and the R53 estimate of the prompt as
    sent, for the OK or FAILED line; empty without counts (R59)."""
    if not usage:
        return ""
    counts = ", ".join(f"{k} {v}" for k, v in usage.items())
    return f" — tokens: {counts}, estimate {estimate}"


def log_run(message: str) -> None:
    config.data_dir().mkdir(parents=True, exist_ok=True)
    via = f" [via {RUN_VIA}]" if RUN_VIA else ""
    with config.log_file().open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}{via}\n")


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)
    log_run(f"WARNING — {message}")


def load_profile(path: Path) -> dict:
    """Read and parse the author profile (see author_profile.parse_profile)."""
    if not path.exists():
        raise SystemExit(
            f"No author profile at {path}\n"
            "Run 'xgp init' (from a repo clone: 'python daily_post.py init') to create one in "
            "the data directory, or copy xgenerateposts/author_profile.example.md to that "
            "path. Then fill it in — it is what makes the posts sound like you."
        )
    return parse_profile(path.read_text(), path)


def create_profile() -> Path:
    """Copy the example profile to the data directory as author_profile.md,
    with mode 600 (R33). Never overwrites: raises FileExistsError if a file
    (or a symlink) is already there.

    A missing data directory is created with mode 700, since it holds the
    profile, drafts and history. An existing one keeps its mode.
    """
    example = resources.files("xgenerateposts").joinpath(config.EXAMPLE_PROFILE).read_bytes()
    data = config.data_dir()
    if not data.exists():
        data.parent.mkdir(parents=True, exist_ok=True)
        data.mkdir(mode=0o700)
        data.chmod(0o700)  # mkdir's mode is reduced by the umask
    path = config.profile_file()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(example)
    path.chmod(0o600)  # os.open's mode is reduced by the umask
    return path


def load_history() -> list:
    history_file = config.history_file()
    if history_file.exists():
        try:
            return json.loads(history_file.read_text())
        except json.JSONDecodeError:
            print(f"WARNING: {history_file} is corrupt; starting fresh.", file=sys.stderr)
    return []


def save_history(history: list) -> None:
    config.history_file().write_text(json.dumps(history, indent=2))


def log_drafts(
    item: dict,
    topic: str,
    x_post: str,
    li_post: str,
    posted_x: bool,
    posted_li: bool,
    via: str = "",
) -> None:
    config.data_dir().mkdir(parents=True, exist_ok=True)
    entry = (
        f"\n---\n\n# {date.today().isoformat()} — {item['title']}\n\n"
        f"Topic: {topic}\n"
        f"Via: {via or 'n/a'}\n"
        f"Source: {item.get('source', 'n/a')}\n"
        f"Why: {item.get('why', 'n/a')}\n\n"
        f"## X{' (POSTED)' if posted_x else ''}\n\n{x_post}\n\n"
        f"## LinkedIn{' (POSTED)' if posted_li else ''}\n\n{li_post}\n"
    )
    with config.drafts_file().open("a") as f:
        f.write(entry)
