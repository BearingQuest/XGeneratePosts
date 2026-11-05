"""Command-line interface: the flags, one run, and the entry point."""

import argparse
import random
import sys
from datetime import date
from pathlib import Path

from xgenerateposts import config, storage
from xgenerateposts.author_profile import match_topic, topic_menu
from xgenerateposts.config import (
    DEFAULT_PROVIDER,
    MAX_LINKEDIN_TEXT,
    MAX_TWEET_TEXT,
    NOTHING_LOST,
    PROVIDERS,
    SEARCH_SOURCES,
)
from xgenerateposts.dedup import is_used, used_from
from xgenerateposts.fields import compose_posts, parse_fields, trim
from xgenerateposts.prompt import build_prompt, estimate_tokens
from xgenerateposts.providers import make_provider, select_provider
from xgenerateposts.publish import post_to_linkedin, post_to_x
from xgenerateposts.search import make_search, select_search
from xgenerateposts.storage import load_history, load_profile, log_drafts, log_run, save_history


def choose_topic(topics: list, requested: str | None = None, pick_random: bool = False) -> str:
    """Resolve the topic from --topic, --random-topic, or an interactive menu.

    --topic accepts a menu number, a case-insensitive substring of a menu
    entry, or free text that is not in the menu at all.
    """
    if pick_random:
        return random.choice(topics)  # noqa: S311 - random topic choice, not security

    if requested:
        return match_topic(topics, requested)

    if not sys.stdin.isatty():
        raise SystemExit(
            'No topic given and no terminal to ask on. Use --topic "..." or --random-topic.'
        )

    print("\nPick a topic for today:\n")
    for i, topic in enumerate(topics, 1):
        print(f"  {i:>2}. {topic}")
    print("\n   r. Random")
    print("   Or type any topic of your own.\n")

    answer = input("Topic: ").strip()
    if not answer:
        raise SystemExit("No topic chosen.")
    if answer.lower() == "r":
        return random.choice(topics)  # noqa: S311 - random topic choice, not security
    if answer.isdigit() and 1 <= int(answer) <= len(topics):
        return topics[int(answer) - 1]
    return answer


def generate_item(
    provider, search, profile: dict, topic: str, used_urls: list, used_titles: list
) -> dict:
    candidates = search.candidates(used_urls, used_titles, topic)
    if candidates is not None:
        candidates = search.fit_budget(
            lambda block: build_prompt(profile, topic, used_urls, used_titles, block)
        )
        if len(search.sent) < len(search.picked):
            print(
                f"Sending {len(search.sent)} of {len(search.picked)} feed entries "
                "to fit the token budget",
                file=sys.stderr,
            )
    prompt = build_prompt(profile, topic, used_urls, used_titles, candidates)
    try:
        text = provider.generate(prompt, native_search=candidates is None)
    finally:
        # Set even when generate fails, so the FAILED line gets the counts.
        # In native mode the estimate is of the prompt before search (R59).
        storage.RUN_TOKENS = storage.token_note(provider.usage, estimate_tokens(prompt))

    try:
        item = parse_fields(text)
    except ValueError as exc:
        raise SystemExit(
            f"\nThe model's response could not be parsed.\n\n  {exc}\n\n"
            f"  This is usually a one-off. Re-run and it will normally\n"
            f"  succeed.\n\n  {NOTHING_LOST}\n"
        ) from None

    missing = [k for k in ("url", "title", "tweet", "linkedin") if not item.get(k)]
    if missing:
        raise SystemExit(
            f"\nThe model's response was missing: {', '.join(missing)}.\n\n"
            f"  Re-run — this is usually a one-off.\n\n  {NOTHING_LOST}\n"
        )

    if is_used(item["url"], item["title"], used_urls, used_titles):
        raise SystemExit(
            f"\nThe model picked an item already covered:\n  {item['title']}\n  {item['url']}\n\n"
            "  Re-run, or try a different topic — this one may be quiet this\n"
            f"  week.\n\n  {NOTHING_LOST}\n"
        )

    if candidates is not None and item["url"] not in search.links:
        print(
            f"WARNING: the picked URL is not one of the feed entries — check it "
            f"before posting:\n  {item['url']}",
            file=sys.stderr,
        )

    # X gets hard-trimmed (the platform enforces the limit anyway). LinkedIn is
    # only flagged — trimming from the end would eat the closing question and
    # hashtags, which is worse than a draft you shorten by hand.
    item["tweet"] = trim(item["tweet"], MAX_TWEET_TEXT)
    item["linkedin"] = item["linkedin"].strip()
    if len(item["linkedin"]) > MAX_LINKEDIN_TEXT:
        print(
            f"WARNING: LinkedIn draft is {len(item['linkedin'])} characters "
            f"(ceiling {MAX_LINKEDIN_TEXT}) — shorten it before posting.",
            file=sys.stderr,
        )
    return item


