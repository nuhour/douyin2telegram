# douyin2telegram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把用户自己抖音账号的点赞视频定期自动同步到 Telegram 频道（完整无水印视频文件，图集发相册）。

**Architecture:** 本机 Mac 上的单个 Python 项目，launchd 每小时唤起 `main.py` 执行一个"同步 tick"：f2 拉喜欢列表增量入库 SQLite → 按点赞正序取 pending 批次 → 逐条 `fetch_one_video` 取新鲜地址 → httpx 流式下载 → Telethon(MTProto) 上传频道 → 更新状态。Cookie 失效时冷却 6 小时并私聊告警。

**Tech Stack:** Python 3.10+（本机为 3.10.19）、f2（抖音接口+签名）、Telethon（Bot Token 走 MTProto，上传上限 2GB）、hachoir（Telethon 自动提取视频元数据）、PyYAML、httpx、SQLite（标准库 sqlite3）、pytest。

**Spec:** `docs/superpowers/specs/2026-07-26-douyin2telegram-design.md`

## Global Constraints

- Python 3.10+（本机为 3.10.19，f2 最低要求 3.10）；依赖管理用 venv + pip + requirements.txt（用户明确要求 pip）
- `config.yaml`、`data/`（含 state.db、*.session、临时文件、日志）绝不入 git
- 限速默认值：单 tick 处理 20 条；每条间隔随机 5~15 秒；Cookie 失效冷却 6 小时
- 发送顺序：按点赞时间正序（旧→新）
- 单条失败重试 3 次后标记 `failed`；>1.9GB 或无法下载降级为链接卡片
- Telegram caption 上限 1024 字符，超出截断
- 测试统一用 pytest；异步代码在测试里用 `asyncio.run(...)` 包装（不引入 pytest-asyncio）
- 所有代码内注释与用户可见文案用简体中文

## File Structure

```
douyin2telegram/
├── requirements.txt
├── .gitignore
├── config.example.yaml        # 模板；用户复制为 config.yaml 填写
├── main.py                    # CLI 入口 + tick 编排
├── d2t/
│   ├── __init__.py
│   ├── models.py              # Work/Media 数据类 + 自定义异常
│   ├── config.py              # 配置加载与校验
│   ├── state.py               # SQLite 状态库
│   ├── fetcher.py             # f2 封装：喜欢列表分页、单作品详情、增量收集
│   ├── downloader.py          # 媒体地址提取 + httpx 流式下载
│   ├── uploader.py            # Telethon 上传（视频/相册/链接卡片）
│   └── notifier.py            # 告警文案 + 私聊发送
├── scripts/
│   ├── inspect_aweme.py       # 联调用：打印真实作品字段，验证 extract_media 假设
│   └── com.douyin2telegram.sync.plist   # launchd 模板
├── tests/
│   ├── test_config.py
│   ├── test_state.py
│   ├── test_fetcher.py
│   ├── test_downloader.py
│   ├── test_uploader.py
│   └── test_main.py
└── data/                      # 运行时生成（gitignore）
```

---

### Task 1: 项目骨架 + 配置加载

**Files:**
- Create: `.gitignore`, `requirements.txt`, `config.example.yaml`, `d2t/__init__.py`, `d2t/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `load_config(path: Path) -> Config`；`Config` 含 `douyin.cookie: str`、`douyin.profile_url: str`、`telegram.api_id: int`、`telegram.api_hash: str`、`telegram.bot_token: str`、`telegram.channel: str|int`、`telegram.alert_chat_id: int`、`sync.batch_size: int`、`sync.sleep_min: float`、`sync.sleep_max: float`、`sync.cooldown_hours: float`；缺失必填项抛 `ConfigError("缺少配置项: xxx")`

- [ ] **Step 1: 写基础文件**

`.gitignore`:
```
venv/
__pycache__/
*.pyc
config.yaml
data/
*.session
*.session-journal
.pytest_cache/
```

`requirements.txt`:
```
f2>=0.0.1.7
telethon>=1.36
hachoir>=3.3
PyYAML>=6.0
httpx>=0.27
pytest>=8.0
```

`config.example.yaml`:
```yaml
douyin:
  # 浏览器登录 www.douyin.com 后，从开发者工具复制完整 Cookie
  cookie: "PASTE_YOUR_COOKIE_HERE"
  # 你自己的抖音主页链接
  profile_url: "https://www.douyin.com/user/MS4wLjABAAAAxxxxxxxx"

