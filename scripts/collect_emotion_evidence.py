"""Collect bounded, privacy-minimized Weibo and Bilibili emotion evidence.

The command is a network-free dry run unless ``--apply`` is supplied. Live
collection also requires a run-level ethics status of ready, exempt, or
not_applicable. Checkpoints make each target/platform pair resumable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.bilibili_evidence import (  # noqa: E402
    BILIBILI_REPLY_MAIN_API,
    BILIBILI_REPLY_USER_AGENT,
    BILIBILI_SEARCH_ALL_API,
    BilibiliReplyEvidence,
    DEFAULT_USER_AGENT,
    parse_bilibili_replies,
    parse_bilibili_search_results,
)
from crawlers.weibo_skin_comment_crawler import (  # noqa: E402
    WeiboClient,
    fetch_all_comments,
    fetch_hot_comments,
    load_cookie,
    search_skin_posts,
)
from data.emotion_evidence_repository import EmotionEvidenceRepository  # noqa: E402
from data.skin_repository import DEFAULT_DB_PATH  # noqa: E402
from models.emotion_workflow import (  # noqa: E402
    ASPECT_QUERY_TERMS,
    build_sanitized_evidence_item,
    canonical_json,
    contains_exact_name,
    in_observation_window,
    sha256_json,
    validate_cohort_manifest,
)


DEFAULT_RUN_ID = "20260902-current100-v1"
DEFAULT_MANIFEST = Path("data/emotion_evidence/runs") / DEFAULT_RUN_ID / "manifest.json"
ALLOWED_ETHICS_STATUS = {"ready", "exempt", "not_applicable"}
WEIBO_COMMENT_LIMIT = 60
WEIBO_PARENT_LIMIT = 3
BILIBILI_REPLY_LIMIT = 40
BILIBILI_PARENT_LIMIT = 2
BILIBILI_SEARCH_PAGE_LIMIT = 2
COLLECTION_PLATFORMS = ("weibo", "bilibili")
BILIBILI_EMPTY_PARENT_MESSAGES = (
    "已关闭评论区",
    "评论区已关闭",
    "评论已关闭",
)
MAX_PARENT_SHARE = 0.60


@dataclass(slots=True)
class _ParentBatch:
    rank: int
    parent: Any
    query_aspects: set[str]
    rows: list[Any]


def aspect_queries(target: dict[str, Any]) -> dict[str, str]:
    """Create one exact hero/skin query for each locked aspect."""
    return {
        aspect: " ".join(
            (
                str(target["hero_name"]),
                str(target["skin_name"]),
                *terms,
            )
        )
        for aspect, terms in ASPECT_QUERY_TERMS.items()
    }


def _collection_plan(
    targets: Iterable[dict[str, Any]], platforms: Iterable[str]
) -> list[dict[str, Any]]:
    return [
        {
            "source_key": row["source_key"],
            "hero_name": row["hero_name"],
            "skin_name": row["skin_name"],
            "platform": platform,
            "queries": aspect_queries(row),
        }
        for row in targets
        for platform in platforms
    ]


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_cohort_manifest(payload)
    return payload


def _selected_targets(
    manifest: dict[str, Any], keys: Iterable[str], limit: int
) -> list[dict[str, Any]]:
    requested = set(keys)
    records = [
        dict(row)
        for row in manifest["records"]
        if not requested or str(row["source_key"]) in requested
    ]
    missing = requested - {str(row["source_key"]) for row in records}
    if missing:
        raise ValueError("source keys are outside the frozen cohort: " + ",".join(sorted(missing)))
    return records[: limit or None]


def _parent_scope(title: str, target: dict[str, Any]) -> str:
    if contains_exact_name(title, target["skin_name"]):
        return "exact_skin"
    if contains_exact_name(title, target["hero_name"]) and "皮肤" in title:
        return "hero_fallback"
    return "unverifiable"


def _unique_comments(comments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in comments:
        key = (
            str(row.get("comment_id") or ""),
            str(row.get("user_id") or row.get("user") or ""),
            str(row.get("text") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _parent_fetch_limit(total_limit: int) -> int:
    """Bound one candidate so retained evidence can satisfy the 60% parent cap."""
    return max(1, math.floor(total_limit * MAX_PARENT_SHARE))


def _has_sufficient_parent_evidence(
    batches: Iterable[_ParentBatch], *, parent_limit: int, total_limit: int
) -> bool:
    counts = sorted((len(batch.rows) for batch in batches), reverse=True)
    return sum(counts[:parent_limit]) >= total_limit


def _select_parent_batches(
    batches: Iterable[_ParentBatch], *, parent_limit: int
) -> list[_ParentBatch]:
    chosen = sorted(batches, key=lambda batch: (-len(batch.rows), batch.rank))[
        :parent_limit
    ]
    return sorted(chosen, key=lambda batch: batch.rank)


async def _collect_weibo_once(
    target: dict[str, Any], *, cookie: str, proxy: str | None, timeout: float
) -> list[dict[str, Any]]:
    queries = aspect_queries(target)
    discovered: dict[str, tuple[Any, set[str]]] = {}
    async with WeiboClient(cookie, timeout=timeout, proxy=proxy) as client:
        for aspect, query in queries.items():
            for post in await search_skin_posts(
                client, query, since_date=None, max_posts=5
            ):
                if not post.mid:
                    continue
                if post.mid not in discovered:
                    discovered[post.mid] = (post, set())
                discovered[post.mid][1].add(aspect)

        ranked = sorted(
            (
                item
                for item in discovered.values()
                if _parent_scope(item[0].text, target) == "exact_skin"
            ),
            key=lambda item: (
                -len(item[1]),
                -int(item[0].comments_count or 0),
                str(item[0].mid),
            ),
        )
        batches: list[_ParentBatch] = []
        fetch_limit = _parent_fetch_limit(WEIBO_COMMENT_LIMIT)
        for index, (post, query_aspects) in enumerate(ranked):
            hot = await fetch_hot_comments(client, post.mid, fetch_limit)
            combined = list(hot)
            if len(combined) < fetch_limit:
                try:
                    combined.extend(
                        await fetch_all_comments(
                            client, post.mid, fetch_limit - len(combined)
                        )
                    )
                except Exception:
                    pass
            combined = _unique_comments(combined)[:fetch_limit]
            if not combined:
                continue
            batches.append(
                _ParentBatch(index, post, set(query_aspects), combined)
            )
            if _has_sufficient_parent_evidence(
                batches,
                parent_limit=WEIBO_PARENT_LIMIT,
                total_limit=WEIBO_COMMENT_LIMIT,
            ):
                break

        evidence: list[dict[str, Any]] = []
        remaining = WEIBO_COMMENT_LIMIT
        selected = _select_parent_batches(
            batches, parent_limit=WEIBO_PARENT_LIMIT
        )
        for batch in selected:
            if remaining <= 0:
                break
            post = batch.parent
            combined = batch.rows[:remaining]
            scope = _parent_scope(post.text, target)
            query_hash = sha256_json(
                {
                    aspect: queries[aspect]
                    for aspect in sorted(batch.query_aspects)
                }
            )
            for comment in combined:
                evidence.append(
                    build_sanitized_evidence_item(
                        run_id=str(target["run_id"]),
                        source_key=str(target["source_key"]),
                        platform="weibo",
                        external_id=str(comment.get("comment_id") or ""),
                        parent_external_id=str(post.mid),
                        mapping_scope=scope,
                        author_value=comment.get("user_id") or comment.get("user"),
                        text=comment.get("text"),
                        published_at=comment.get("created_at"),
                        parent_title=post.text,
                        skin_name=str(target["skin_name"]),
                        url=f"https://m.weibo.cn/detail/{post.mid}",
                        metrics={
                            "like_count": int(comment.get("like_count") or 0),
                            "reply_count": int(comment.get("total_number") or 0),
                        },
                        provenance={
                            "collector": "emotion-weibo-v1",
                            "query_hash": query_hash,
                            "query_aspects": sorted(query_aspects),
                            "network_route": "proxy" if proxy else "direct",
                        },
                    )
                )
            remaining -= len(combined)
        return evidence


async def collect_weibo(
    target: dict[str, Any], *, cookie: str, proxy: str, timeout: float
) -> tuple[list[dict[str, Any]], bool]:
    try:
        return await _collect_weibo_once(target, cookie=cookie, proxy=None, timeout=timeout), False
    except (httpx.RequestError, OSError):
        return await _collect_weibo_once(target, cookie=cookie, proxy=proxy, timeout=timeout), True


def _bilibili_json(
    client: httpx.Client, url: str, *, params: dict[str, Any]
) -> dict[str, Any]:
    referer = (
        "https://www.bilibili.com/"
        if url == BILIBILI_REPLY_MAIN_API
        else "https://search.bilibili.com/"
    )
    user_agent = (
        BILIBILI_REPLY_USER_AGENT
        if url == BILIBILI_REPLY_MAIN_API
        else DEFAULT_USER_AGENT
    )
    response = client.get(
        url,
        params=params,
        headers={
            "User-Agent": user_agent,
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"bilibili api error: {payload.get('message')}")
    return payload


def _is_empty_bilibili_parent_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in BILIBILI_EMPTY_PARENT_MESSAGES)


def _fetch_bilibili_parent_replies(
    client: httpx.Client, aid: str, *, limit: int
) -> list[BilibiliReplyEvidence]:
    """Fetch one parent's replies with the API's maximum valid page size."""
    replies: list[BilibiliReplyEvidence] = []
    seen: set[str] = set()
    next_cursor: int | str = 0
    seen_cursors: set[str] = set()
    while len(replies) < limit:
        page_size = min(20, limit - len(replies))
        payload = _bilibili_json(
            client,
            BILIBILI_REPLY_MAIN_API,
            params={
                "type": 1,
                "oid": aid,
                "mode": 2,
                "next": next_cursor,
                "ps": page_size,
                "plat": 1,
            },
        )
        raw_replies = (payload.get("data") or {}).get("replies") or []
        if not raw_replies:
            break
        before = len(replies)
        for reply in parse_bilibili_replies(payload):
            if reply.rpid in seen:
                continue
            seen.add(reply.rpid)
            replies.append(reply)
            if len(replies) >= limit:
                break
        cursor = (payload.get("data") or {}).get("cursor") or {}
        following = cursor.get("next")
        following_key = str(following)
        if (
            len(replies) == before
            or cursor.get("is_end")
            or following in (None, "")
            or following_key == str(next_cursor)
            or following_key in seen_cursors
        ):
            break
        seen_cursors.add(str(next_cursor))
        next_cursor = following
    return replies


def _collect_bilibili_once(
    target: dict[str, Any], *, proxy: str | None, timeout: float
) -> list[dict[str, Any]]:
    queries = aspect_queries(target)
    discovered: dict[str, tuple[Any, set[str]]] = {}
    with httpx.Client(timeout=timeout, proxy=proxy, follow_redirects=True) as client:
        for aspect, query in queries.items():
            for page in range(1, BILIBILI_SEARCH_PAGE_LIMIT + 1):
                payload = _bilibili_json(
                    client,
                    BILIBILI_SEARCH_ALL_API,
                    params={"keyword": query, "page": page},
                )
                videos = parse_bilibili_search_results(payload)
                if not videos:
                    break
                for video in videos:
                    if video.bvid not in discovered:
                        discovered[video.bvid] = (video, set())
                    discovered[video.bvid][1].add(aspect)
        ranked = sorted(
            (
                item
                for item in discovered.values()
                if _parent_scope(item[0].title, target) == "exact_skin"
                and item[0].raw_json.get("aid")
            ),
            key=lambda item: (
                -len(item[1]),
                -int(item[0].play or 0),
                item[0].bvid,
            ),
        )
        batches: list[_ParentBatch] = []
        fetch_limit = _parent_fetch_limit(BILIBILI_REPLY_LIMIT)
        for index, (video, query_aspects) in enumerate(ranked):
            aid = str(video.raw_json.get("aid") or "")
            if not aid:
                continue
            try:
                replies = _fetch_bilibili_parent_replies(
                    client, aid, limit=fetch_limit
                )
            except ValueError as exc:
                if _is_empty_bilibili_parent_error(exc):
                    continue
                raise
            replies = [
                reply
                for reply in replies
                if in_observation_window(reply.published_at)
            ]
            if not replies:
                continue
            batches.append(
                _ParentBatch(index, video, set(query_aspects), replies)
            )
            if _has_sufficient_parent_evidence(
                batches,
                parent_limit=BILIBILI_PARENT_LIMIT,
                total_limit=BILIBILI_REPLY_LIMIT,
            ):
                break

        evidence: list[dict[str, Any]] = []
        remaining = BILIBILI_REPLY_LIMIT
        selected = _select_parent_batches(
            batches, parent_limit=BILIBILI_PARENT_LIMIT
        )
        for batch in selected:
            if remaining <= 0:
                break
            video = batch.parent
            replies = batch.rows[:remaining]
            scope = _parent_scope(video.title, target)
            query_hash = sha256_json(
                {
                    aspect: queries[aspect]
                    for aspect in sorted(batch.query_aspects)
                }
            )
            for reply in replies:
                evidence.append(
                    build_sanitized_evidence_item(
                        run_id=str(target["run_id"]),
                        source_key=str(target["source_key"]),
                        platform="bilibili",
                        external_id=reply.rpid,
                        parent_external_id=video.bvid,
                        mapping_scope=scope,
                        author_value=reply.author_id,
                        text=reply.text,
                        published_at=reply.published_at,
                        parent_title=video.title,
                        skin_name=str(target["skin_name"]),
                        url=f"https://www.bilibili.com/video/{video.bvid}",
                        metrics={
                            "like_count": reply.like_count,
                            "reply_count": reply.reply_count,
                        },
                        provenance={
                            "collector": "emotion-bilibili-v1",
                            "query_hash": query_hash,
                            "query_aspects": sorted(query_aspects),
                            "network_route": "proxy" if proxy else "direct",
                        },
                    )
                )
            remaining -= len(replies)
        return evidence


def collect_bilibili(
    target: dict[str, Any], *, proxy: str, timeout: float
) -> tuple[list[dict[str, Any]], bool]:
    try:
        return _collect_bilibili_once(target, proxy=None, timeout=timeout), False
    except (httpx.RequestError, OSError):
        return _collect_bilibili_once(target, proxy=proxy, timeout=timeout), True
    except ValueError as exc:
        if "bilibili api error: -352" not in str(exc):
            raise
        return _collect_bilibili_once(target, proxy=proxy, timeout=timeout), True


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--platform", choices=("weibo", "bilibili", "both"), default="both")
    parser.add_argument("--source-key", action="append", default=[])
    parser.add_argument("--target-limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--proxy", default="socks5://127.0.0.1:7890")
    parser.add_argument("--retry-completed", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        manifest = _load_manifest(args.manifest)
        targets = _selected_targets(manifest, args.source_key, args.target_limit)
        platforms = (
            list(COLLECTION_PLATFORMS)
            if args.platform == "both"
            else [args.platform]
        )
        contract_plan = _collection_plan(
            manifest["records"], COLLECTION_PLATFORMS
        )
        execution_plan = _collection_plan(targets, platforms)
        plan_hash = sha256_json(contract_plan)
        execution_plan_hash = sha256_json(execution_plan)
        if not args.apply:
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "dry_run": True,
                        "target_count": len(targets),
                        "target_platform_jobs": len(execution_plan),
                        "plan_sha256": plan_hash,
                        "execution_plan_sha256": execution_plan_hash,
                        "network_requests_made": 0,
                        "jobs": execution_plan,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        repo = EmotionEvidenceRepository(args.db)
        run = repo.get_run(args.run_id)
        if run is None:
            raise ValueError(f"emotion run is not initialized: {args.run_id}")
        if run.get("cohort_hash") != manifest.get("records_sha256"):
            raise ValueError("manifest hash does not match the initialized run")
        if (
            run.get("ethics_status") not in ALLOWED_ETHICS_STATUS
            or not run.get("ethics_record_hash")
        ):
            raise ValueError("live collection blocked: record public-data/privacy ethics status first")
        repo.bind_input_hash(args.run_id, "collection_plan", plan_hash)
        checkpoints = {
            (row["source_key"], row["platform"]): row
            for row in repo.collection_checkpoints(args.run_id)
        }
        cookie = load_cookie() if "weibo" in platforms else ""
        totals: Counter[str] = Counter()
        job_results: list[dict[str, Any]] = []
        for target in targets:
            target["run_id"] = args.run_id
            for platform in platforms:
                previous = checkpoints.get((target["source_key"], platform))
                if previous and previous["status"] == "completed" and not args.retry_completed:
                    totals["skipped_completed"] += 1
                    continue
                query_hash = sha256_json(aspect_queries(target))
                try:
                    if platform == "weibo":
                        items, proxy_used = asyncio.run(
                            collect_weibo(target, cookie=cookie, proxy=args.proxy, timeout=args.timeout)
                        )
                    else:
                        items, proxy_used = collect_bilibili(
                            target, proxy=args.proxy, timeout=args.timeout
                        )
                    accepted = 0
                    quarantined = 0
                    for item in items:
                        repo.add_evidence(item)
                        if item.get("quarantine_reason"):
                            quarantined += 1
                        else:
                            accepted += 1
                    repo.save_collection_checkpoint(
                        run_id=args.run_id,
                        source_key=target["source_key"],
                        platform=platform,
                        status="completed",
                        query_hash=query_hash,
                        accepted_count=accepted,
                        quarantined_count=quarantined,
                        proxy_used=proxy_used,
                    )
                    totals["completed"] += 1
                    totals["accepted"] += accepted
                    totals["quarantined"] += quarantined
                    job_results.append(
                        {
                            "source_key": target["source_key"],
                            "platform": platform,
                            "status": "completed",
                            "accepted": accepted,
                            "quarantined": quarantined,
                            "proxy_used": proxy_used,
                        }
                    )
                except Exception as exc:
                    repo.save_collection_checkpoint(
                        run_id=args.run_id,
                        source_key=target["source_key"],
                        platform=platform,
                        status="failed",
                        query_hash=query_hash,
                        accepted_count=0,
                        quarantined_count=0,
                        proxy_used=False,
                        error_text=str(exc)[:500],
                    )
                    totals["failed"] += 1
                    job_results.append(
                        {
                            "source_key": target["source_key"],
                            "platform": platform,
                            "status": "failed",
                            "error": str(exc)[:500],
                        }
                    )
        report = {
            "schema_version": 1,
            "run_id": args.run_id,
            "plan_sha256": plan_hash,
            "execution_plan_sha256": execution_plan_hash,
            "totals": dict(sorted(totals.items())),
            "jobs": job_results,
        }
        if args.report:
            _write_json_atomic(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if totals["failed"] else 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
