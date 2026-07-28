"""从作品详情提取媒体地址，httpx 流式下载到临时目录。"""

from pathlib import Path

import httpx

from d2t.models import Media, OversizeError


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


def _extract_album(raw_images) -> Media | None:
    """从原始 images 结构逐张解析图集；带 video 字段的是 live photo，取视频地址。"""
    if not isinstance(raw_images, list) or not raw_images:
        return None
    urls, kinds = [], []
    for item in raw_images:
        if not isinstance(item, dict):
            return None  # 结构不符合假设，回退扁平字段
        live = (((item.get("video") or {}).get("play_addr") or {}).get("url_list")) or []
        static = item.get("url_list") or []
        if live:
            urls.append(live[0])
            kinds.append("video")
        elif static:
            urls.append(static[0])
            kinds.append("image")
    if not urls:
        return None
    return Media(kind="images", urls=urls,
                 item_kinds=kinds if "video" in kinds else None)


def extract_media(detail: dict) -> Media:
    album = _extract_album(detail.get("images_raw"))
    if album:
        return album
    images = _all_urls(detail.get("images"))
    if images:
        return Media(kind="images", urls=images)
    video_url = _first_url(detail.get("video_play_addr"))
    if video_url:
        return Media(kind="video", urls=[video_url])
    raise ValueError("作品详情中没有可用的媒体地址")


async def download_media(
    media: Media, aweme_id: str, tmp_dir: Path, headers: dict, client=None,
    heartbeat=None, max_bytes=None,
) -> list[Path]:
    """
    流式下载媒体文件。

    - 视频保存为 {aweme_id}.mp4
    - 图片保存为 {aweme_id}_{i}.jpg
    - 失败时不遗留部分文件
    - heartbeat: 每收到一块数据调用一次，供上层的无进展看门狗使用
    - max_bytes: 媒体总大小上限；响应头声明超限立即中止，声明缺失时按累计字节掐断，
      避免下载完成后才在上传阶段发现超限（大文件白下载）
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.AsyncClient(
        headers=headers, timeout=60, follow_redirects=True
    )
    files = []
    downloaded = 0
    path = None  # 当前正在写入的文件，中途失败时一并清理
    try:
        try:
            for i, url in enumerate(media.urls):
                if media.kind == "video":
                    path = tmp_dir / f"{aweme_id}.mp4"
                elif media.item_kinds and media.item_kinds[i] == "video":
                    path = tmp_dir / f"{aweme_id}_{i}.mp4"  # live photo 按视频下载
                else:
                    path = tmp_dir / f"{aweme_id}_{i}.jpg"
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    declared = int(resp.headers.get("content-length") or 0)
                    if max_bytes and declared and downloaded + declared > max_bytes:
                        raise OversizeError(
                            f"媒体大小 {(downloaded + declared) / 1024**2:.1f}MB"
                            f" 超出上传上限 {max_bytes / 1024**2:.0f}MB"
                        )
                    with path.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1024 * 256):
                            downloaded += len(chunk)
                            if max_bytes and downloaded > max_bytes:
                                raise OversizeError(
                                    f"媒体大小超出上传上限 {max_bytes / 1024**2:.0f}MB"
                                )
                            fh.write(chunk)
                            if heartbeat:
                                heartbeat()
                files.append(path)
            return files
        except Exception:
            # 失败时删除所有已创建的文件（含正在写入的半截文件）
            for p in files:
                if p.exists():
                    p.unlink()
            if path is not None and path not in files and path.exists():
                path.unlink()
            raise
    finally:
        if own_client:
            await client.aclose()