telegram:
  # https://my.telegram.org -> API development tools 申请
  api_id: 123456
  api_hash: "your_api_hash"
  # @BotFather 创建 Bot 获得
  bot_token: "123456:ABC-xxxx"
  # 频道 @用户名，或 -100 开头的频道数字 ID（Bot 须为频道管理员）
  channel: "@your_channel"
  # 你自己的用户 chat_id（先给 Bot 发一次 /start；可用 @userinfobot 查询自己的 ID）
  alert_chat_id: 123456789

sync:
  batch_size: 20        # 单次 tick 最多处理条数
  sleep_min: 5          # 每条之间最小间隔（秒）
  sleep_max: 15         # 每条之间最大间隔（秒）
  cooldown_hours: 6     # Cookie 失效后的冷却时长（小时）
```

`d2t/__init__.py` 留空。

- [ ] **Step 2: 写失败测试** `tests/test_config.py`

```python
from pathlib import Path

import pytest

from d2t.config import ConfigError, load_config

VALID = """
douyin:
  cookie: "abc=1"
  profile_url: "https://www.douyin.com/user/MS4wLjAB"
telegram:
  api_id: 123
  api_hash: "hash"
  bot_token: "1:tok"
  channel: "@ch"
  alert_chat_id: 42
sync:
  batch_size: 10
  sleep_min: 1
  sleep_max: 2
  cooldown_hours: 6
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.douyin.cookie == "abc=1"
    assert cfg.telegram.api_id == 123
    assert cfg.telegram.channel == "@ch"
    assert cfg.sync.batch_size == 10


def test_missing_key_raises(tmp_path):
    broken = VALID.replace('  cookie: "abc=1"\n', "")
    with pytest.raises(ConfigError, match="douyin.cookie"):
        load_config(_write(tmp_path, broken))


def test_sync_defaults(tmp_path):
    no_sync = VALID.split("sync:")[0]
    cfg = load_config(_write(tmp_path, no_sync))
    assert cfg.sync.batch_size == 20
    assert cfg.sync.cooldown_hours == 6
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'd2t.config'`）

- [ ] **Step 4: 实现** `d2t/config.py`

```python
"""配置加载与校验。"""

from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class DouyinConfig:
    cookie: str
    profile_url: str


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    bot_token: str
    channel: str | int
    alert_chat_id: int


@dataclass
class SyncConfig:
    batch_size: int = 20
    sleep_min: float = 5
    sleep_max: float = 15
    cooldown_hours: float = 6


@dataclass
class Config:
    douyin: DouyinConfig
    telegram: TelegramConfig
    sync: SyncConfig


def _require(data: dict, section: str, key: str):
    value = (data.get(section) or {}).get(key)
    if value in (None, ""):
        raise ConfigError(f"缺少配置项: {section}.{key}")
    return value


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}（请复制 config.example.yaml 为 config.yaml 并填写）")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    douyin = DouyinConfig(
        cookie=_require(data, "douyin", "cookie"),
        profile_url=_require(data, "douyin", "profile_url"),
    )
    telegram = TelegramConfig(
        api_id=int(_require(data, "telegram", "api_id")),
        api_hash=_require(data, "telegram", "api_hash"),
        bot_token=_require(data, "telegram", "bot_token"),
        channel=_require(data, "telegram", "channel"),
        alert_chat_id=int(_require(data, "telegram", "alert_chat_id")),
    )
    sync = SyncConfig(**(data.get("sync") or {}))
    return Config(douyin=douyin, telegram=telegram, sync=sync)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 6: 提交**

```bash
git add .gitignore requirements.txt config.example.yaml d2t/ tests/test_config.py
git commit -m "feat: 项目骨架与配置加载"
```

---

### Task 2: 数据模型 + SQLite 状态库

**Files:**
- Create: `d2t/models.py`, `d2t/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces（models.py）:
  - `@dataclass Work: aweme_id: str; aweme_type: str  # "video"|"images"; title: str; author: str; status: str; retries: int`
  - `@dataclass Media: kind: str  # "video"|"images"; urls: list[str]`
  - 异常：`DouyinAuthError(Exception)`、`OversizeError(Exception)`
- Produces（state.py）: `State` 类，方法见 Step 3 实现；record 字典形如 `{"aweme_id": str, "aweme_type": str, "title": str, "author": str}`，`add_works` 的入参列表按**新→旧**排列

- [ ] **Step 1: 写** `d2t/models.py`

```python
"""共享数据类与异常。"""

from dataclasses import dataclass


@dataclass
class Work:
    aweme_id: str
    aweme_type: str  # "video" | "images"
    title: str
    author: str
    status: str = "pending"
    retries: int = 0

    @property
    def url(self) -> str:
        kind = "note" if self.aweme_type == "images" else "video"
        return f"https://www.douyin.com/{kind}/{self.aweme_id}"


@dataclass
class Media:
    kind: str  # "video" | "images"
    urls: list


class DouyinAuthError(Exception):
    """Cookie 失效或触发风控。"""


class OversizeError(Exception):
    """文件超出 Telegram 上传上限。"""
```

- [ ] **Step 2: 写失败测试** `tests/test_state.py`

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_state.py -v`
Expected: FAIL（`No module named 'd2t.state'`）

- [ ] **Step 4: 实现** `d2t/state.py`

```python
"""SQLite 状态库：作品去重、处理队列、冷却标记。"""

import sqlite3
import time
from pathlib import Path

from d2t.models import Work

MAX_RETRIES = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    aweme_id   TEXT PRIMARY KEY,
    sort_key   INTEGER NOT NULL,
    aweme_type TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    retries    INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)

    def add_works(self, records: list[dict]) -> int:
        """入库新作品。records 按新→旧排列；旧的分配更小的 sort_key。"""
        row = self.conn.execute("SELECT COALESCE(MAX(sort_key), -1) FROM works").fetchone()
        next_key = row[0] + 1
        inserted = 0
        for rec in reversed(records):  # 旧→新依次分配递增 sort_key
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO works (aweme_id, sort_key, aweme_type, title, author, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rec["aweme_id"], next_key, rec["aweme_type"],
                 rec.get("title", ""), rec.get("author", ""), time.time()),
            )
            if cur.rowcount:
                inserted += 1
                next_key += 1
        self.conn.commit()
        return inserted

    def is_known(self, aweme_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM works WHERE aweme_id = ?", (aweme_id,)
        ).fetchone() is not None

    def next_batch(self, limit: int) -> list[Work]:
        rows = self.conn.execute(
            "SELECT aweme_id, aweme_type, title, author, status, retries FROM works"
            " WHERE status = 'pending' ORDER BY sort_key ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Work(*row) for row in rows]

    def _set(self, aweme_id: str, status: str, error: str | None = None):
        self.conn.execute(
            "UPDATE works SET status = ?, error = ?, updated_at = ? WHERE aweme_id = ?",
            (status, error, time.time(), aweme_id),
        )
        self.conn.commit()

    def mark_uploaded(self, aweme_id: str):
        self._set(aweme_id, "uploaded")

    def mark_skipped(self, aweme_id: str, reason: str):
        self._set(aweme_id, "skipped", reason)

    def mark_failed(self, aweme_id: str, error: str) -> str:
        """累计重试次数，达到 MAX_RETRIES 转 failed。返回最新状态。"""
        self.conn.execute(
            "UPDATE works SET retries = retries + 1, error = ?, updated_at = ? WHERE aweme_id = ?",
            (error, time.time(), aweme_id),
        )
        retries = self.conn.execute(
            "SELECT retries FROM works WHERE aweme_id = ?", (aweme_id,)
        ).fetchone()[0]
        status = "failed" if retries >= MAX_RETRIES else "pending"
        if status == "failed":
            self._set(aweme_id, "failed", error)
        else:
            self.conn.commit()
        return status

    def reset_failed(self) -> int:
        cur = self.conn.execute(
            "UPDATE works SET status = 'pending', retries = 0, updated_at = ? WHERE status = 'failed'",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount

    def set_cooldown(self, until_ts: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('cooldown_until', ?)",
            (str(until_ts),),
        )
        self.conn.commit()

    def in_cooldown(self) -> bool:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'cooldown_until'"
        ).fetchone()
        return bool(row) and float(row[0]) > time.time()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_state.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add d2t/models.py d2t/state.py tests/test_state.py
git commit -m "feat: 数据模型与 SQLite 状态库"
```

---

### Task 3: fetcher —— 喜欢列表拉取与增量收集

**Files:**
- Create: `d2t/fetcher.py`
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Consumes: `Config`（Task 1）、`DouyinAuthError`（Task 2）
- Produces:
  - `async collect_new(pages, is_known, stop_after=3) -> list[dict]`：`pages` 为异步生成器（每项是一页规范化 record 列表，页内新→旧）；`is_known(aweme_id)->bool`；遇连续 `stop_after` 条已知即停；返回新 record 列表（新→旧）
  - `class DouyinFetcher(cfg: Config)`：`http_headers: dict`（含 UA/Referer/Cookie，供下载用）；`async resolve_sec_user_id() -> str`；`fetch_like_pages(sec_user_id) -> AsyncGenerator[list[dict]]`（首页为空抛 `DouyinAuthError`）；`async fetch_detail(aweme_id) -> dict`
  - record 字典：`{"aweme_id": str, "aweme_type": "video"|"images", "title": str, "author": str}`

- [ ] **Step 1: 写失败测试** `tests/test_fetcher.py`

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: FAIL（`No module named 'd2t.fetcher'`）

- [ ] **Step 3: 实现** `d2t/fetcher.py`

```python
"""抖音侧封装：基于 f2 拉喜欢列表、单作品详情。"""

from f2.apps.douyin.handler import DouyinHandler
from f2.apps.douyin.utils import SecUserIdFetcher

from d2t.config import Config
from d2t.models import DouyinAuthError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)


def _normalize(rec: dict) -> dict:
    return {
        "aweme_id": str(rec["aweme_id"]),
        "aweme_type": "images" if rec.get("images") else "video",
        "title": rec.get("desc") or "",
        "author": rec.get("nickname") or "",
    }


async def collect_new(pages, is_known, stop_after: int = 3) -> list[dict]:
    """遍历喜欢列表页（新→旧），遇连续 stop_after 条已知作品即停。

    连续判停而非单条判停：用户中途取消点赞会让单条判停漏掉更旧的新增。
    """
    new, consecutive = [], 0
    async for page in pages:
        for rec in page:
            if is_known(rec["aweme_id"]):
                consecutive += 1
                if consecutive >= stop_after:
                    return new
            else:
                consecutive = 0
                new.append(rec)
    return new


class DouyinFetcher:
    def __init__(self, cfg: Config):
        self.profile_url = cfg.douyin.profile_url
        self.http_headers = {
            "User-Agent": _UA,
            "Referer": "https://www.douyin.com/",
            "Cookie": cfg.douyin.cookie,
        }
        self.kwargs = {
            "headers": {"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
            "proxies": {"http://": None, "https://": None},
            "timeout": 15,
            "cookie": cfg.douyin.cookie,
            "mode": "like",
        }
        self.handler = DouyinHandler(self.kwargs)

    async def resolve_sec_user_id(self) -> str:
        return await SecUserIdFetcher.get_sec_user_id(self.profile_url)

    async def fetch_like_pages(self, sec_user_id: str):
        """逐页 yield 规范化 record 列表（页内新→旧）。首页为空视为 Cookie 失效。"""
        first_page = True
        async for aweme_list in self.handler.fetch_user_like_videos(
            sec_user_id, 0, 20, None
        ):
            records = aweme_list._to_list()
            if first_page and not records:
                raise DouyinAuthError("喜欢列表首页为空，Cookie 可能已失效或触发风控")
            first_page = False
            if not records:
                return
            yield [_normalize(r) for r in records]

    async def fetch_detail(self, aweme_id: str) -> dict:
        video = await self.handler.fetch_one_video(aweme_id=aweme_id)
        return video._to_dict()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: 4 passed（注意：测试只覆盖纯逻辑，不发真实请求；f2 的 import 需要已 `pip install -r requirements.txt`）

- [ ] **Step 5: 提交**

```bash
git add d2t/fetcher.py tests/test_fetcher.py
git commit -m "feat: 喜欢列表增量收集与 f2 封装"
```

---

### Task 4: downloader —— 媒体提取与流式下载

**Files:**
- Create: `d2t/downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: `Media`（Task 2）
- Produces:
  - `extract_media(detail: dict) -> Media`：从 `fetch_detail` 返回的字典提取播放地址；无可用地址抛 `ValueError`
  - `async download_media(media: Media, aweme_id: str, tmp_dir: Path, headers: dict, client=None) -> list[Path]`：视频存为 `{aweme_id}.mp4`，图集存为 `{aweme_id}_{i}.jpg`；`client` 参数允许测试注入 mock httpx.AsyncClient

- [ ] **Step 1: 写失败测试** `tests/test_downloader.py`

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_downloader.py -v`
Expected: FAIL（`No module named 'd2t.downloader'`）

- [ ] **Step 3: 实现** `d2t/downloader.py`

```python
"""从作品详情提取媒体地址，httpx 流式下载到临时目录。"""

from pathlib import Path

import httpx

from d2t.models import Media


def _first_url(value) -> str | None:
    """兼容 str / list[str] / list[{"url_list": [...]}] 三种形态。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            urls = item.get("url_list") or []
            return urls[0] if urls else None
    return None


