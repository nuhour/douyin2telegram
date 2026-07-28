import asyncio
from pathlib import Path

from d2t.config import Config, DouyinConfig, SyncConfig, TelegramConfig
from d2t.models import DouyinAuthError, OversizeError
from d2t.state import State
from main import run_backfill, run_tick


def _cfg():
    return Config(
        douyin=DouyinConfig(cookie="c", profile_url="u"),
        telegram=TelegramConfig(
            api_id=1, api_hash="h", bot_token="t", channel="@ch", alert_chat_id=9
        ),
        sync=SyncConfig(sleep_min=0, sleep_max=0),
    )


def _rec(aweme_id):
    return {"aweme_id": aweme_id, "aweme_type": "video", "title": "t", "author": "a"}


class FakeFetcher:
    def __init__(self, pages=None, fail_fetch=False, fail_detail=False,
                 fail_detail_ids=None, detail_overrides=None, resumable_pages=None):
        self.pages, self.fail_fetch, self.fail_detail = pages or [], fail_fetch, fail_detail
        self.fail_detail_ids = fail_detail_ids or set()
        self.detail_overrides = detail_overrides or {}
        self.resumable_pages = resumable_pages or []  # [(next_cursor, has_more, records)]
        self.start_cursors = []
        self.http_headers = {}

    async def resolve_sec_user_id(self):
        if self.fail_fetch:
            raise DouyinAuthError("cookie 失效")
        return "sec_uid"

    async def fetch_like_pages(self, sec_user_id):
        for p in self.pages:
            yield p

    async def fetch_like_pages_resumable(self, sec_user_id, start_cursor=0):
        self.start_cursors.append(start_cursor)
        for cursor, has_more, records in self.resumable_pages:
            if start_cursor and cursor >= start_cursor:  # 游标递减；跳过断点前已翻过的页
                continue
            yield cursor, has_more, records

    async def fetch_detail(self, aweme_id):
        if self.fail_detail or aweme_id in self.fail_detail_ids:
            raise RuntimeError("视频已删除")
        if aweme_id in self.detail_overrides:
            return self.detail_overrides[aweme_id]
        return {"video_play_addr": f"http://v/{aweme_id}.mp4", "images": None}


class FakeUploader:
    def __init__(self, oversize=False):
        self.oversize = oversize
        self.uploaded, self.link_cards, self.messages, self.kinds = [], [], [], []

    async def upload_work(self, work, files, kind, progress=None):
        if self.oversize:
            raise OversizeError("太大")
        self.uploaded.append(work.aweme_id)
        self.kinds.append((work.aweme_id, kind))

    async def send_link_card(self, work, reason):
        self.link_cards.append(work.aweme_id)

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class FakeNotifier:
    def __init__(self, fail_alert=False):
        self.alerts = []
        self.fail_alert = fail_alert

    async def alert(self, text):
        self.alerts.append(text)
        if self.fail_alert:
            raise RuntimeError("Telethon 解析 entity 失败")


async def _noop_sleep(_):
    pass


async def _fake_download(media, aweme_id, tmp_dir, headers, client=None, heartbeat=None, max_bytes=None):
    path = Path(tmp_dir) / f"{aweme_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return [path]


def _run(state, fetcher, uploader, tmp_path, limit=None, notifier=None, download_fn=_fake_download):
    notifier = notifier or FakeNotifier()
    asyncio.run(
        run_tick(
            _cfg(), state, fetcher, uploader, notifier,
            tmp_dir=tmp_path / "tmp", sleep_fn=_noop_sleep,
            download_fn=download_fn, limit=limit,
        )
    )
    return notifier


def test_happy_path(tmp_path):
    state = State(tmp_path / "s.db")
    fetcher = FakeFetcher(pages=[[_rec("2"), _rec("1")]])
    uploader = FakeUploader()
    _run(state, fetcher, uploader, tmp_path)
    assert uploader.uploaded == ["1", "2"]  # 点赞正序
    assert state.next_batch(10) == []


def test_auth_error_sets_cooldown_and_alerts(tmp_path):
    state = State(tmp_path / "s.db")
    notifier = _run(state, FakeFetcher(fail_fetch=True), FakeUploader(), tmp_path)
    assert state.in_cooldown()
    assert "Cookie" in notifier.alerts[0] or "失败" in notifier.alerts[0]


def test_auth_error_while_fetching_detail_alerts_and_does_not_consume_retry(tmp_path):
    """详情阶段才发现 Cookie 失效时，也应告警并保留作品待处理。"""
    state = State(tmp_path / "s.db")

    class DetailAuthFailureFetcher(FakeFetcher):
        async def fetch_detail(self, aweme_id):
            raise DouyinAuthError("详情接口登录已过期")

    notifier = _run(
        state, DetailAuthFailureFetcher(pages=[[_rec("1")]]), FakeUploader(), tmp_path
    )
    assert state.in_cooldown()
    pending = state.next_batch(10)
    assert len(pending) == 1
    assert pending[0].retries == 0
    assert "Cookie" in notifier.alerts[0]


