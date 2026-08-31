"""Collect bounded Weibo comments for every skin in a premium radar run.

The generic Weibo crawler limits comments per post and its JSON output does
not retain which search keyword discovered a post.  This runner keeps the
same HTTP/search/comment implementation while applying a per-skin budget and
writing target-aware evidence next to the radar run.

Example:
    .venv/bin/python scripts/crawl_radar_weibo_comments.py \
        data/premium_pilot/runs/20260828-seed42-partial-v3/radar_plots.html
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawlers.weibo_skin_comment_crawler import (  # noqa: E402
    WZRY_UID,
    WeiboAccessBlocked,
    WeiboClient,
    WeiboPost,
    fetch_all_comments,
    fetch_hot_comments,
    filter_comments,
    load_cookie,
    search_skin_posts,
)

logger = logging.getLogger("radar_weibo_crawler")


def _normalise_match_text(value: str) -> str:
    """Normalise punctuation and spacing for conservative name matching."""
    value = unicodedata.normalize("NFKC", value).casefold()
    ignored = " \t\r\n-_·・•—–《》【】[]()（）"
    return "".join(char for char in value if char not in ignored)


def _contains_name(text: str, name: str) -> bool:
    return _normalise_match_text(name) in _normalise_match_text(text)


def load_targets(radar_html: Path) -> list[dict[str, str]]:
    """Load the skin targets from the manifest backing ``radar_html``."""
    radar_html = radar_html.resolve()
    manifest_path = radar_html.with_name("manifest.json")
    if not radar_html.is_file():
        raise FileNotFoundError(f"radar HTML not found: {radar_html}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"companion manifest not found: {manifest_path}")

    radar_text = radar_html.read_text(encoding="utf-8")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in payload.get("records", []):
        target = {
            "source_key": str(record.get("source_key") or "").strip(),
            "hero_name": str(record.get("hero_name") or "").strip(),
            "skin_name": str(record.get("skin_name") or "").strip(),
            "online_date": str(record.get("online_date") or "").strip(),
        }
        if not all(target[key] for key in ("source_key", "hero_name", "skin_name")):
            raise ValueError(f"incomplete target in {manifest_path}: {record}")
        if target["source_key"] in seen:
            raise ValueError(f"duplicate source_key in {manifest_path}: {target['source_key']}")

        search_text = html.escape(
            f"{target['hero_name']} {target['skin_name']} {target['source_key']}".lower(),
            quote=True,
        )
        if f'data-search="{search_text}"' not in radar_text:
            raise ValueError(
                f"manifest target is absent from radar HTML: {target['source_key']}"
            )
        targets.append(target)
        seen.add(target["source_key"])

    if not targets:
        raise ValueError(f"no targets found in {manifest_path}")
    return targets


def rank_matching_posts(
    posts: list[WeiboPost],
    *,
    hero_name: str,
    skin_name: str,
) -> tuple[list[WeiboPost], str]:
    """Return relevant posts, preferring exact official-account matches."""
    exact = [post for post in posts if _contains_name(post.text, skin_name)]
    exact_with_hero = [post for post in exact if _contains_name(post.text, hero_name)]
    if exact_with_hero:
        scope = "exact_skin"
        matching = exact_with_hero
    elif exact:
        scope = "exact_skin"
        matching = exact
    else:
        matching = [
            post
            for post in posts
            if _contains_name(post.text, hero_name) and "皮肤" in post.text
        ]
        scope = "hero_skin_fallback"

    return sorted(
        matching,
        key=lambda post: (
            0 if _contains_name(post.text, skin_name) else 1,
            0 if _contains_name(post.text, hero_name) else 1,
            0 if post.user_id == WZRY_UID else 1,
            -int(post.comments_count or 0),
            str(post.mid),
        ),
    ), scope


def _post_record(post: WeiboPost) -> dict[str, Any]:
    return {
        "mid": post.mid,
        "user_id": post.user_id,
        "user_name": post.user_name,
        "source_type": "official" if post.user_id == WZRY_UID else "general",
        "title": post.text,
        "created_at": post.created_at,
        "comments_count": post.comments_count,
        "attitudes_count": post.attitudes_count,
    }


def _comment_key(comment: dict[str, Any]) -> tuple[Any, str, str]:
    return (
        comment.get("user_id", 0),
        str(comment.get("created_at") or ""),
        str(comment.get("text") or ""),
    )


async def discover_posts(
    client: WeiboClient,
    target: dict[str, str],
    *,
    search_limit: int,
    minimum_posts: int = 1,
) -> tuple[list[WeiboPost], list[str], str]:
    """Search exact skin first, then qualified and generic fallbacks."""
    queries = [target["skin_name"]]
    posts = await search_skin_posts(
        client,
        target["skin_name"],
        since_date=None,
        max_posts=search_limit,
    )
    ranked, match_scope = rank_matching_posts(
        posts,
        hero_name=target["hero_name"],
        skin_name=target["skin_name"],
    )
    if (
        ranked
        and match_scope == "exact_skin"
        and len(ranked) >= minimum_posts
    ):
        return ranked, queries, match_scope

    qualified_query = f"{target['hero_name']} {target['skin_name']}"
    queries.append(qualified_query)
    qualified_posts = await search_skin_posts(
        client,
        qualified_query,
        since_date=None,
        max_posts=search_limit,
    )
    by_mid = {post.mid: post for post in [*posts, *qualified_posts] if post.mid}
    ranked, match_scope = rank_matching_posts(
        list(by_mid.values()),
        hero_name=target["hero_name"],
        skin_name=target["skin_name"],
    )
    if ranked and match_scope == "exact_skin":
        return ranked, queries, match_scope

    fallback_query = f"{target['hero_name']} 皮肤"
    queries.append(fallback_query)
    fallback_posts = await search_skin_posts(
        client,
        fallback_query,
        since_date=None,
        max_posts=search_limit,
    )
    by_mid = {
        post.mid: post
        for post in [*posts, *qualified_posts, *fallback_posts]
        if post.mid
    }
    ranked, match_scope = rank_matching_posts(
        list(by_mid.values()),
        hero_name=target["hero_name"],
        skin_name=target["skin_name"],
    )
    return ranked, queries, match_scope


async def crawl_target(
    client: WeiboClient,
    target: dict[str, str],
    *,
    max_comments: int,
    search_limit: int,
    max_posts: int,
    min_skin_signals: int,
) -> dict[str, Any]:
    """Collect at most ``max_comments`` raw comments for one target."""
    ranked, queries, match_scope = await discover_posts(
        client,
        target,
        search_limit=search_limit,
        minimum_posts=max_posts,
    )
    if not ranked:
        return {
            **target,
            "queries": queries,
            "match_scope": "none",
            "status": "no_matching_post",
            "raw_comments_considered": 0,
            "filtered_comment_count": 0,
            "comment_count": 0,
            "posts": [],
            "comments": [],
        }

    raw_budget = max_comments
    filtered_count = 0
    comments: list[dict[str, Any]] = []
    used_posts: list[dict[str, Any]] = []
    used_post_ids: set[str] = set()
    seen_comments: set[tuple[Any, str, str]] = set()

    async def collect_from(candidate_posts: list[WeiboPost]) -> None:
        nonlocal raw_budget, filtered_count
        for post in candidate_posts:
            if raw_budget <= 0 or len(used_posts) >= max_posts:
                break
            if post.mid in used_post_ids:
                continue
            used_post_ids.add(post.mid)
            used_posts.append(_post_record(post))

            hot = await fetch_hot_comments(client, post.mid, raw_budget)
            raw_budget -= len(hot)
            raw_candidates = list(hot)
            if raw_budget > 0:
                try:
                    recent = await fetch_all_comments(client, post.mid, raw_budget)
                except Exception as exc:
                    logger.warning(
                        "All-comments fallback unavailable for mid=%s; keeping hot comments: %s",
                        post.mid,
                        exc,
                    )
                else:
                    raw_budget -= len(recent)
                    raw_candidates.extend(recent)

            unique_candidates: list[dict[str, Any]] = []
            for comment in raw_candidates:
                key = _comment_key(comment)
                if key in seen_comments:
                    continue
                seen_comments.add(key)
                unique_candidates.append(comment)

            meaningful, low_quality = filter_comments(
                unique_candidates, min_skin_signals
            )
            filtered_count += low_quality
            for comment in meaningful:
                if len(comments) >= max_comments:
                    break
                comments.append({
                    **comment,
                    "post_mid": post.mid,
                    "post_source_type": (
                        "official" if post.user_id == WZRY_UID else "general"
                    ),
                })

    await collect_from(ranked)

    if not comments and raw_budget > 0 and len(used_posts) < max_posts:
        fallback_query = f"{target['hero_name']} 皮肤"
        if fallback_query not in queries:
            queries.append(fallback_query)
        fallback_posts = await search_skin_posts(
            client,
            fallback_query,
            since_date=None,
            max_posts=search_limit,
        )
        fallback_posts = [
            post
            for post in fallback_posts
            if post.mid not in used_post_ids
            and _contains_name(post.text, target["hero_name"])
            and "皮肤" in post.text
        ]
        fallback_posts.sort(
            key=lambda post: (
                0 if post.user_id == WZRY_UID else 1,
                -int(post.comments_count or 0),
                str(post.mid),
            )
        )
        if fallback_posts:
            match_scope = "hero_skin_fallback_after_empty_exact"
            await collect_from(fallback_posts)

    considered = max_comments - raw_budget
    return {
        **target,
        "queries": queries,
        "match_scope": match_scope,
        "status": "ok" if comments else "no_comments",
        "raw_comments_considered": considered,
        "filtered_comment_count": filtered_count,
        "comment_count": len(comments),
        "posts": used_posts,
        "comments": comments,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _summary(targets: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for target in targets:
        status = str(target.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "target_count": len(targets),
        "status_counts": status_counts,
        "targets_with_comments": sum(bool(target.get("comments")) for target in targets),
        "total_comments": sum(int(target.get("comment_count") or 0) for target in targets),
        "official_comment_count": sum(
            comment.get("post_source_type") == "official"
            for target in targets
            for comment in target.get("comments", [])
        ),
        "general_comment_count": sum(
            comment.get("post_source_type") == "general"
            for target in targets
            for comment in target.get("comments", [])
        ),
    }


def _base_payload(
    *,
    radar_html: Path,
    max_comments: int,
    search_limit: int,
    max_posts: int,
    min_skin_signals: int,
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_radar_html": str(radar_html),
        "collection_policy": {
            "query_order": [
                "exact skin name",
                "hero name + exact skin name",
                "hero name + 皮肤 fallback",
            ],
            "post_priority": ["exact skin match", "hero match", "official", "engagement"],
            "max_comments_per_target": max_comments,
            "search_results_per_query": search_limit,
            "max_posts_per_target": max_posts,
            "min_skin_signals": min_skin_signals,
            "date_cutoff": None,
        },
        "summary": _summary(targets),
        "targets": targets,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect target-aware Weibo comments for a premium radar run."
    )
    parser.add_argument("radar_html", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-comments", type=int, default=25)
    parser.add_argument("--search-limit", type=int, default=8)
    parser.add_argument("--max-posts", type=int, default=3)
    parser.add_argument("--min-signals", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-empty",
        action="store_true",
        help="With --resume, retry prior no_comments and no_matching_post targets.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.max_comments < 1:
        raise ValueError("--max-comments must be positive")
    if args.search_limit < 1 or args.max_posts < 1:
        raise ValueError("--search-limit and --max-posts must be positive")

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    radar_html = args.radar_html.resolve()
    output_path = (
        args.output.resolve()
        if args.output
        else radar_html.with_name("weibo_comments.json")
    )
    source_targets = load_targets(radar_html)

    completed: dict[str, dict[str, Any]] = {}
    if args.resume and output_path.is_file():
        prior = json.loads(output_path.read_text(encoding="utf-8"))
        completed_statuses = {"ok"}
        if not args.retry_empty:
            completed_statuses.update({"no_comments", "no_matching_post"})
        completed = {
            str(target.get("source_key")): target
            for target in prior.get("targets", [])
            if target.get("status") in completed_statuses
        }
        logger.info("Resuming with %d completed targets", len(completed))

    results: list[dict[str, Any]] = []
    cookie = load_cookie()
    try:
        async with WeiboClient(cookie) as client:
            for index, target in enumerate(source_targets, start=1):
                if target["source_key"] in completed:
                    result = completed[target["source_key"]]
                    logger.info(
                        "[%d/%d] reuse %s %s (%s comments)",
                        index,
                        len(source_targets),
                        target["hero_name"],
                        target["skin_name"],
                        result.get("comment_count", 0),
                    )
                else:
                    logger.info(
                        "[%d/%d] crawl %s %s",
                        index,
                        len(source_targets),
                        target["hero_name"],
                        target["skin_name"],
                    )
                    try:
                        result = await crawl_target(
                            client,
                            target,
                            max_comments=args.max_comments,
                            search_limit=args.search_limit,
                            max_posts=args.max_posts,
                            min_skin_signals=args.min_signals,
                        )
                    except WeiboAccessBlocked:
                        raise
                    except Exception as exc:
                        logger.exception(
                            "Target failed: %s %s", target["hero_name"], target["skin_name"]
                        )
                        result = {
                            **target,
                            "queries": [],
                            "match_scope": "none",
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "raw_comments_considered": 0,
                            "filtered_comment_count": 0,
                            "comment_count": 0,
                            "posts": [],
                            "comments": [],
                        }

                results.append(result)
                payload = _base_payload(
                    radar_html=radar_html,
                    max_comments=args.max_comments,
                    search_limit=args.search_limit,
                    max_posts=args.max_posts,
                    min_skin_signals=args.min_signals,
                    targets=results,
                )
                _write_json_atomic(output_path, payload)
    except BaseException:
        if results:
            payload = _base_payload(
                radar_html=radar_html,
                max_comments=args.max_comments,
                search_limit=args.search_limit,
                max_posts=args.max_posts,
                min_skin_signals=args.min_signals,
                targets=results,
            )
            payload["interrupted"] = True
            _write_json_atomic(output_path, payload)
        raise

    summary = _summary(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Output: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