def _all_urls(value) -> list[str]:
    if not isinstance(value, list):
        return []
    urls = []
    for item in value:
        url = _first_url([item]) if not isinstance(item, str) else item
        if url:
            urls.append(url)
    return urls


def extract_media(detail: dict) -> Media:
    images = _all_urls(detail.get("images"))
    if images:
        return Media(kind="images", urls=images)
    video_url = _first_url(detail.get("video_play_addr"))
    if video_url:
        return Media(kind="video", urls=[video_url])
    raise ValueError("作品详情中没有可用的媒体地址")


async def download_media(
    media: Media, aweme_id: str, tmp_dir: Path, headers: dict, client=None
) -> list[Path]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.AsyncClient(
        headers=headers, timeout=60, follow_redirects=True
    )
    files = []
    try:
        for i, url in enumerate(media.urls):
            if media.kind == "video":
                path = tmp_dir / f"{aweme_id}.mp4"
            else:
                path = tmp_dir / f"{aweme_id}_{i}.jpg"
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 256):
                        fh.write(chunk)
            files.append(path)
        return files
    finally:
        if own_client:
            await client.aclose()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_downloader.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add d2t/downloader.py tests/test_downloader.py
git commit -m "feat: 媒体地址提取与流式下载"
```

---

### Task 5: uploader —— Telethon 上传

**Files:**
- Create: `d2t/uploader.py`
- Test: `tests/test_uploader.py`

**Interfaces:**
- Consumes: `Work`、`Media`、`OversizeError`（Task 2）、`Config`（Task 1）
- Produces:
  - `build_caption(work: Work) -> str`：`标题\n\n👤 作者\n🔗 链接`，超 1024 字符时截断标题并加 `…`
  - `chunk10(items: list) -> list[list]`：按 10 个一组切分（Telegram 相册上限）
  - `class Uploader(cfg: Config, session_path: Path)`：`async start()`；`async close()`；`async upload_work(work: Work, files: list[Path])`（视频单发、图集按相册分组发，首组带 caption；总大小 >1.9GB 抛 `OversizeError`）；`async send_link_card(work: Work, reason: str)`；`async send_message(chat_id, text)`（notifier 复用）

- [ ] **Step 1: 写失败测试** `tests/test_uploader.py`

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_uploader.py -v`
Expected: FAIL（`No module named 'd2t.uploader'`）

