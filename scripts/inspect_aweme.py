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
