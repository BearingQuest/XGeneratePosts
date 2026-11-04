"""Optional publishing. Runs only with --post or --post-linkedin."""

import json
import urllib.error
import urllib.request

from xgenerateposts import config, net

MAYBE_POSTED = (
    "  LinkedIn may have published the post before this failed; check your\n"
    "  profile before re-running.\n"
)


def post_to_x(post: str) -> str:
    """Publish to X. Needs four user-context credentials; a Bearer token is
    app-only auth and cannot post."""
    try:
        import tweepy  # imported lazily so draft-only mode needs no dependency
    except ImportError:
        raise SystemExit(
            "\n--post needs tweepy. Install it with:\n\n  ~/.ai_post_venv/bin/pip install tweepy\n"
        ) from None

    creds = config.x_credentials()
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise SystemExit(
            f"\n--post needs these in {config.env_file()}: {', '.join(missing)}\n\n"
            "  These come from an X developer app set to Read and Write. A\n"
            "  Bearer token is app-only auth and cannot post on your behalf.\n"
        )

    client = tweepy.Client(
        consumer_key=creds["X_API_KEY"],
        consumer_secret=creds["X_API_SECRET"],
        access_token=creds["X_ACCESS_TOKEN"],
        access_token_secret=creds["X_ACCESS_SECRET"],
    )
    try:
        return str(client.create_tweet(text=post).data.get("id", ""))
    except Exception as exc:
        raise SystemExit(
            f"\nX rejected the post:\n\n  {exc}\n\n"
            "  Common causes: the app is set to Read-only (change it, then\n"
            "  regenerate the access token — tokens keep the permissions they\n"
            "  were created with), or a duplicate post.\n"
            "  The drafts were not saved; re-run to try again.\n"
        ) from None


def post_to_linkedin(post: str) -> str:
    """Publish to LinkedIn via the REST Posts API.

    Optional and unused by the default workflow. Requires an approved LinkedIn
    app with the w_member_social scope, a member access token, and your author
    URN (urn:li:person:XXXX). LinkedIn revises both the API version header and
    its app-review requirements periodically — check current docs if this
    returns a 4xx.
    """
    token = config.linkedin_access_token()
    author = config.linkedin_author_urn()
    if not token or not author:
        raise SystemExit(
            f"\n--post-linkedin needs LINKEDIN_ACCESS_TOKEN and "
            f"LINKEDIN_AUTHOR_URN in {config.env_file()}.\n\n"
            "  Both come from an approved LinkedIn app with the\n"
            "  w_member_social scope.\n"
        )

    payload = {
        "author": author,
        "commentary": post,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    request = urllib.request.Request(
        "https://api.linkedin.com/rest/posts",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": config.linkedin_api_version(),
        },
        method="POST",
    )
    try:
        with net.HTTP_OPENER.open(request, timeout=config.LINKEDIN_TIMEOUT) as response:
            net.read_capped(response)
            return response.headers.get("x-restli-id", "posted")
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", "replace")[:300]
        raise SystemExit(
            f"\nLinkedIn rejected the post ({exc.code}):\n\n  {body}\n\n"
            "  LinkedIn revises its API version header on a schedule — if\n"
            "  this is a 4xx, check the current value and set\n"
            "  LINKEDIN_API_VERSION accordingly.\n"
        ) from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"\nCould not reach LinkedIn: {exc.reason}\n") from None
    except TimeoutError:
        raise SystemExit(
            f"\nLinkedIn sent no complete response within {config.LINKEDIN_TIMEOUT:g} seconds.\n\n"
            + MAYBE_POSTED
        ) from None
    except ValueError as exc:
        raise SystemExit(f"\nLinkedIn response refused: {exc}.\n\n" + MAYBE_POSTED) from None