- [ ] **Step 3: 实现** `d2t/uploader.py`

```python
"""Telethon 上传：Bot Token 走 MTProto，上传上限 2GB。"""

from pathlib import Path

from telethon import TelegramClient

from d2t.config import Config
from d2t.models import OversizeError, Work

MAX_BYTES = int(1.9 * 1024**3)  # 留出安全余量的 2GB 上限
CAPTION_LIMIT = 1024


def build_caption(work: Work) -> str:
    tail = f"\n\n👤 {work.author}\n🔗 {work.url}"
    title = work.title
    budget = CAPTION_LIMIT - len(tail)
    if len(title) > budget:
        title = title[: budget - 1] + "…"
    return f"{title}{tail}"


def chunk10(items: list) -> list[list]:
    return [items[i : i + 10] for i in range(0, len(items), 10)]


class Uploader:
    def __init__(self, cfg: Config, session_path: Path):
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.client = TelegramClient(
            str(session_path), cfg.telegram.api_id, cfg.telegram.api_hash
        )
        self.client.parse_mode = None  # caption 是任意文本，禁用 markdown 解析
        self.client.flood_sleep_threshold = 120

    async def start(self):
        await self.client.start(bot_token=self.cfg.telegram.bot_token)

    async def close(self):
        await self.client.disconnect()

    @property
    def channel(self):
        ch = self.cfg.telegram.channel
        return ch if isinstance(ch, str) and ch.startswith("@") else int(ch)

    async def upload_work(self, work: Work, files: list[Path]):
        total = sum(f.stat().st_size for f in files)
        if total > MAX_BYTES:
            raise OversizeError(f"文件共 {total / 1024**3:.2f}GB，超出上传上限")
        caption = build_caption(work)
        if work.aweme_type == "video":
            await self.client.send_file(
                self.channel, files[0], caption=caption, supports_streaming=True
            )
        else:
            for i, group in enumerate(chunk10(files)):
                await self.client.send_file(
                    self.channel, group, caption=caption if i == 0 else None
                )

    async def send_link_card(self, work: Work, reason: str):
        text = f"⚠️ 无法上传完整文件（{reason}）\n\n{build_caption(work)}"
        await self.client.send_message(self.channel, text[:4096])

    async def send_message(self, chat_id, text: str):
        await self.client.send_message(chat_id, text[:4096])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_uploader.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add d2t/uploader.py tests/test_uploader.py
git commit -m "feat: Telethon 上传模块"
```

