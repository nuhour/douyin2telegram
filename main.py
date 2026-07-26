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
