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
    proxy: str | None = None  # 形如 socks5://127.0.0.1:7897，直连可用时留空
    max_upload_mb: float = 1900  # 单作品媒体总大小上限（MB），超过跳过上传、降级为链接卡片


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
        proxy=(data.get("telegram") or {}).get("proxy") or None,
        max_upload_mb=float((data.get("telegram") or {}).get("max_upload_mb") or 1900),
    )
    sync = SyncConfig(**(data.get("sync") or {}))
    return Config(douyin=douyin, telegram=telegram, sync=sync)
