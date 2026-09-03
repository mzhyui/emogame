import unittest
from unittest.mock import AsyncMock, patch

import httpx

from crawlers.weibo_skin_comment_crawler import (
    CrawlEntry,
    LONG_TEXT_URL,
    WeiboPost,
    WeiboAccessBlocked,
    WeiboClient,
    fetch_hot_comments,
    fetch_post_text,
    format_output,
)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def _request(self, url, params=None):
        self.calls.append((url, params))
        return self.response


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def _request(self, url, params=None):
        self.calls.append((url, params))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class FetchPostTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_expands_long_post_and_cleans_html(self):
        client = FakeClient({
            "ok": 1,
            "data": {"longTextContent": "<b>full</b> post &amp; details"},
        })

        text = await fetch_post_text(client, {
            "id": "123",
            "isLongText": True,
            "text": "preview…",
        })

        self.assertEqual(text, "full post & details")
        self.assertEqual(client.calls, [(LONG_TEXT_URL, {"id": "123"})])

    async def test_short_post_does_not_request_extension(self):
        client = FakeClient({})

        text = await fetch_post_text(client, {
            "id": "123",
            "isLongText": False,
            "text": "<b>complete</b>",
        })

        self.assertEqual(text, "complete")
        self.assertEqual(client.calls, [])

    async def test_empty_extension_falls_back_to_preview(self):
        client = FakeClient({"ok": 1, "data": {}})

        text = await fetch_post_text(client, {
            "id": "123",
            "isLongText": True,
            "text": "preview…",
        })

        self.assertEqual(text, "preview…")


class FetchHotCommentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_stops_when_weibo_repeats_pagination_cursor(self):
        def payload(text):
            return {
                "ok": 1,
                "data": {
                    "data": [{
                        "user": {"screen_name": "viewer", "id": 1},
                        "text": text,
                        "like_count": 0,
                        "total_number": 0,
                        "created_at": "today",
                        "source": "",
                    }],
                    "max_id": 7,
                },
            }

        client = SequenceClient([payload("first"), payload("second")])

        comments = await fetch_hot_comments(client, "123", max_comments=10)

        self.assertEqual([comment["text"] for comment in comments], ["first", "second"])
        self.assertEqual(len(client.calls), 2)

    async def test_keeps_first_page_when_later_page_fails(self):
        first_page = {
            "ok": 1,
            "data": {
                "data": [{
                    "user": {"screen_name": "viewer", "id": 1},
                    "text": "first",
                    "like_count": 0,
                    "total_number": 0,
                    "created_at": "today",
                    "source": "",
                }],
                "max_id": 7,
            },
        }
        client = SequenceClient([first_page, ValueError("later page unavailable")])

        comments = await fetch_hot_comments(client, "123", max_comments=10)

        self.assertEqual([comment["text"] for comment in comments], ["first"])
        self.assertEqual(len(client.calls), 2)

    async def test_treats_explicit_no_comments_response_as_empty(self):
        client = SequenceClient([ValueError("还没有人评论哦~快来抢沙发！")])

        comments = await fetch_hot_comments(client, "123", max_comments=10)

        self.assertEqual(comments, [])
        self.assertEqual(len(client.calls), 1)

    async def test_treats_generic_empty_content_response_as_empty(self):
        client = SequenceClient([ValueError("Weibo API returned ok=0: 这里还没有内容")])

        comments = await fetch_hot_comments(client, "123", max_comments=10)

        self.assertEqual(comments, [])
        self.assertEqual(len(client.calls), 1)


class WeiboClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_432_is_not_retried(self):
        request_count = 0

        def handler(request):
            nonlocal request_count
            request_count += 1
            return httpx.Response(432, request=request)

        client = WeiboClient("cookie", max_retries=4)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def no_wait():
            return None

        client.rate_limiter.wait = no_wait
        try:
            with self.assertRaisesRegex(WeiboAccessBlocked, "HTTP 432"):
                await client._request("https://m.weibo.cn/api/container/getIndex")
        finally:
            await client._client.aclose()

        self.assertEqual(request_count, 1)

    async def test_transient_protocol_error_is_retried(self):
        request_count = 0

        def handler(request):
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                raise httpx.RemoteProtocolError("server disconnected", request=request)
            return httpx.Response(200, json={"ok": 1, "data": {}}, request=request)

        client = WeiboClient("cookie", max_retries=2)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def no_wait():
            return None

        client.rate_limiter.wait = no_wait
        try:
            with patch(
                "crawlers.weibo_skin_comment_crawler.asyncio.sleep",
                new=AsyncMock(),
            ):
                response = await client._request("https://m.weibo.cn/test")
        finally:
            await client._client.aclose()

        self.assertEqual(response["ok"], 1)
        self.assertEqual(request_count, 2)

    async def test_empty_content_response_is_not_retried(self):
        request_count = 0

        def handler(request):
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                json={"ok": 0, "msg": "这里还没有内容"},
                request=request,
            )

        client = WeiboClient("cookie", max_retries=4)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def no_wait():
            return None

        client.rate_limiter.wait = no_wait
        try:
            response = await client._request("https://m.weibo.cn/comments/hotflow")
        finally:
            await client._client.aclose()

        self.assertEqual(response["ok"], 0)
        self.assertEqual(request_count, 1)


class OutputTests(unittest.TestCase):
    def test_format_output_keeps_full_title(self):
        full_text = "x" * 100
        post = WeiboPost("123", 1, "user", full_text, "", 0, 0, 0)
        entry = CrawlEntry("123", full_text, 0, 0, 0, post, [])

        output = format_output([entry])

        self.assertEqual(output[0]["title"], full_text)


if __name__ == "__main__":
    unittest.main()