---

### Task 6: notifier —— 告警

**Files:**
- Create: `d2t/notifier.py`
- Test: 并入 `tests/test_main.py`（Task 7 用 fake 覆盖调用路径；文案函数简单，不单独建测试文件）

**Interfaces:**
- Consumes: `Uploader.send_message`（Task 5）、`Work`（Task 2）
- Produces: `class Notifier(uploader, alert_chat_id: int)`：`async alert(text: str)`；`cookie_invalid_text(err: str) -> str`；`work_failed_text(work: Work, err: str) -> str`（两个文案函数为模块级纯函数）

- [ ] **Step 1: 实现** `d2t/notifier.py`

```python
"""通过 Bot 私聊发送告警。"""

from d2t.models import Work


def cookie_invalid_text(err: str) -> str:
    return (
        "🚨 douyin2telegram：拉取喜欢列表失败\n\n"
        f"原因：{err}\n\n"
        "请重新登录 www.douyin.com 复制 Cookie 更新到 config.yaml。\n"
        "已进入冷却，期间不再重试。"
    )


def work_failed_text(work: Work, err: str) -> str:
    return (
        "⚠️ douyin2telegram：作品同步失败（已重试 3 次，跳过）\n\n"
        f"{work.title or work.aweme_id}\n🔗 {work.url}\n原因：{err}\n\n"
        "修复后可运行 python main.py --reset-failed 重试。"
    )


class Notifier:
    def __init__(self, uploader, alert_chat_id: int):
        self.uploader = uploader
        self.alert_chat_id = alert_chat_id

    async def alert(self, text: str):
        await self.uploader.send_message(self.alert_chat_id, text)
```

