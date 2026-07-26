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
