"""Telethon 上传：Bot Token 走 MTProto，上传上限 2GB。"""

from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient

from d2t.config import Config
from d2t.models import OversizeError, Work

MAX_BYTES = int(1.9 * 1024**3)  # 留出安全余量的 2GB 上限
CAPTION_LIMIT = 1024


def _tg_len(s: str) -> int:
    """Telegram 客户端按 UTF-16 code unit 计数文本长度（而非 Python 的码点数）。

    BMP 之外的字符（如大多数 emoji）在 UTF-16 中占 2 个 code unit，
    此时 Python 的 len() 会比 Telegram 实际计数偏小，可能导致确定性截断失误。
    """
    return len(s.encode("utf-16-le")) // 2


def build_caption(work: Work) -> str:
    tail = f"\n\n👤 {work.author}\n🔗 {work.url}"
    title = work.title
    budget = CAPTION_LIMIT - _tg_len(tail)
    if _tg_len(title) > budget:
        if budget > 0:
            # 逐字符裁剪直到加上省略号（占 1 个 UTF-16 unit）后不超过 budget
            while title and _tg_len(title) + 1 > budget:
                title = title[:-1]
            title = title + "…"
        else:
            title = ""
    return f"{title}{tail}"


def chunk10(items: list) -> list[list]:
    return [items[i : i + 10] for i in range(0, len(items), 10)]


def _parse_proxy(url: str | None) -> dict | None:
    """把 socks5://host:port 形式的代理串转成 Telethon 的 proxy 参数。"""
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"telegram.proxy 格式不正确（应形如 socks5://127.0.0.1:7897）: {url}")
    return {
        "proxy_type": parsed.scheme or "socks5",
        "addr": parsed.hostname,
        "port": parsed.port,
        **({"username": parsed.username, "password": parsed.password}
           if parsed.username else {}),
    }


class Uploader:
    def __init__(self, cfg: Config, session_path: Path):
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.client = TelegramClient(
            str(session_path), cfg.telegram.api_id, cfg.telegram.api_hash,
            proxy=_parse_proxy(cfg.telegram.proxy),
        )
        self.client.parse_mode = None  # caption 是任意文本，禁用 markdown 解析
        self.client.flood_sleep_threshold = 120
        # 生效上限取配置与 Telegram 硬上限的较小者；下载阶段也用它提前掐断超限文件
        self.max_bytes = min(int(cfg.telegram.max_upload_mb * 1024**2), MAX_BYTES)

    async def start(self):
        await self.client.start(bot_token=self.cfg.telegram.bot_token)

    async def close(self):
        await self.client.disconnect()

    @property
    def channel(self):
        ch = self.cfg.telegram.channel
        return ch if isinstance(ch, str) and ch.startswith("@") else int(ch)

    async def upload_work(self, work: Work, files: list[Path], kind: str, progress=None):
        """kind 必须来自详情页判型（extract_media 的结果），而非入库时列表页的 work.aweme_type——
        两者可能分叉（例如列表页判为 video，详情页实际是图集），按 kind 分支才能保证不会把图集当视频发送。

        progress: Telethon 每上传一块数据回调一次，供上层的无进展看门狗使用。"""
        total = sum(f.stat().st_size for f in files)
        if total > self.max_bytes:
            raise OversizeError(
                f"文件共 {total / 1024**2:.1f}MB，超出上传上限 {self.max_bytes / 1024**2:.0f}MB"
            )
        caption = build_caption(work)
        if kind == "video":
            await self.client.send_file(
                self.channel, files[0], caption=caption, supports_streaming=True,
                progress_callback=progress,
            )
        else:
            for i, group in enumerate(chunk10(files)):
                await self.client.send_file(
                    self.channel, group, caption=caption if i == 0 else None,
                    supports_streaming=True,  # 图集内的 live photo 视频可流式播放
                    progress_callback=progress,
                )

    async def send_link_card(self, work: Work, reason: str):
        text = f"⚠️ 无法上传完整文件（{reason}）\n\n{build_caption(work)}"
        await self.client.send_message(self.channel, text[:4096])

    async def send_message(self, chat_id, text: str):
        await self.client.send_message(chat_id, text[:4096])
