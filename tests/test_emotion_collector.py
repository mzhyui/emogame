"""Tests for bounded, dry-run-by-default emotion collection."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crawlers.bilibili_evidence import parse_bilibili_replies
from crawlers.weibo_skin_comment_crawler import WeiboPost
from models.emotion_workflow import sha256_json
from scripts import collect_emotion_evidence as collector


def manifest_payload() -> dict:
    records = [
        {
            "source_key": f"skin-{index:03d}",
            "hero_name": f"hero-{index:03d}",
            "skin_name": f"skin-name-{index:03d}",
            "online_date": "2024-01-01",
            "cohort_role": "warm_start" if index < 50 else "coverage_extension",
            "release_era": "pre_2021",
        }
        for index in range(100)
    ]
    return {
        "schema_version": 1,
        "protocol_version": "emotion-evidence-v1",
        "run_id": "run-1",
        "records_sha256": sha256_json(records),
        "records": records,
    }


class EmotionCollectorTests(unittest.TestCase):
    def test_queries_are_separate_for_all_six_aspects(self):
        target = {"hero_name": "赵云", "skin_name": "龙胆"}
        queries = collector.aspect_queries(target)
        self.assertEqual(len(queries), 6)
        self.assertTrue(all("赵云" in query and "龙胆" in query for query in queries.values()))

    def test_default_run_is_network_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            argv = [
                "collect_emotion_evidence.py",
                "--manifest",
                str(manifest),
                "--run-id",
                "run-1",
                "--target-limit",
                "1",
            ]
            output = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch.object(collector, "collect_weibo") as weibo,
                patch.object(collector, "collect_bilibili") as bilibili,
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(collector.main(), 0)
            weibo.assert_not_called()
            bilibili.assert_not_called()
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["network_requests_made"], 0)
            self.assertEqual(payload["target_platform_jobs"], 2)
            full_plan = collector._collection_plan(
                manifest_payload()["records"], collector.COLLECTION_PLATFORMS
            )
            self.assertEqual(payload["plan_sha256"], sha256_json(full_plan))
            self.assertEqual(
                payload["execution_plan_sha256"],
                sha256_json(full_plan[:2]),
            )

    def test_applied_subset_retry_binds_full_run_collection_plan_hash(self):
        payload = manifest_payload()
        full_plan = collector._collection_plan(
            payload["records"], collector.COLLECTION_PLATFORMS
        )
        bound = []

        class FakeRepository:
            def get_run(self, run_id):
                return {
                    "cohort_hash": payload["records_sha256"],
                    "ethics_status": "ready",
                    "ethics_record_hash": "ethics-hash",
                }

            def bind_input_hash(self, run_id, name, digest):
                bound.append((run_id, name, digest))

            def collection_checkpoints(self, run_id):
                return []

            def add_evidence(self, item):
                raise AssertionError("empty mocked collection has no evidence")

            def save_collection_checkpoint(self, **kwargs):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            argv = [
                "collect_emotion_evidence.py",
                "--manifest",
                str(manifest),
                "--run-id",
                "run-1",
                "--source-key",
                "skin-010",
                "--platform",
                "bilibili",
                "--apply",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    collector,
                    "EmotionEvidenceRepository",
                    return_value=FakeRepository(),
                ),
                patch.object(
                    collector,
                    "collect_bilibili",
                    return_value=([], False),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(collector.main(), 0)

        self.assertEqual(
            bound,
            [("run-1", "collection_plan", sha256_json(full_plan))],
        )

    def test_bilibili_reply_parser_keeps_ids_and_text(self):
        rows = parse_bilibili_replies(
            {
                "data": {
                    "replies": [
                        {
                            "rpid_str": "99",
                            "member": {"mid": "123456789"},
                            "content": {"message": "<b>局内手感很好</b>"},
                            "ctime": 1750000000,
                            "like": 3,
                            "rcount": 1,
                        }
                    ]
                }
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rpid, "99")
        self.assertEqual(rows[0].text, "局内手感很好")
        self.assertEqual(rows[0].author_id, "123456789")

    def test_bilibili_reply_main_uses_video_referer(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"code": 0, "data": {}}

        class FakeClient:
            def get(self, url, *, params, headers):
                captured.update({"url": url, "params": params, "headers": headers})
                return FakeResponse()

        collector._bilibili_json(
            FakeClient(),
            collector.BILIBILI_REPLY_MAIN_API,
            params={"oid": "123"},
        )

        self.assertEqual(captured["headers"]["Referer"], "https://www.bilibili.com/")
        self.assertEqual(captured["headers"]["User-Agent"], "Mozilla/5.0")

    def test_bilibili_access_rejection_retries_through_proxy(self):
        target = {"source_key": "skin-001"}
        with patch.object(
            collector,
            "_collect_bilibili_once",
            side_effect=[ValueError("bilibili api error: -352"), []],
        ) as collect_once:
            rows, proxy_used = collector.collect_bilibili(
                target, proxy="socks5://127.0.0.1:7890", timeout=1
            )

        self.assertEqual(rows, [])
        self.assertTrue(proxy_used)
        self.assertEqual(collect_once.call_count, 2)
        self.assertIsNone(collect_once.call_args_list[0].kwargs["proxy"])
        self.assertEqual(
            collect_once.call_args_list[1].kwargs["proxy"],
            "socks5://127.0.0.1:7890",
        )

    def test_bilibili_reply_pages_never_exceed_twenty(self):
        calls = []

        def reply_payload(page):
            return {
                "code": 0,
                "data": {
                    "cursor": {"next": page, "is_end": page >= 2},
                    "replies": [
                        {
                            "rpid_str": f"{page}-{index}",
                            "member": {"mid": f"author-{page}-{index}"},
                            "content": {"message": "局内手感很好"},
                            "ctime": 1750000000,
                        }
                        for index in range(20)
                    ]
                },
            }

        def fake_json(client, url, *, params):
            calls.append(dict(params))
            return reply_payload(params["next"] + 1)

        with patch.object(collector, "_bilibili_json", side_effect=fake_json):
            rows = collector._fetch_bilibili_parent_replies(
                object(), "123", limit=40
            )

        self.assertEqual(len(rows), 40)
        self.assertEqual([call["next"] for call in calls], [0, 1])
        self.assertTrue(all(call["ps"] <= 20 for call in calls))
        self.assertTrue(all(call["mode"] == 2 for call in calls))


class FakeAsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeSyncContext:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class CandidateExpansionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _target() -> dict:
        return {
            "run_id": "run-1",
            "source_key": "skin-001",
            "hero_name": "赵云",
            "skin_name": "龙胆",
        }

    async def test_weibo_skips_empty_parents_and_uses_later_candidate(self):
        posts = [
            WeiboPost(
                mid=str(index),
                user_id=index,
                user_name="author",
                text=f"赵云 龙胆 parent {index}",
                created_at="2025-01-01",
                reposts_count=0,
                comments_count=100 - index,
                attitudes_count=0,
                source="",
                pics=[],
            )
            for index in range(1, 5)
        ]

        async def hot_comments(client, mid, max_comments):
            if mid != "4":
                return []
            return [
                {
                    "comment_id": "comment-4",
                    "user_id": "author-4",
                    "text": "龙胆局内手感很好",
                    "created_at": "2025-01-01T00:00:00+08:00",
                }
            ]

        with (
            patch.object(collector, "WeiboClient", return_value=FakeAsyncContext()),
            patch.object(
                collector,
                "search_skin_posts",
                new=AsyncMock(return_value=posts),
            ),
            patch.object(collector, "fetch_hot_comments", new=hot_comments),
            patch.object(
                collector,
                "fetch_all_comments",
                new=AsyncMock(return_value=[]),
            ),
        ):
            rows = await collector._collect_weibo_once(
                self._target(), cookie="cookie", proxy=None, timeout=1
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parent_external_id"], "4")
        self.assertIsNone(rows[0]["quarantine_reason"])

    async def test_bilibili_skips_closed_and_empty_parents(self):
        search_payload = {
            "code": 0,
            "data": {
                "result": [
                    {
                        "result_type": "video",
                        "data": [
                            {
                                "bvid": f"BV000000000{index}",
                                "aid": str(index),
                                "title": f"赵云 龙胆 parent {index}",
                                "author": "author",
                                "play": 100 - index,
                            }
                            for index in range(1, 4)
                        ],
                    }
                ]
            },
        }
        reply_calls = []

        def fake_json(client, url, *, params):
            if url == collector.BILIBILI_SEARCH_ALL_API:
                return search_payload
            reply_calls.append(str(params["oid"]))
            if str(params["oid"]) == "1":
                raise ValueError("bilibili api error: UP主已关闭评论区")
            if str(params["oid"]) == "2":
                return {"code": 0, "data": {"replies": None}}
            return {
                "code": 0,
                "data": {
                    "replies": [
                        {
                            "rpid_str": "reply-3",
                            "member": {"mid": "author-3"},
                            "content": {"message": "龙胆局内手感很好"},
                            "ctime": 1750000000,
                        }
                    ]
                },
            }

        with (
            patch.object(collector.httpx, "Client", return_value=FakeSyncContext()),
            patch.object(collector, "_bilibili_json", side_effect=fake_json),
        ):
            rows = collector._collect_bilibili_once(
                self._target(), proxy=None, timeout=1
            )

        self.assertEqual(reply_calls, ["1", "2", "3"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parent_external_id"], "BV0000000003")
        self.assertIsNone(rows[0]["quarantine_reason"])

    async def test_bilibili_checks_exact_result_beyond_first_five(self):
        search_payload = {
            "code": 0,
            "data": {
                "result": [
                    {
                        "result_type": "video",
                        "data": [
                            {
                                "bvid": f"BV000000000{index}",
                                "aid": str(index),
                                "title": "unrelated result",
                                "author": "author",
                                "play": 100 - index,
                            }
                            for index in range(1, 6)
                        ]
                        + [
                            {
                                "bvid": "BV0000000006",
                                "aid": "6",
                                "title": "赵云 龙胆 recent discussion",
                                "author": "author",
                                "play": 1,
                            }
                        ],
                    }
                ]
            },
        }

        def fake_json(client, url, *, params):
            if url == collector.BILIBILI_SEARCH_ALL_API:
                return search_payload
            return {
                "code": 0,
                "data": {
                    "replies": [
                        {
                            "rpid_str": "reply-6",
                            "member": {"mid": "author-6"},
                            "content": {"message": "龙胆局内手感很好"},
                            "ctime": 1750000000,
                        }
                    ]
                },
            }

        with (
            patch.object(collector.httpx, "Client", return_value=FakeSyncContext()),
            patch.object(collector, "_bilibili_json", side_effect=fake_json),
        ):
            rows = collector._collect_bilibili_once(
                self._target(), proxy=None, timeout=1
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parent_external_id"], "BV0000000006")

    async def test_bilibili_checks_exact_result_on_second_search_page(self):
        search_pages = []

        def fake_json(client, url, *, params):
            if url == collector.BILIBILI_SEARCH_ALL_API:
                page = int(params["page"])
                search_pages.append(page)
                title = "unrelated result" if page == 1 else "赵云 龙胆 recent discussion"
                return {
                    "code": 0,
                    "data": {
                        "result": [
                            {
                                "result_type": "video",
                                "data": [
                                    {
                                        "bvid": f"BV000000000{page}",
                                        "aid": str(page),
                                        "title": title,
                                        "author": "author",
                                        "play": 1,
                                    }
                                ],
                            }
                        ]
                    },
                }
            return {
                "code": 0,
                "data": {
                    "replies": [
                        {
                            "rpid_str": "reply-page-2",
                            "member": {"mid": "author-page-2"},
                            "content": {"message": "龙胆局内手感很好"},
                            "ctime": 1750000000,
                        }
                    ]
                },
            }

        with (
            patch.object(collector.httpx, "Client", return_value=FakeSyncContext()),
            patch.object(collector, "_bilibili_json", side_effect=fake_json),
        ):
            rows = collector._collect_bilibili_once(
                self._target(), proxy=None, timeout=1
            )

        self.assertIn(2, search_pages)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parent_external_id"], "BV0000000002")

    async def test_bilibili_sparse_parents_do_not_consume_retained_slots(self):
        search_payload = {
            "code": 0,
            "data": {
                "result": [
                    {
                        "result_type": "video",
                        "data": [
                            {
                                "bvid": f"BV000000000{index}",
                                "aid": str(index),
                                "title": f"赵云 龙胆 parent {index}",
                                "author": "author",
                                "play": 100 - index,
                            }
                            for index in range(1, 5)
                        ],
                    }
                ]
            },
        }
        reply_calls = []

        def fake_json(client, url, *, params):
            if url == collector.BILIBILI_SEARCH_ALL_API:
                return search_payload
            aid = str(params["oid"])
            page = int(params["next"]) + 1
            reply_calls.append((aid, page, int(params["ps"])))
            count = 1 if aid in {"1", "2"} else (20 if page == 1 else 0)
            return {
                "code": 0,
                "data": {
                    "replies": [
                        {
                            "rpid_str": f"reply-{aid}-{page}-{index}",
                            "member": {"mid": f"author-{aid}-{index}"},
                            "content": {"message": "龙胆局内手感很好"},
                            "ctime": 1750000000,
                        }
                        for index in range(count)
                    ]
                },
            }

        with (
            patch.object(collector.httpx, "Client", return_value=FakeSyncContext()),
            patch.object(collector, "_bilibili_json", side_effect=fake_json),
        ):
            rows = collector._collect_bilibili_once(
                self._target(), proxy=None, timeout=1
            )

        self.assertEqual({row["parent_external_id"] for row in rows}, {
            "BV0000000003",
            "BV0000000004",
        })
        self.assertEqual(len(rows), 40)
        self.assertEqual({aid for aid, _, _ in reply_calls}, {"1", "2", "3", "4"})
        self.assertTrue(all(page_size <= 20 for _, _, page_size in reply_calls))


if __name__ == "__main__":
    unittest.main()
