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
    # 图集逐项类型（"image" | "video"），live photo 项为 "video"；None 表示全静图
    item_kinds: list | None = None


class DouyinAuthError(Exception):
    """Cookie 失效或触发风控。"""


class OversizeError(Exception):
    """文件超出 Telegram 上传上限。"""
