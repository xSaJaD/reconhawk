from __future__ import annotations

import asyncio

import httpx

from .db import SessionLocal, Finding, Addendum
from . import settings_store, reporting
from .integrations.hackerone import HackerOneError

API = "https://api.telegram.org/bot{token}/{method}"


async def _cfg():
    c = await settings_store.get_all()
    return c.get("telegram_bot_token", ""), c.get("telegram_chat_id", "")


async def _call(method: str, payload: dict) -> dict | None:
    token, _ = await _cfg()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(API.format(token=token, method=method), json=payload)
            return r.json()
    except Exception:
        return None


async def send_report(finding_id: int) -> bool:
    """Send a full finding report to Telegram with an inline 'Submit to HackerOne' button."""
    token, chat = await _cfg()
    if not token or not chat:
        return False
    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        if not f:
            return False
    body = (f"🛡️ *ReconHawk — Confirmed Finding*\n"
            f"*{_esc(f.title)}*\n"
            f"Severity: {f.severity} | Confidence: {f.confidence}% | {f.weakness or ''}\n\n"
            f"{_esc(f.summary)[:900]}\n\n"
            f"— full report —\n{_esc(f.report_md)[:2800]}")
    buttons = []
    if f.h1_report_id:
        buttons.append([{"text": f"✅ Submitted · #{f.h1_report_id}",
                         "url": f.h1_report_url or "https://hackerone.com"}])
    else:
        buttons.append([{"text": "🚀 Submit to HackerOne", "callback_data": f"submit:{f.id}"}])
    await _call("sendMessage", {"chat_id": chat, "text": body, "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": buttons}})
    return True


async def send_comment(addendum_id: int) -> bool:
    """Send a prepared comment to Telegram with a button that opens the report to paste it."""
    token, chat = await _cfg()
    if not token or not chat:
        return False
    async with SessionLocal() as s:
        a = await s.get(Addendum, addendum_id)
        if not a:
            return False
        f = await s.get(Finding, a.finding_id)
    text = reporting.build_comment_payload(a, f)
    url = f.h1_report_url or (f"https://hackerone.com/reports/{f.h1_report_id}" if f.h1_report_id else "https://hackerone.com")
    body = (f"💬 *New evidence for report #{f.h1_report_id or '?'}*\n"
            f"*{_esc(f.title)}*\n\n{_esc(text)[:3200]}\n\n"
            f"_Tap below to open the report, then paste the comment._")
    await _call("sendMessage", {"chat_id": chat, "text": body, "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": [[
                                    {"text": "🔗 Open report to paste", "url": url}]]}})
    return True


def _esc(s: str) -> str:
    # minimal Markdown-safe escaping
    return (s or "").replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")


# ---- callback poller: handles the inline "Submit" button ----
_task: asyncio.Task | None = None
_offset = 0


async def _poll_once() -> None:
    global _offset
    token, _ = await _cfg()
    if not token:
        return
    async with httpx.AsyncClient(timeout=35) as c:
        r = await c.get(API.format(token=token, method="getUpdates"),
                        params={"offset": _offset + 1, "timeout": 25,
                                "allowed_updates": '["callback_query"]'})
        data = r.json()
    for upd in data.get("result", []):
        _offset = max(_offset, upd["update_id"])
        cq = upd.get("callback_query")
        if not cq:
            continue
        await _handle_callback(cq)


async def _handle_callback(cq: dict) -> None:
    _,chat=await _cfg()
    if str(cq.get('message',{}).get('chat',{}).get('id',''))!=str(chat): return
    data = cq.get("data", "")
    cq_id = cq.get("id")
    if data.startswith("submit:"):
        fid = int(data.split(":", 1)[1])
        try:
            res = await reporting.submit_finding(fid)
            text = f"✅ Submitted — report #{res.get('id')}"
        except HackerOneError as e:
            text = f"❌ {e}"
        except Exception as e:  # noqa: BLE001
            text = f"❌ {e}"
        await _call("answerCallbackQuery", {"callback_query_id": cq_id, "text": text[:200], "show_alert": True})


async def _loop() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            await asyncio.sleep(5)
        await asyncio.sleep(1)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())