- [ ] **Step 2: 提交**

```bash
git add d2t/notifier.py
git commit -m "feat: 告警通知模块"
```

---

### Task 7: main —— tick 编排与 CLI

**Files:**
- Create: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: 前述全部模块
- Produces: `async run_tick(cfg, state, fetcher, uploader, notifier, tmp_dir, sleep_fn, limit=None)`（可注入 fake 依赖）；CLI：`python main.py`（一次 tick）、`--limit N`（联调限量）、`--reset-failed`、`--config PATH`

- [ ] **Step 1: 写失败测试** `tests/test_main.py`

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL（`No module named 'main'` 或 `cannot import name 'run_tick'`）

- [ ] **Step 3: 实现** `main.py`

```python
"""douyin2telegram 入口：单次同步 tick。由 launchd 定时调用。"""

import argparse
import asyncio
import fcntl
import random
import shutil
import sys
import time
from pathlib import Path

from d2t.config import load_config
from d2t.downloader import download_media, extract_media
from d2t.fetcher import DouyinFetcher, collect_new
from d2t.models import OversizeError
from d2t.notifier import Notifier, cookie_invalid_text, work_failed_text
from d2t.state import State
from d2t.uploader import Uploader

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


async def run_tick(cfg, state, fetcher, uploader, notifier, tmp_dir: Path,
                   sleep_fn=asyncio.sleep, download_fn=download_media, limit=None):
    if state.in_cooldown():
        print("处于冷却期，跳过本次同步")
        return

    # 阶段一：增量拉取喜欢列表
    try:
        sec_uid = await fetcher.resolve_sec_user_id()
        new_records = await collect_new(
            fetcher.fetch_like_pages(sec_uid), state.is_known
        )
        added = state.add_works(new_records)
        print(f"新增 {added} 条待同步作品")
    except Exception as e:  # Cookie 失效/风控/接口变动都走同一冷却路径
        state.set_cooldown(time.time() + cfg.sync.cooldown_hours * 3600)
        await notifier.alert(cookie_invalid_text(str(e)))
        return

    # 阶段二：处理待同步队列（点赞正序）
    batch = state.next_batch(limit or cfg.sync.batch_size)
    for work in batch:
        files = []
        try:
            detail = await fetcher.fetch_detail(work.aweme_id)
            media = extract_media(detail)
            files = await download_fn(media, work.aweme_id, tmp_dir, fetcher.http_headers)
            await uploader.upload_work(work, files)
            state.mark_uploaded(work.aweme_id)
            print(f"已同步 {work.aweme_id}")
        except OversizeError as e:
            await uploader.send_link_card(work, str(e))
            state.mark_skipped(work.aweme_id, str(e))
        except Exception as e:
            status = state.mark_failed(work.aweme_id, str(e))
            print(f"处理失败 {work.aweme_id}: {e}", file=sys.stderr)
            if status == "failed":
                await notifier.alert(work_failed_text(work, str(e)))
        finally:
            for f in files:
                f.unlink(missing_ok=True)
        await sleep_fn(random.uniform(cfg.sync.sleep_min, cfg.sync.sleep_max))


async def main_async(args):
    cfg = load_config(Path(args.config))
    state = State(DATA / "state.db")

    if args.reset_failed:
        print(f"已重置 {state.reset_failed()} 条失败作品")
        return

    fetcher = DouyinFetcher(cfg)
    uploader = Uploader(cfg, DATA / "bot.session")
    await uploader.start()
    notifier = Notifier(uploader, cfg.telegram.alert_chat_id)
    tmp_dir = DATA / "tmp"
    try:
        await run_tick(cfg, state, fetcher, uploader, notifier, tmp_dir, limit=args.limit)
    finally:
        await uploader.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="抖音点赞同步到 Telegram 频道")
    parser.add_argument("--config", default=str(BASE / "config.yaml"))
    parser.add_argument("--limit", type=int, default=None, help="本次最多处理条数（联调用）")
    parser.add_argument("--reset-failed", action="store_true", help="重置失败作品后退出")
    args = parser.parse_args()

    DATA.mkdir(exist_ok=True)
    lock = (DATA / "sync.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("已有同步进程在运行，退出")
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑全部测试确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部 passed（约 25 个）

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_main.py
git commit -m "feat: tick 编排与 CLI 入口"
```

