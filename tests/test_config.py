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
