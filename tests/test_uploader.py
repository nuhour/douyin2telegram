from d2t.models import Work
from d2t.uploader import build_caption, chunk10


def _work(title):
    return Work(aweme_id="123", aweme_type="video", title=title, author="张三")


def test_caption_format():
    cap = build_caption(_work("好视频"))
    assert cap == "好视频\n\n👤 张三\n🔗 https://www.douyin.com/video/123"


def test_caption_truncated_to_1024():
    cap = build_caption(_work("长" * 2000))
    assert len(cap) <= 1024
    assert cap.endswith("🔗 https://www.douyin.com/video/123")
    assert "…" in cap


def test_images_url_uses_note():
    w = Work(aweme_id="9", aweme_type="images", title="图", author="a")
    assert "douyin.com/note/9" in build_caption(w)


def test_chunk10():
    assert chunk10(list(range(23))) == [list(range(10)), list(range(10, 20)), [20, 21, 22]]
    assert chunk10([1]) == [[1]]
    assert chunk10([]) == []