def test_cooldown_skips_tick(tmp_path):
    import time

    state = State(tmp_path / "s.db")
    state.set_cooldown(time.time() + 3600)
    uploader = FakeUploader()
    _run(state, FakeFetcher(pages=[[_rec("1")]]), uploader, tmp_path)
    assert uploader.uploaded == []


def test_oversize_falls_back_to_link_card(tmp_path):
    state = State(tmp_path / "s.db")
    uploader = FakeUploader(oversize=True)
    _run(state, FakeFetcher(pages=[[_rec("1")]]), uploader, tmp_path)
    assert uploader.link_cards == ["1"]
    assert state.next_batch(10) == []  # skipped，不再重试


def test_detail_failure_retries_then_failed(tmp_path):
    state = State(tmp_path / "s.db")
    fetcher = FakeFetcher(pages=[[_rec("1")]], fail_detail=True)
    notifier = _run(state, fetcher, FakeUploader(), tmp_path)
    assert state.next_batch(10)[0].retries == 1  # 第 1 次失败仍 pending
    fetcher.pages = []
    _run(state, fetcher, FakeUploader(), tmp_path)
    uploader = FakeUploader()
    notifier = _run(state, fetcher, uploader, tmp_path)
    assert state.next_batch(10) == []  # 3 次后 failed
    assert any("重试 3 次" in t for t in notifier.alerts)
    assert uploader.link_cards == ["1"]  # F5：多次失败后降级为链接卡片，不彻底丢失


def test_limit(tmp_path):
    state = State(tmp_path / "s.db")
    uploader = FakeUploader()
    _run(state, FakeFetcher(pages=[[_rec("3"), _rec("2"), _rec("1")]]), uploader,
         tmp_path, limit=2)
    assert uploader.uploaded == ["1", "2"]


def test_limit_zero_processes_nothing(tmp_path):
    state = State(tmp_path / "s.db")
    uploader = FakeUploader()
    _run(state, FakeFetcher(pages=[[_rec("3"), _rec("2"), _rec("1")]]), uploader,
         tmp_path, limit=0)
    assert uploader.uploaded == []
    assert len(state.next_batch(10)) == 3  # 队列保持不动，全部仍 pending


def test_images_detail_routes_as_album(tmp_path):
    """work.aweme_type（列表页判型）为 video，但详情页实际是图集时，
    上传分支必须按详情页的 media.kind 走，不能被列表页的旧判型带偏。"""
    state = State(tmp_path / "s.db")
    fetcher = FakeFetcher(
        pages=[[_rec("1")]],
        detail_overrides={"1": {"video_play_addr": None, "images": ["http://i/1.jpg"]}},
    )
    uploader = FakeUploader()

    async def fake_download_images(media, aweme_id, tmp_dir, headers, client=None,
                                   heartbeat=None, max_bytes=None):
        path = Path(tmp_dir) / f"{aweme_id}_0.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return [path]

    _run(state, fetcher, uploader, tmp_path, download_fn=fake_download_images)
    assert uploader.kinds == [("1", "images")]
    assert uploader.uploaded == ["1"]


def test_alert_failure_during_cooldown_does_not_crash(tmp_path):
    """冷却告警若因 Telethon 解析 entity 失败而抛异常，不应中断 run_tick，
    且冷却状态应已经写入（写库先于告警发送）。"""
    state = State(tmp_path / "s.db")
    notifier = FakeNotifier(fail_alert=True)
    _run(state, FakeFetcher(fail_fetch=True), FakeUploader(), tmp_path, notifier=notifier)
    assert state.in_cooldown()
    assert notifier.alerts  # 确实尝试过发送


def test_hung_work_times_out_and_continues(tmp_path, monkeypatch):
    """网络卡死（如代理断连后 Telethon 永久等待、期间无任何心跳）应被
    无进展看门狗打断，记为一次失败后继续批次，而不是无限挂起。"""
    import main as main_mod

    monkeypatch.setattr(main_mod, "STALL_TIMEOUT", 0.05)
    monkeypatch.setattr(main_mod, "_WATCH_INTERVAL", 0.01)

    async def hanging_download(media, aweme_id, tmp_dir, headers, client=None,
                               heartbeat=None, max_bytes=None):
        if aweme_id == "1":
            await asyncio.sleep(3600)
        return await _fake_download(media, aweme_id, tmp_dir, headers, client)

    state = State(tmp_path / "s.db")
    uploader = FakeUploader()
    _run(state, FakeFetcher(pages=[[_rec("2"), _rec("1")]]), uploader, tmp_path,
         download_fn=hanging_download)
    assert uploader.uploaded == ["2"]  # "1" 卡死被打断，"2" 正常继续
    pending = state.next_batch(10)
    assert [w.aweme_id for w in pending] == ["1"]
    assert pending[0].retries == 1  # 记为一次普通失败，等待重试