---

### Task 8: 部署文件 + 联调（需要用户配合）

**Files:**
- Create: `scripts/inspect_aweme.py`, `scripts/com.douyin2telegram.sync.plist`, `README.md`

**Interfaces:**
- Consumes: 全部模块

- [ ] **Step 1: 写字段核验脚本** `scripts/inspect_aweme.py`

```python
"""联调工具：打印真实作品详情的字段，核验 extract_media 的键名假设。

用法: python scripts/inspect_aweme.py <aweme_id 或作品链接>
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d2t.config import load_config
from d2t.downloader import extract_media
from d2t.fetcher import DouyinFetcher


async def main():
    target = sys.argv[1]
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    fetcher = DouyinFetcher(cfg)

    if target.startswith("http"):
        from f2.apps.douyin.utils import AwemeIdFetcher

        target = await AwemeIdFetcher.get_aweme_id(target)

    detail = await fetcher.fetch_detail(target)
    print("=== 详情字段与取值类型 ===")
    for key, value in sorted(detail.items()):
        preview = repr(value)[:80]
        print(f"{key:30s} {type(value).__name__:8s} {preview}")
    print("\n=== extract_media 结果 ===")
    print(extract_media(detail))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 写 launchd 模板** `scripts/com.douyin2telegram.sync.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.douyin2telegram.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram/venv/bin/python</string>
        <string>/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram</string>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram/data/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram/data/launchd.err.log</string>
