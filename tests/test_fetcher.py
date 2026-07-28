import asyncio
import pytest

from d2t.fetcher import _normalize, collect_new, DouyinFetcher
from d2t.models import DouyinAuthError


async def _pages(*pages):
    for p in pages:
        yield p


def _rec(aweme_id):
    return {"aweme_id": aweme_id, "aweme_type": "video", "title": "", "author": ""}


def test_collect_stops_after_consecutive_known():
    known = {"10", "9", "8", "7"}
    pages = _pages(
        [_rec("13"), _rec("12"), _rec("11"), _rec("10")],
        [_rec("9"), _rec("8"), _rec("7")],  # 到 8 时连续 3 条已知，停止
    )
    new = asyncio.run(collect_new(pages, known.__contains__))
    assert [r["aweme_id"] for r in new] == ["13", "12", "11"]


def test_cancelled_like_does_not_stop_early():
    # 中间取消赞形成"已知-新-已知"交错，不能只凭 1 条已知就停
    known = {"10", "8", "7", "6"}
    pages = _pages(
        [_rec("12"), _rec("10"), _rec("11"), _rec("8"), _rec("7"), _rec("6")],
    )
    new = asyncio.run(collect_new(pages, known.__contains__))
    assert [r["aweme_id"] for r in new] == ["12", "11"]


def test_first_run_collects_everything():
    pages = _pages([_rec("3"), _rec("2")], [_rec("1")])
    new = asyncio.run(collect_new(pages, lambda _: False))
    assert [r["aweme_id"] for r in new] == ["3", "2", "1"]


def test_normalize_video_and_images():
    video = {"aweme_id": 123, "desc": "标题", "nickname": "作者", "images": None}
    images = {"aweme_id": "456", "desc": None, "nickname": None, "images": ["u1"]}
    assert _normalize(video) == {
        "aweme_id": "123", "aweme_type": "video", "title": "标题", "author": "作者",
    }
    assert _normalize(images) == {
        "aweme_id": "456", "aweme_type": "images", "title": "", "author": "",
    }


class FakeFilter:
    """假 filter 对象，模拟 f2 的 aweme_list."""
    def __init__(self, records):
        self.records = records

    def _to_list(self):
        return self.records


class AuthExpiredFilter(FakeFilter):
    status_code = 8


async def _fake_fetch_user_like_videos(*yields):
    """生成一系列假 filter 对象."""
    for records in yields:
        yield records if hasattr(records, "_to_list") else FakeFilter(records)


def test_fetch_like_pages_skips_transient_empty_page():
    """非首页空页被跳过，继续消费底层生成器，获取后续数据."""
    async def run_test():
        from unittest.mock import MagicMock
        fetcher = DouyinFetcher(MagicMock(douyin=MagicMock(profile_url="", cookie="")))
        # 替换 handler 为假对象，返回假的异步生成器
        fetcher.handler = MagicMock()
        fetcher.handler.fetch_user_like_videos = MagicMock(
            return_value=_fake_fetch_user_like_videos(
                [_rec("3"), _rec("2")],  # 第一页：2 条
                [],                      # 第二页：空（被跳过）
                [_rec("1")],            # 第三页：1 条
            )
        )

        result = []
        async for page in fetcher.fetch_like_pages("sec_user_id"):
            result.extend(page)

        # 应该获得全部 3 条记录
        assert len(result) == 3
        assert [r["aweme_id"] for r in result] == ["3", "2", "1"]

    asyncio.run(run_test())


def test_fetch_like_pages_first_page_empty_raises():
    """首页为空时抛 DouyinAuthError."""
    async def run_test():
        from unittest.mock import MagicMock
        fetcher = DouyinFetcher(MagicMock(douyin=MagicMock(profile_url="", cookie="")))
        # 替换 handler 为假对象，返回首页空的异步生成器
        fetcher.handler = MagicMock()
        fetcher.handler.fetch_user_like_videos = MagicMock(
            return_value=_fake_fetch_user_like_videos([])
        )

        with pytest.raises(DouyinAuthError, match="喜欢列表首页为空"):
            async for _ in fetcher.fetch_like_pages("sec_user_id"):
                pass

    asyncio.run(run_test())


def test_fetch_like_pages_login_expired_response_raises():
    """F2 返回 status_code=8 时，即使结构可解析，也必须识别为 Cookie 失效。"""
    async def run_test():
        from unittest.mock import MagicMock
        fetcher = DouyinFetcher(MagicMock(douyin=MagicMock(profile_url="", cookie="")))
        fetcher.handler = MagicMock()
        fetcher.handler.fetch_user_like_videos = MagicMock(
            return_value=_fake_fetch_user_like_videos(AuthExpiredFilter([_rec("1")]))
        )

        with pytest.raises(DouyinAuthError, match="登录已过期"):
            async for _ in fetcher.fetch_like_pages("sec_user_id"):
                pass

    asyncio.run(run_test())
