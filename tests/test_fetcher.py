import asyncio

from d2t.fetcher import _normalize, collect_new


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
