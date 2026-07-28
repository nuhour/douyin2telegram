"""抖音侧封装：基于 f2 拉喜欢列表、单作品详情。"""

from f2.apps.douyin.handler import DouyinHandler
from f2.apps.douyin.utils import SecUserIdFetcher
from f2.exceptions.api_exceptions import APIUnauthorizedError

from d2t.config import Config
from d2t.models import DouyinAuthError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)

_AUTH_STATUS_CODES = {8, 401}  # F2/抖音分别使用 8 和 HTTP 401 表示登录失效


def _auth_error_from_response(response) -> DouyinAuthError | None:
    """识别 F2 已成功解析、但接口返回登录过期的响应。"""
    for attr in ("status_code", "api_status_code"):
        status_code = getattr(response, attr, None)
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            status_code = None
        if status_code in _AUTH_STATUS_CODES:
            return DouyinAuthError("抖音接口返回登录已过期，请更新 Cookie")

    raw = response._to_raw() if hasattr(response, "_to_raw") else {}
    if not isinstance(raw, dict):
        return None
    message = " ".join(
        str(raw.get(key) or "")
        for key in ("msg", "status_msg", "message", "toast")
    ).lower()
    if any(marker in message for marker in (
        "login_expired", "登录过期", "登陆过期", "请先登录", "需要登录", "未登录",
    )):
        return DouyinAuthError("抖音接口返回登录已过期，请更新 Cookie")
    return None


def _auth_error_from_exception(exc: Exception) -> DouyinAuthError | None:
    """把 F2 的鉴权异常转换成项目自己的异常，避免上层按普通作品失败处理。"""
    status_code = getattr(exc, "status_code", None)
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        status_code = None
    if isinstance(exc, APIUnauthorizedError) or status_code in _AUTH_STATUS_CODES:
        return DouyinAuthError("抖音接口鉴权失败，请更新 Cookie")
    return None


def _normalize(rec: dict) -> dict:
    return {
        "aweme_id": str(rec["aweme_id"]),
        "aweme_type": "images" if rec.get("images") else "video",
        "title": rec.get("desc") or "",
        "author": rec.get("nickname") or "",
    }


async def collect_new(pages, is_known, stop_after: int = 3) -> list[dict]:
    """遍历喜欢列表页（新→旧），遇连续 stop_after 条已知作品即停。

    连续判停而非单条判停：用户中途取消点赞会让单条判停漏掉更旧的新增。
    """
    new, consecutive = [], 0
    async for page in pages:
        for rec in page:
            if is_known(rec["aweme_id"]):
                consecutive += 1
                if consecutive >= stop_after:
                    return new
            else:
                consecutive = 0
                new.append(rec)
    return new


class DouyinFetcher:
    def __init__(self, cfg: Config):
        self.profile_url = cfg.douyin.profile_url
        self.http_headers = {
            "User-Agent": _UA,
            "Referer": "https://www.douyin.com/",
            "Cookie": cfg.douyin.cookie,
        }
        self.kwargs = {
            "headers": {"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
            "proxies": {"http://": None, "https://": None},
            "timeout": 5,  # f2 同时把它用作翻页间隔（秒），过大导致全量拉取极慢
            "cookie": cfg.douyin.cookie,
            "mode": "like",
        }
        self.handler = DouyinHandler(self.kwargs)

    async def resolve_sec_user_id(self) -> str:
        return await SecUserIdFetcher.get_sec_user_id(self.profile_url)

    async def fetch_like_pages(self, sec_user_id: str):
        """逐页 yield 规范化 record 列表（页内新→旧）。首页为空视为 Cookie 失效。"""
        first_page = True
        try:
            async for aweme_list in self.handler.fetch_user_like_videos(
                sec_user_id, 0, 20, None
            ):
                auth_error = _auth_error_from_response(aweme_list)
                if auth_error:
                    raise auth_error
                records = aweme_list._to_list()
                if first_page and not records:
                    raise DouyinAuthError("喜欢列表首页为空，Cookie 可能已失效或触发风控")
                first_page = False
                if not records:
                    continue
                yield [_normalize(r) for r in records]
        except Exception as exc:
            auth_error = _auth_error_from_exception(exc)
            if auth_error:
                raise auth_error from exc
            raise

    async def fetch_like_pages_resumable(self, sec_user_id: str, start_cursor: int = 0):
        """回填模式：从 start_cursor 起逐页 yield (next_cursor, has_more, records)。

        与 fetch_like_pages 不同，这里不做增量判停，而是携带游标供断点续翻，
        直到 has_more 为 False（翻到最早一条点赞）。
        """
        first_page = start_cursor == 0
        try:
            async for aweme_list in self.handler.fetch_user_like_videos(
                sec_user_id, start_cursor, 20, None
            ):
                auth_error = _auth_error_from_response(aweme_list)
                if auth_error:
                    raise auth_error
                records = aweme_list._to_list()
                if first_page and not records:
                    raise DouyinAuthError("喜欢列表首页为空，Cookie 可能已失效或触发风控")
                first_page = False
                yield (
                    aweme_list.max_cursor,
                    aweme_list.has_more,
                    [_normalize(r) for r in records] if records else [],
                )
        except Exception as exc:
            auth_error = _auth_error_from_exception(exc)
            if auth_error:
                raise auth_error from exc
            raise

    async def fetch_detail(self, aweme_id: str) -> dict:
        try:
            video = await self.handler.fetch_one_video(aweme_id=aweme_id)
        except Exception as exc:
            auth_error = _auth_error_from_exception(exc)
            if auth_error:
                raise auth_error from exc
            raise
        auth_error = _auth_error_from_response(video)
        if auth_error:
            raise auth_error
        detail = video._to_dict()
        # 附带逐张图的原始结构：live photo 判定需要知道哪张图带 video 字段，
        # 扁平化的 images/images_video 字段会丢失静图与动图的对位信息
        detail["images_raw"] = (video._to_raw().get("aweme_detail") or {}).get("images")
        return detail
