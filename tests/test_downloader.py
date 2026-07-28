import asyncio

import httpx

from d2t.downloader import download_media, extract_media
from d2t.models import Media


def test_extract_video_url_str():
    assert extract_media({"video_play_addr": "http://v/a.mp4", "images": None}) == Media(
        kind="video", urls=["http://v/a.mp4"]
    )


def test_extract_video_url_list():
    detail = {"video_play_addr": ["http://v/1.mp4", "http://v/2.mp4"], "images": None}
    assert extract_media(detail).urls == ["http://v/1.mp4"]


def test_extract_images_plain_and_nested():
    plain = {"video_play_addr": None, "images": ["http://i/1.jpg", "http://i/2.jpg"]}
    assert extract_media(plain) == Media(kind="images", urls=["http://i/1.jpg", "http://i/2.jpg"])
    nested = {"video_play_addr": None, "images": [{"url_list": ["http://i/1.jpg"]}]}
    assert extract_media(nested).urls == ["http://i/1.jpg"]


def _raw_static(url):
    return {"url_list": [url]}


def _raw_live(img_url, video_url):
    return {"url_list": [img_url], "video": {"play_addr": {"url_list": [video_url]}}}


def test_extract_live_photo_album_mixed():
    """混合图集：live photo 项取视频地址并标记为 video，静图保持原位。"""
    detail = {
        "video_play_addr": None,
        "images": ["http://i/1.jpg", "http://i/2.jpg", "http://i/3.jpg"],
        "images_raw": [
            _raw_static("http://i/1.jpg"),
            _raw_live("http://i/2.jpg", "http://v/2.mp4"),
            _raw_static("http://i/3.jpg"),
        ],
    }
    media = extract_media(detail)
    assert media.kind == "images"
    assert media.urls == ["http://i/1.jpg", "http://v/2.mp4", "http://i/3.jpg"]
    assert media.item_kinds == ["image", "video", "image"]


def test_extract_all_static_album_has_no_item_kinds():
    detail = {
        "video_play_addr": None,
        "images": ["http://i/1.jpg"],
        "images_raw": [_raw_static("http://i/1.jpg")],
    }
    media = extract_media(detail)
    assert media.urls == ["http://i/1.jpg"]
    assert media.item_kinds is None


def test_extract_malformed_raw_falls_back_to_flat():
    detail = {
        "video_play_addr": None,
        "images": ["http://i/1.jpg"],
        "images_raw": ["not-a-dict"],
    }
    assert extract_media(detail).urls == ["http://i/1.jpg"]


def test_extract_no_media_raises():
    import pytest

    with pytest.raises(ValueError):
        extract_media({"video_play_addr": None, "images": None})


def _mock_client():
    def respond(request):
        return httpx.Response(200, content=b"DATA-" + request.url.path.encode())

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def test_download_video(tmp_path):
    media = Media(kind="video", urls=["http://v/a.mp4"])
    files = asyncio.run(download_media(media, "111", tmp_path, {}, client=_mock_client()))
    assert files == [tmp_path / "111.mp4"]
    assert files[0].read_bytes() == b"DATA-/a.mp4"


def test_download_images(tmp_path):
    media = Media(kind="images", urls=["http://i/1.jpg", "http://i/2.jpg"])
    files = asyncio.run(download_media(media, "222", tmp_path, {}, client=_mock_client()))
    assert [f.name for f in files] == ["222_0.jpg", "222_1.jpg"]


def test_download_live_photo_saves_mp4(tmp_path):
    media = Media(
        kind="images",
        urls=["http://i/1.jpg", "http://v/2.mp4"],
        item_kinds=["image", "video"],
    )
    files = asyncio.run(download_media(media, "444", tmp_path, {}, client=_mock_client()))
    assert [f.name for f in files] == ["444_0.jpg", "444_1.mp4"]


def test_download_oversize_declared_aborts_early(tmp_path):
    """响应头声明的大小超限时立即中止，不下载正文，也不遗留文件。"""
    import pytest
    from d2t.models import OversizeError

    def respond(request):
        return httpx.Response(200, headers={"content-length": "999999"}, content=b"x" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    media = Media(kind="video", urls=["http://v/big.mp4"])
    with pytest.raises(OversizeError):
        asyncio.run(download_media(media, "555", tmp_path, {}, client=client, max_bytes=1000))
    assert list(tmp_path.iterdir()) == []


def test_download_oversize_streaming_aborts(tmp_path):
    """声明缺失时按累计下载字节掐断，不遗留部分文件。"""
    import pytest
    from d2t.models import OversizeError

    async def stream_body():
        for _ in range(10):
            yield b"x" * 512

    def respond(request):
        return httpx.Response(200, content=stream_body())  # 无 content-length

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    media = Media(kind="video", urls=["http://v/big.mp4"])
    with pytest.raises(OversizeError):
        asyncio.run(download_media(media, "666", tmp_path, {}, client=client, max_bytes=1000))
    assert list(tmp_path.iterdir()) == []


def test_download_within_limit_passes(tmp_path):
    media = Media(kind="video", urls=["http://v/a.mp4"])
    files = asyncio.run(
        download_media(media, "777", tmp_path, {}, client=_mock_client(), max_bytes=1000)
    )
    assert files == [tmp_path / "777.mp4"]


def test_download_cleanup_on_failure(tmp_path):
    import pytest

    def respond(request):
        if request.url.path == "/1.jpg":
            return httpx.Response(200, content=b"IMAGE1-DATA")
        else:
            return httpx.Response(404, content=b"Not Found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    media = Media(kind="images", urls=["http://i/1.jpg", "http://i/2.jpg"])

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(download_media(media, "333", tmp_path, {}, client=client))

    # 断言目录下没有遗留任何文件（第一张已下载的也被清掉）
    assert list(tmp_path.iterdir()) == []
