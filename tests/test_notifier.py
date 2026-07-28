import asyncio

from d2t.notifier import Notifier


class FakeUploader:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


def test_alert_is_sent_to_configured_chat_id():
    uploader = FakeUploader()
    asyncio.run(Notifier(uploader, alert_chat_id=987654321).alert("Cookie 已失效"))
    assert uploader.messages == [(987654321, "Cookie 已失效")]
