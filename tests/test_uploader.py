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


def test_caption_tail_exceeds_limit():
    """当 tail 部分本身很长时，标题应被完全移除，caption 等于 tail。"""
    w = Work(aweme_id="123", aweme_type="video", title="标题" * 1000, author="A" * 1100)
    cap = build_caption(w)
    # tail = "\n\n👤 " + author + "\n🔗 " + url
    tail = f"\n\n👤 {'A' * 1100}\n🔗 https://www.douyin.com/video/123"
    assert len(cap) == len(tail)  # caption 应该等于 tail 长度
    assert "标题" not in cap  # 标题应被完全移除
    assert cap.endswith("🔗 https://www.douyin.com/video/123")


def test_caption_tail_exactly_1024():
    """当 tail 正好为 1024 时，caption 应该等于 tail。"""
    # 计算使得 tail 正好为 1024 的 author 长度
    # tail 格式: "\n\n👤 <author>\n🔗 <url>"
    author_len = 985
    author = "X" * author_len
    w = Work(aweme_id="123", aweme_type="video", title="很长的标题" * 100, author=author)
    cap = build_caption(w)
    tail = f"\n\n👤 {author}\n🔗 https://www.douyin.com/video/123"
    assert len(cap) == 1024  # caption 长度应该正好是 1024
    assert len(tail) == 1024  # tail 也应该正好是 1024
    assert cap == tail  # caption 应该等于 tail（无标题）
    assert "很长的标题" not in cap  # 标题应被完全移除