def test_slow_but_progressing_work_not_killed(tmp_path, monkeypatch):
    """大文件传输总时长可远超看门狗阈值，只要心跳持续就不能被打断。"""
    import main as main_mod

    monkeypatch.setattr(main_mod, "STALL_TIMEOUT", 0.1)
    monkeypatch.setattr(main_mod, "_WATCH_INTERVAL", 0.02)

    async def slow_download(media, aweme_id, tmp_dir, headers, client=None,
                            heartbeat=None, max_bytes=None):
        for _ in range(10):  # 总耗时 0.3s > STALL_TIMEOUT，但每块都有心跳
            await asyncio.sleep(0.03)
            if heartbeat:
                heartbeat()
        return await _fake_download(media, aweme_id, tmp_dir, headers, client)

    state = State(tmp_path / "s.db")
    uploader = FakeUploader()
    _run(state, FakeFetcher(pages=[[_rec("1")]]), uploader, tmp_path,
         download_fn=slow_download)
    assert uploader.uploaded == ["1"]
    assert state.next_batch(10) == []


def _run_backfill(state, fetcher, uploader, tmp_path, limit=None, notifier=None):
    notifier = notifier or FakeNotifier()
    asyncio.run(
        run_backfill(
            _cfg(), state, fetcher, uploader, notifier,
            tmp_dir=tmp_path / "tmp", sleep_fn=_noop_sleep,
            download_fn=_fake_download, limit=limit,
        )
    )
    return notifier


def test_backfill_streams_newest_first_and_marks_done(tmp_path):
    state = State(tmp_path / "s.db")
    fetcher = FakeFetcher(resumable_pages=[
        (200, True, [_rec("4"), _rec("3")]),   # 最新页
        (100, False, [_rec("2"), _rec("1")]),  # 最早页
    ])
    uploader = FakeUploader()
    _run_backfill(state, fetcher, uploader, tmp_path)
    assert uploader.uploaded == ["4", "3", "2", "1"]  # 边翻边传，新→旧
    assert state.get_meta("backfill_done") == "1"
    assert state.next_batch(10) == []


def test_backfill_limit_saves_cursor_and_resumes(tmp_path):
    state = State(tmp_path / "s.db")
    pages = [
        (200, True, [_rec("4"), _rec("3")]),
        (100, False, [_rec("2"), _rec("1")]),
    ]
    uploader = FakeUploader()
    # 第一次只处理 3 条：第 1 页整页 + 第 2 页的 "2"，中断在第 2 页内
    _run_backfill(state, FakeFetcher(resumable_pages=pages), uploader, tmp_path, limit=3)
    assert uploader.uploaded == ["4", "3", "2"]
    assert state.get_meta("backfill_cursor") == "200"  # 只落了已完成页的断点
    assert state.get_meta("backfill_done") is None

    # 断点重跑：从游标 200 继续，已上传的 "2" 被跳过，仅补 "1"
    fetcher2 = FakeFetcher(resumable_pages=pages)
    uploader2 = FakeUploader()
    _run_backfill(state, fetcher2, uploader2, tmp_path)
    assert fetcher2.start_cursors == [200]
    assert uploader2.uploaded == ["1"]
    assert state.get_meta("backfill_done") == "1"


def test_backfill_done_short_circuits(tmp_path):
    state = State(tmp_path / "s.db")
    state.set_meta("backfill_done", "1")
    fetcher = FakeFetcher(resumable_pages=[(100, False, [_rec("1")])])
    uploader = FakeUploader()
    _run_backfill(state, fetcher, uploader, tmp_path)
    assert uploader.uploaded == []
    assert fetcher.start_cursors == []  # 未发起任何翻页


def test_backfill_auth_error_sets_cooldown_and_alerts(tmp_path):
    state = State(tmp_path / "s.db")
    notifier = _run_backfill(state, FakeFetcher(fail_fetch=True), FakeUploader(), tmp_path)
    assert state.in_cooldown()
    assert notifier.alerts


def test_failed_alert_failure_continues_batch(tmp_path):
    """failed 告警发送失败时，批次内其余作品应继续处理，且失败作品仍降级为链接卡片。"""
    state = State(tmp_path / "s.db")
    state.add_works([_rec("2"), _rec("1")])  # "1" 更旧，sort_key 更小，先处理
    state.mark_failed("1", "boom")
    state.mark_failed("1", "boom")  # retries=2，本次将是第 3 次，转 failed

    fetcher = FakeFetcher(pages=[], fail_detail_ids={"1"})
    uploader = FakeUploader()
    notifier = FakeNotifier(fail_alert=True)

    _run(state, fetcher, uploader, tmp_path, notifier=notifier)

    assert state.next_batch(10) == []  # 两条都已终态处理完
    assert uploader.uploaded == ["2"]  # 批次继续处理到了第二条
    assert uploader.link_cards == ["1"]  # F5：失败降级为链接卡片
