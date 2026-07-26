"""通过 Bot 私聊发送告警。"""

from d2t.models import Work


def cookie_invalid_text(err: str) -> str:
    return (
        "🚨 douyin2telegram：拉取喜欢列表失败\n\n"
        f"原因：{err}\n\n"
        "请重新登录 www.douyin.com 复制 Cookie 更新到 config.yaml。\n"
        "已进入冷却，期间不再重试。"
    )


def work_failed_text(work: Work, err: str) -> str:
    return (
        "⚠️ douyin2telegram：作品同步失败（已重试 3 次，跳过）\n\n"
        f"{work.title or work.aweme_id}\n🔗 {work.url}\n原因：{err}\n\n"
        "修复后可运行 python main.py --reset-failed 重试。"
    )


class Notifier:
    def __init__(self, uploader, alert_chat_id: int):
        self.uploader = uploader
        self.alert_chat_id = alert_chat_id

    async def alert(self, text: str):
        await self.uploader.send_message(self.alert_chat_id, text)
