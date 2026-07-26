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
    """
    流式下载媒体文件。

    - 视频保存为 {aweme_id}.mp4
    - 图片保存为 {aweme_id}_{i}.jpg
    - 失败时不遗留部分文件
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.AsyncClient(
        headers=headers, timeout=60, follow_redirects=True
    )
    files = []
    try:
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
        except Exception:
            # 失败时删除所有已创建的文件
            for path in files:
                if path.exists():
                    path.unlink()
            raise
    finally:
        if own_client:
            await client.aclose()