</dict>
</plist>
```

- [ ] **Step 3: 写 README.md**（覆盖首次提交的占位 README）

内容包含：项目一句话说明；环境搭建（`python3 -m venv venv`、`venv/bin/pip install -r requirements.txt`）；一次性准备四步（① my.telegram.org 申请 api_id/api_hash ② BotFather 建 Bot、拉进频道当管理员、给 Bot 发 /start、@userinfobot 查自己 chat_id ③ 登录 douyin.com 复制 Cookie ④ `cp config.example.yaml config.yaml` 填写）；联调命令（`venv/bin/python scripts/inspect_aweme.py <链接>`、`venv/bin/python main.py --limit 2`）；部署命令（复制 plist 到 `~/Library/LaunchAgents/` 后 `launchctl load ~/Library/LaunchAgents/com.douyin2telegram.sync.plist`）；日常运维（Cookie 失效收到私聊告警后更新 config.yaml、`--reset-failed` 重试失败作品、日志位置 `data/launchd.log`）。

- [ ] **Step 4: 提交**

```bash
git add scripts/ README.md
git commit -m "docs: 部署脚本与使用说明"
```

- [ ] **Step 5: 联调（需要用户提供配置，无法自动完成）**

按 README 完成一次性准备后，依次执行并核验：

1. `venv/bin/python scripts/inspect_aweme.py <任一你点赞过的作品链接>`
   - 核验点：`aweme_id`、`desc`、`nickname`、`video_play_addr`、`images` 键名与假设一致，`extract_media` 输出正常
   - 若键名不一致：只需修改 `d2t/downloader.py` 的 `extract_media`/`_first_url` 与 `d2t/fetcher.py` 的 `_normalize`，改后重跑 `python -m pytest tests/`
2. `venv/bin/python main.py --limit 2`
   - 核验点：频道里出现 2 条最早点赞的视频（含 caption），私聊无告警，`data/state.db` 中对应记录状态为 uploaded
3. 确认无误后放开：`venv/bin/python main.py`（跑满一个 batch），观察限速与顺序
4. 部署 launchd 并确认下一个整点自动运行

- [ ] **Step 6: 最终提交**

```bash
git add -A
git commit -m "chore: 联调修正"
```

---

## Self-Review 记录

- **Spec 覆盖**：内容形式（视频+相册+超限降级）✓ Task 4/5；运行环境（launchd）✓ Task 8；全量补齐+增量（统一队列、连续 3 条判停）✓ Task 2/3；限速与正序 ✓ Task 2/7；Cookie 冷却与告警 ✓ Task 6/7；失败重试 3 次 ✓ Task 2/7；上传即删临时文件 ✓ Task 7；venv+pip ✓ Task 1/8。
- **占位符扫描**：无 TBD/TODO；README 内容以清单形式给出要点（Task 8 Step 3），执行者可直接展开成文。
- **类型一致性**：`Work`/`Media`/record 字典结构在 Task 2 定义后，Task 3/4/5/7 的用法与签名一致；`download_fn` 注入签名与 `download_media` 一致。
- **已知不确定点（已在计划内消化）**：f2 filter `_to_dict()/_to_list()` 的具体键名（`video_play_addr`/`images` 等）以联调 Step 5.1 的 inspect 脚本为准，兼容逻辑集中在 `extract_media`/`_normalize` 两处，改动面可控。
