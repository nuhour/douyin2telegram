import asyncio
from pathlib import Path

from d2t.config import Config, DouyinConfig, SyncConfig, TelegramConfig
from d2t.models import DouyinAuthError, OversizeError
from d2t.state import State
from main import run_tick


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
    def __init__(self, pages=None, fail_fetch=False, fail_detail=False):
        self.pages, self.fail_fetch, self.fail_detail = pages or [], fail_fetch, fail_detail
        self.http_headers = {}

    async def resolve_sec_user_id(self):
        if self.fail_fetch:
            raise DouyinAuthError("cookie 失效")
        return "sec_uid"

    async def fetch_like_pages(self, sec_user_id):
        for p in self.pages:
            yield p

    async def fetch_detail(self, aweme_id):
        if self.fail_detail:
            raise RuntimeError("视频已删除")
        return {"video_play_addr": f"http://v/{aweme_id}.mp4", "images": None}


class FakeUploader:
    def __init__(self, oversize=False):
        self.oversize = oversize
        self.uploaded, self.link_cards, self.messages = [], [], []

    async def upload_work(self, work, files):
        if self.oversize:
            raise OversizeError("太大")
        self.uploaded.append(work.aweme_id)

    async def send_link_card(self, work, reason):
        self.link_cards.append(work.aweme_id)

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class FakeNotifier:
    def __init__(self):
        self.alerts = []

    async def alert(self, text):
        self.alerts.append(text)


async def _noop_sleep(_):
    pass


async def _fake_download(media, aweme_id, tmp_dir, headers, client=None):
    path = Path(tmp_dir) / f"{aweme_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return [path]


def _run(state, fetcher, uploader, tmp_path, limit=None):
    notifier = FakeNotifier()
    asyncio.run(
        run_tick(
            _cfg(), state, fetcher, uploader, notifier,
            tmp_dir=tmp_path / "tmp", sleep_fn=_noop_sleep,
            download_fn=_fake_download, limit=limit,
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
    notifier = _run(state, fetcher, FakeUploader(), tmp_path)
    assert state.next_batch(10) == []  # 3 次后 failed
    assert any("重试 3 次" in t for t in notifier.alerts)


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
