import time

from d2t.state import State


def _rec(aweme_id, title="标题"):
    return {"aweme_id": aweme_id, "aweme_type": "video", "title": title, "author": "作者"}


def make_state(tmp_path):
    return State(tmp_path / "state.db")


def test_add_and_dedup(tmp_path):
    st = make_state(tmp_path)
    assert st.add_works([_rec("3"), _rec("2"), _rec("1")]) == 3  # 新→旧
    assert st.add_works([_rec("4"), _rec("3")]) == 1  # 3 已存在
    assert st.is_known("3") and not st.is_known("99")


def test_batch_order_oldest_first(tmp_path):
    st = make_state(tmp_path)
    st.add_works([_rec("3"), _rec("2"), _rec("1")])  # 点赞顺序：1 最早
    st.add_works([_rec("5"), _rec("4")])             # 之后又赞了 4、5
    batch = st.next_batch(10)
    assert [w.aweme_id for w in batch] == ["1", "2", "3", "4", "5"]
    assert st.next_batch(2)[0].aweme_id == "1"  # limit 生效


def test_status_transitions(tmp_path):
    st = make_state(tmp_path)
    st.add_works([_rec("1")])
    st.mark_uploaded("1")
    assert st.next_batch(10) == []

    st.add_works([_rec("2")])
    assert st.mark_failed("2", "网络错误") == "pending"  # 第 1 次
    assert st.mark_failed("2", "网络错误") == "pending"  # 第 2 次
    assert st.mark_failed("2", "网络错误") == "failed"   # 第 3 次转 failed
    assert st.next_batch(10) == []

    st.reset_failed()
    assert [w.aweme_id for w in st.next_batch(10)] == ["2"]
    assert st.next_batch(10)[0].retries == 0


def test_skip(tmp_path):
    st = make_state(tmp_path)
    st.add_works([_rec("1")])
    st.mark_skipped("1", "超出大小限制")
    assert st.next_batch(10) == []


def test_cooldown(tmp_path):
    st = make_state(tmp_path)
    assert not st.in_cooldown()
    st.set_cooldown(time.time() + 60)
    assert st.in_cooldown()
    st.set_cooldown(time.time() - 1)
    assert not st.in_cooldown()
