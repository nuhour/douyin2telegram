"""抖音侧封装：基于 f2 拉喜欢列表、单作品详情。"""

from f2.apps.douyin.handler import DouyinHandler
from f2.apps.douyin.utils import SecUserIdFetcher

from d2t.config import Config
from d2t.models import DouyinAuthError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)


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
            "timeout": 15,
            "cookie": cfg.douyin.cookie,
            "mode": "like",
        }
        self.handler = DouyinHandler(self.kwargs)

    async def resolve_sec_user_id(self) -> str:
        return await SecUserIdFetcher.get_sec_user_id(self.profile_url)

    async def fetch_like_pages(self, sec_user_id: str):
        """逐页 yield 规范化 record 列表（页内新→旧）。首页为空视为 Cookie 失效。"""
        first_page = True
        async for aweme_list in self.handler.fetch_user_like_videos(
            sec_user_id, 0, 20, None
        ):
            records = aweme_list._to_list()
            if first_page and not records:
                raise DouyinAuthError("喜欢列表首页为空，Cookie 可能已失效或触发风控")
            first_page = False
            if not records:
                return
            yield [_normalize(r) for r in records]

    async def fetch_detail(self, aweme_id: str) -> dict:
        video = await self.handler.fetch_one_video(aweme_id=aweme_id)
        return video._to_dict()
