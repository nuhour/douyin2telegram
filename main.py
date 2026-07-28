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
from d2t.models import DouyinAuthError, OversizeError
from d2t.notifier import Notifier, cookie_invalid_text, work_failed_text
from d2t.state import State
from d2t.uploader import Uploader

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


# 无进展看门狗（秒）：超过该时长没有任何字节流动才判定卡死（代理断连时
# Telethon 会在死连接上永久等待）。大文件只要仍在传输就不会被打断。
STALL_TIMEOUT = 300
_WATCH_INTERVAL = 15


async def _process_work(state, fetcher, uploader, notifier, tmp_dir: Path,
                        work, download_fn=download_media):
    """处理单条作品：拉详情→下载→上传，按结果落状态。异常均在内部消化。"""
    files = []
    last_progress = [time.time()]

    def _beat(*_args):
        last_progress[0] = time.time()

    async def _do():
        nonlocal files
        detail = await fetcher.fetch_detail(work.aweme_id)
        _beat()
        media = extract_media(detail)
        files = await download_fn(media, work.aweme_id, tmp_dir, fetcher.http_headers,
                                  heartbeat=_beat,
                                  max_bytes=getattr(uploader, "max_bytes", None))
        _beat()
        await uploader.upload_work(work, files, media.kind, progress=_beat)

    try:
        task = asyncio.ensure_future(_do())
        while True:
            done, _ = await asyncio.wait({task}, timeout=_WATCH_INTERVAL)
            if done:
                task.result()  # 正常结束或重新抛出内部异常
                break
            if time.time() - last_progress[0] > STALL_TIMEOUT:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise TimeoutError(f"超过 {STALL_TIMEOUT} 秒无传输进展，判定连接卡死")
        state.mark_uploaded(work.aweme_id)
        print(f"已同步 {work.aweme_id}")
    except DouyinAuthError:
        raise
    except OversizeError as e:
        await uploader.send_link_card(work, str(e))
        state.mark_skipped(work.aweme_id, str(e))
    except Exception as e:
        err = str(e) or type(e).__name__  # TimeoutError 等异常 str() 为空，落类型名兜底
        status = state.mark_failed(work.aweme_id, err)
        print(f"处理失败 {work.aweme_id}: {err}", file=sys.stderr)
        if status == "failed":
            try:
                await notifier.alert(work_failed_text(work, err))
            except Exception as alert_err:  # 告警失败不应中断批次
                print(f"告警发送失败: {alert_err}", file=sys.stderr)
            try:
                # 多次重试仍失败，降级为链接卡片，避免作品彻底丢失
                await uploader.send_link_card(work, "多次尝试后失败，降级为链接")
            except Exception as card_err:
                print(f"降级链接卡片发送失败: {card_err}", file=sys.stderr)
    finally:
        for f in files:
            f.unlink(missing_ok=True)


async def _handle_cookie_failure(cfg, state, notifier, err: Exception):
    """记录 Cookie 冷却并向配置的 chat_id 发一次本次检测到的告警。"""
    state.set_cooldown(time.time() + cfg.sync.cooldown_hours * 3600)
    try:
        await notifier.alert(cookie_invalid_text(str(err)))
    except Exception as alert_err:  # 告警本身失败不应影响冷却已生效的事实
        print(f"告警发送失败: {alert_err}", file=sys.stderr)


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
        await _handle_cookie_failure(cfg, state, notifier, e)
        return

    # 阶段二：处理待同步队列（点赞正序）
    batch = state.next_batch(limit if limit is not None else cfg.sync.batch_size)
    try:
        for work in batch:
            await _process_work(state, fetcher, uploader, notifier, tmp_dir, work, download_fn)
            await sleep_fn(random.uniform(cfg.sync.sleep_min, cfg.sync.sleep_max))
    except DouyinAuthError as e:
        await _handle_cookie_failure(cfg, state, notifier, e)


async def run_backfill(cfg, state, fetcher, uploader, notifier, tmp_dir: Path,
                       sleep_fn=asyncio.sleep, download_fn=download_media, limit=None):
    """历史回填：从最新点赞逐页往旧翻，边翻页边入库边上传，直到最早一条。

    翻页游标随每页处理完落库（meta.backfill_cursor），中断后重跑从断点继续；
    已处理过的作品按状态自动跳过。全部翻完置 meta.backfill_done，此后应改用
    正常命令（不带 --backfill）做增量同步。
    """
    if state.get_meta("backfill_done"):
        print("历史回填已完成，请改用正常命令: venv/bin/python main.py")
        return
    if state.in_cooldown():
        print("处于冷却期，跳过本次回填")
        return

    processed = 0
    start_cursor = int(state.get_meta("backfill_cursor") or 0)
    print(f"回填自游标 {start_cursor} 开始（0 表示从最新页起）")
    try:
        sec_uid = await fetcher.resolve_sec_user_id()
        pages = fetcher.fetch_like_pages_resumable(sec_uid, start_cursor)
        async for next_cursor, has_more, records in pages:
            state.add_works(records)
            for rec in records:  # 页内新→旧
                work = state.get_pending_work(rec["aweme_id"])
                if work is None:  # 已上传/跳过/失败的断点重跑直接略过
                    continue
                await _process_work(state, fetcher, uploader, notifier, tmp_dir,
                                    work, download_fn)
                processed += 1
                if limit is not None and processed >= limit:
                    print(f"已达本次处理上限 {limit}，退出（断点已保存，可重复执行继续）")
                    return
                await sleep_fn(random.uniform(cfg.sync.sleep_min, cfg.sync.sleep_max))
            state.set_meta("backfill_cursor", str(next_cursor))
            if not has_more:
                break
        state.set_meta("backfill_done", "1")
        print("✅ 历史回填完成！之后请使用正常命令做增量同步: venv/bin/python main.py")
    except Exception as e:  # 翻页失败（Cookie 失效/风控）→ 冷却并告警，断点已保存
        await _handle_cookie_failure(cfg, state, notifier, e)


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
        run_fn = run_backfill if args.backfill else run_tick
        await run_fn(cfg, state, fetcher, uploader, notifier, tmp_dir, limit=args.limit)
    finally:
        await uploader.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="抖音点赞同步到 Telegram 频道")
    parser.add_argument("--config", default=str(BASE / "config.yaml"))
    parser.add_argument("--limit", type=int, default=None, help="本次最多处理条数（联调用）")
    parser.add_argument("--reset-failed", action="store_true", help="重置失败作品后退出")
    parser.add_argument("--backfill", action="store_true",
                        help="历史回填（临时）：从最新逐页往旧边翻边传，支持断点续传；完成后改用正常命令")
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