def run() -> None:
    """One run: parse the flags, then draft (and optionally publish)."""
    # Loaded first, so AI_POST_DATA_DIR set in the env file applies to the
    # --profile default as well as to the history, drafts and run log.
    config.load_env_file()

    parser = argparse.ArgumentParser(
        description="Daily X and LinkedIn draft generator, driven by an "
        "author profile file and a topic you pick at run time."
    )
    parser.add_argument(
        "--topic",
        help="menu number, part of a menu entry, or free text; skips the interactive menu",
    )
    parser.add_argument(
        "--random-topic", action="store_true", help="pick a topic from the menu at random"
    )
    parser.add_argument("--list-topics", action="store_true", help="show the topic menu and exit")
    parser.add_argument(
        "--profile",
        type=Path,
        default=config.profile_file(),
        help=f"author profile path (default: {config.profile_file()})",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        help=f"LLM provider (default: AI_POST_PROVIDER, else {DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "--search",
        choices=SEARCH_SOURCES,
        help="where the item comes from (default: AI_POST_SEARCH, else native for "
        "anthropic and groq, rss for openai-compat)",
    )
    parser.add_argument(
        "--post", action="store_true", help="publish the X version instead of only drafting"
    )
    parser.add_argument(
        "--post-linkedin",
        action="store_true",
        help="publish the LinkedIn version (needs an approved LinkedIn app)",
    )
    parser.add_argument(
        "--history", action="store_true", help="show the last 10 items used, then exit"
    )
    commands = parser.add_subparsers(dest="command", title="commands", metavar="COMMAND")
    commands.add_parser(
        "init", help="create the data directory and an author profile to fill in, then exit"
    )
    args = parser.parse_args()

    if args.command == "init":
        init()
        return

    if args.history:
        for h in load_history()[-10:]:
            topic = f"  [{h['topic']}]" if h.get("topic") else ""
            print(f"{h['date']}  {h['title'][:70]}{topic}\n            {h['url']}")
        return

    profile = load_profile(args.profile)
    topics = topic_menu(profile)

    if args.list_topics:
        for i, topic in enumerate(topics, 1):
            print(f"{i:>2}. {topic}")
        return

    provider = make_provider(select_provider(args.provider))
    search_name = select_search(args.search, provider.name)
    storage.RUN_VIA = f"{provider.name}/{provider.model or 'unset'}, search {search_name}"
    provider.require_credentials()
    search = make_search(search_name, profile)

    topic = choose_topic(topics, args.topic, args.random_topic)
    config.data_dir().mkdir(parents=True, exist_ok=True)

    history = load_history()
    used_urls, used_titles = used_from(history)

    if search.name == "rss":
        print(f"\nReading {len(search.feeds)} feed(s) for: {topic}\n")
    else:
        print(f"\nSearching: {topic}\n")
    item = generate_item(provider, search, profile, topic, used_urls, used_titles)
    x_post, li_post = compose_posts(item)

    tweet_id = post_to_x(x_post) if args.post else ""
    li_id = post_to_linkedin(li_post) if args.post_linkedin else ""

    history.append(
        {
            "date": date.today().isoformat(),
            "topic": topic,
            "url": item["url"],
            "title": item["title"],
            "posted_x": bool(tweet_id),
            "posted_linkedin": bool(li_id),
            "tweet_id": tweet_id,
            "provider": provider.name,
            "model": provider.model,
            "search": search.name,
        }
    )
    save_history(history)
    log_drafts(item, topic, x_post, li_post, bool(tweet_id), bool(li_id), storage.RUN_VIA)
    # R53, R59: to check the token estimate against real figures
    log_run(f"OK — [{topic}] {item['title']} ({item['url']}){storage.RUN_TOKENS}")

    rule = "=" * 70
    print(rule)
    print("X — PUBLISHED" if tweet_id else "X — draft")
    print(rule)
    print(x_post)
    print(f"\n{rule}")
    print("LINKEDIN — PUBLISHED" if li_id else "LINKEDIN — draft")
    print(f"{rule}")
    print(li_post)
    print(f"\n[{len(item['linkedin'])} characters] Saved to {config.drafts_file()}")


def init() -> None:
    """xgp init: create the data directory and an author profile to fill in.

    An existing profile is never touched, and nothing is written to run.log,
    so a refused init changes nothing in the data directory.
    """
    try:
        path = storage.create_profile()
    except FileExistsError:
        print(
            f"An author profile already exists at {config.profile_file()}; "
            "xgp init left it unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(
        f"Created {path} from the example profile (mode 600).\n"
        "Fill in at least ## Beat and ## Voice, then add GROQ_API_KEY to "
        f"{config.env_file()} (see the README)."
    )


def main() -> None:
    """Entry point for xgp and daily_post.py."""
    try:
        run()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
    except SystemExit as exc:
        # Expected, explained failures: log the first line, print the message
        # as written, and exit without a traceback.
        if exc.code and isinstance(exc.code, str):
            log_run(f"FAILED — {exc.code.strip().splitlines()[0]}{storage.RUN_TOKENS}")
        raise
    except Exception as exc:
        # Genuinely unexpected: log it and let the traceback through, since
        # that is the useful artifact for an unknown bug.
        log_run(f"FAILED — {type(exc).__name__}: {exc}{storage.RUN_TOKENS}")
        raise
