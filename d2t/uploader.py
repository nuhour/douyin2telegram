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
        if budget > 0:
            title = title[: budget - 1] + "…"
        else:
            title = ""
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
