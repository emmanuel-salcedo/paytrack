from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, parse, request


class TelegramDeliveryError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    message_id: int | None
    raw: dict[str, object]


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = None,
    timeout_seconds: float = 10.0,
) -> TelegramSendResult:
    token = bot_token.strip()
    chat = chat_id.strip()
    if not token:
        raise TelegramDeliveryError("Telegram bot token is required.")
    if not chat:
        raise TelegramDeliveryError("Telegram chat ID is required.")

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_data = {"chat_id": chat, "text": text}
    if parse_mode:
        payload_data["parse_mode"] = parse_mode
    payload = parse.urlencode(payload_data).encode("utf-8")
    req = request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        retryable = exc.code == 429 or 500 <= exc.code <= 599
        raise TelegramDeliveryError(f"Telegram API HTTP {exc.code}: {body}", retryable=retryable) from exc
    except error.URLError as exc:
        raise TelegramDeliveryError(f"Telegram delivery failed: {exc.reason}", retryable=True) from exc
    except json.JSONDecodeError as exc:
        raise TelegramDeliveryError("Telegram API returned invalid JSON.", retryable=True) from exc

    if not bool(data.get("ok")):
        description = data.get("description") or "unknown Telegram API error"
        retryable = "too many requests" in str(description).lower()
        raise TelegramDeliveryError(f"Telegram API rejected message: {description}", retryable=retryable)

    result = data.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    return TelegramSendResult(ok=True, message_id=message_id if isinstance(message_id, int) else None, raw=data)
