import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crawlers.weibo_skin_comment_crawler import WZRY_UID, WeiboPost
from scripts.crawl_radar_weibo_comments import (
    crawl_target,
    discover_posts,
    load_targets,
    rank_matching_posts,
)


def make_post(mid, user_id, text, comments_count=0):
    return WeiboPost(
        mid=mid,
        user_id=user_id,
        user_name="王者荣耀" if user_id == WZRY_UID else "general",
        text=text,
        created_at="",
        reposts_count=0,
        comments_count=comments_count,
        attitudes_count=0,
    )


class LoadTargetsTests(unittest.TestCase):
    def test_loads_only_manifest_targets_present_in_radar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            radar = root / "radar_plots.html"
            radar.write_text(
                '<section data-search="鲁班七号 极速特派 detail-1"></section>',
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps({
                    "records": [{
                        "source_key": "detail-1",
                        "hero_name": "鲁班七号",
                        "skin_name": "极速特派",
                        "online_date": "2026-04-23",
                    }]
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            targets = load_targets(radar)

        self.assertEqual(targets[0]["source_key"], "detail-1")
        self.assertEqual(targets[0]["skin_name"], "极速特派")


class RankMatchingPostsTests(unittest.TestCase):
    def test_prefers_official_exact_match_over_general_engagement(self):
        general = make_post("2", 99, "鲁班七号-极速特派测评", 500)
        official = make_post("1", WZRY_UID, "【鲁班七号-极速特派】爆料", 10)

        ranked, scope = rank_matching_posts(
            [general, official], hero_name="鲁班七号", skin_name="极速特派"
        )

        self.assertEqual(scope, "exact_skin")
        self.assertEqual([post.mid for post in ranked], ["1", "2"])

    def test_uses_hero_skin_fallback_only_without_exact_skin(self):
        relevant = make_post("1", WZRY_UID, "鲁班七号新皮肤免费得")
        irrelevant = make_post("2", WZRY_UID, "孙尚香新皮肤")

        ranked, scope = rank_matching_posts(
            [irrelevant, relevant], hero_name="鲁班七号", skin_name="极速特派"
        )

        self.assertEqual(scope, "hero_skin_fallback")
        self.assertEqual([post.mid for post in ranked], ["1"])

    def test_excludes_same_named_skin_for_a_different_hero(self):
        wanted = make_post("1", WZRY_UID, "哪吒-罗小黑战记上线")
        other_hero = make_post("2", WZRY_UID, "百里玄策-罗小黑战记上线", 500)

        ranked, scope = rank_matching_posts(
            [other_hero, wanted], hero_name="哪吒", skin_name="罗小黑战记"
        )

        self.assertEqual(scope, "exact_skin")
        self.assertEqual([post.mid for post in ranked], ["1"])

    def test_normalises_skin_name_punctuation(self):
        post = make_post("1", WZRY_UID, "达摩 星君亢金 新皮肤")

        ranked, scope = rank_matching_posts(
            [post], hero_name="达摩", skin_name="星君·亢金"
        )

        self.assertEqual(scope, "exact_skin")
        self.assertEqual([item.mid for item in ranked], ["1"])


class CrawlTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_qualified_query_fills_sparse_exact_results(self):
        target = {
            "source_key": "105-02",
            "hero_name": "廉颇",
            "skin_name": "地狱岩魂",
            "online_date": "2015-08-14",
        }
        empty_post = make_post("1", 99, "廉颇-地狱岩魂抽奖")
        discussion_post = make_post("2", 98, "廉颇 地狱岩魂 语音故事")

        with patch(
            "scripts.crawl_radar_weibo_comments.search_skin_posts",
            new=AsyncMock(side_effect=[[empty_post], [discussion_post]]),
        ) as search:
            posts, queries, scope = await discover_posts(
                object(), target, search_limit=8, minimum_posts=3
            )

        self.assertEqual(queries, ["地狱岩魂", "廉颇 地狱岩魂"])
        self.assertEqual(scope, "exact_skin")
        self.assertEqual({post.mid for post in posts}, {"1", "2"})
        self.assertEqual(search.await_count, 2)

    async def test_uses_hero_skin_post_after_exact_post_has_no_comments(self):
        target = {
            "source_key": "118-08",
            "hero_name": "孙膑",
            "skin_name": "小动物乐团",
            "online_date": "2023-12-01",
        }
        empty_exact = make_post("1", 99, "孙膑-小动物乐团")
        hero_skin_post = make_post("2", WZRY_UID, "孙膑新皮肤设计讨论")
        comment = {
            "user": "viewer",
            "user_id": 1,
            "text": "希望孙膑的新皮肤特效更活泼",
            "like_count": 2,
            "total_number": 0,
            "created_at": "today",
            "source": "",
        }

        with (
            patch(
                "scripts.crawl_radar_weibo_comments.discover_posts",
                new=AsyncMock(return_value=([empty_exact], ["小动物乐团"], "exact_skin")),
            ),
            patch(
                "scripts.crawl_radar_weibo_comments.search_skin_posts",
                new=AsyncMock(return_value=[hero_skin_post]),
            ),
            patch(
                "scripts.crawl_radar_weibo_comments.fetch_hot_comments",
                new=AsyncMock(side_effect=[[], [comment]]),
            ),
            patch(
                "scripts.crawl_radar_weibo_comments.fetch_all_comments",
                new=AsyncMock(side_effect=ValueError("endpoint unavailable")),
            ),
        ):
            result = await crawl_target(
                object(),
                target,
                max_comments=25,
                search_limit=8,
                max_posts=3,
                min_skin_signals=0,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["match_scope"], "hero_skin_fallback_after_empty_exact")
        self.assertEqual(result["comment_count"], 1)
        self.assertEqual([post["mid"] for post in result["posts"]], ["1", "2"])

    async def test_keeps_hot_comments_when_all_comments_endpoint_fails(self):
        target = {
            "source_key": "detail-1",
            "hero_name": "鲁班七号",
            "skin_name": "极速特派",
            "online_date": "2026-04-23",
        }
        post = make_post("1", WZRY_UID, "鲁班七号-极速特派")
        comment = {
            "user": "viewer",
            "user_id": 1,
            "text": "这个皮肤特效很好看",
            "like_count": 2,
            "total_number": 0,
            "created_at": "today",
            "source": "",
        }

        with (
            patch(
                "scripts.crawl_radar_weibo_comments.discover_posts",
                new=AsyncMock(return_value=([post], ["极速特派"], "exact_skin")),
            ),
            patch(
                "scripts.crawl_radar_weibo_comments.fetch_hot_comments",
                new=AsyncMock(return_value=[comment]),
            ),
            patch(
                "scripts.crawl_radar_weibo_comments.fetch_all_comments",
                new=AsyncMock(side_effect=ValueError("endpoint unavailable")),
            ),
        ):
            result = await crawl_target(
                object(),
                target,
                max_comments=25,
                search_limit=8,
                max_posts=3,
                min_skin_signals=0,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["comment_count"], 1)
        self.assertEqual(result["comments"][0]["post_source_type"], "official")


if __name__ == "__main__":
    unittest.main()
