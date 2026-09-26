#!/usr/bin/env python3
"""
Отправка в WhatsApp через наш шлюз wagate (или Green-API: API одинаковый).

Перед отправкой текст проходит те же проверки, что письма (outbound_guard.py):
суммы, выдуманные результаты, запрещённые слова, тире, один вопрос. Лимиты
(новые чаты в день, рабочие часы адресата, стоп-лист, повтор текста) проверяет шлюз.

    python3 camirix/wa_send.py --phone "+7 701 123 45 67" --text "…" --kind first
    python3 camirix/wa_send.py --phone 77011234567 --file msg.txt --kind reply --dry-run

--kind: first — первое сообщение (все правила, 35–90 слов);
        followup — второе касание (без правила объёма: 20–40 слов);
        reply — ответ в живом диалоге (без объёма и без правила «ровно один вопрос»).

Переменные окружения (те же, что у wa_inbox.py): GREEN_API_URL (адрес шлюза),
GREEN_API_ID, GREEN_API_TOKEN.
Код возврата: 0 — отправлено (или --dry-run прошёл), 1 — текст не прошёл проверку,
2 — шлюз отказал (лимит, часы, стоп-лист, нет WhatsApp), 3 — нет настроек или шлюз недоступен.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / ".claude" / "hooks"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "agent"))
import humanity  # noqa: E402
from outbound_guard import check_send  # noqa: E402
from wa_inbox import DEFAULT_API, digits  # noqa: E402

# W1 (35–90 слов) для первого сообщения согласуется с данными: Hunter 2026 — лучшие ответы
# у 61–80 слов, Overloop 2026 — 50–125; у Lavender 25–50. Источники расходятся,
# поэтому жёстче не ограничиваем.
SKIP = {"first": (), "followup": ("W1",), "reply": ("W1", "W2")}

# Шлюз объясняет отказ кодом; здесь то же по-русски, чтобы агент и человек понимали, что делать.
REASONS = {
    "off_hours": "у адресата нерабочее время, отправить позже",
    "weekend": "у адресата выходной, отправить в рабочий день",
    "holiday": "у адресата государственный праздник, отправить на следующий рабочий день",
    "low_reply_rate": "на новые чаты почти не отвечают (<10% за 30 дней): новые чаты на паузе, "
                      "владельцу — пересмотреть тексты и сегмент",
    "same_text_many": "этот текст сегодня уже ушёл нескольким людям: переписать под этого человека",
    "unknown_country": "страна номера неизвестна шлюзу, проверьте номер",
    "daily_new_chat_limit": "дневной лимит новых чатов исчерпан, продолжить завтра",
    "too_soon": "слишком частые новые чаты, повторить через паузу",
    "stop_list": "человек просил не писать, номер в стоп-листе",
    "duplicate_24h": "этот текст уже уходил на этот номер за сутки",
    "no_whatsapp": "на номере нет WhatsApp",
    "new_chats_paused": "новые чаты на паузе после неудачной отправки (похоже на ограничение "
                        "WhatsApp): не обходить, владельцу сразу",
    "send_failed": "WhatsApp не принял сообщение; если это новый чат — новые чаты "
                   "на паузе на сутки",
    "check_limit": "лимит проверок номеров на сегодня исчерпан: проверять только тех, "
                   "кому пишем сегодня",
    "country_blocked": "в России WhatsApp заблокирован (с февраля 2026): новым писать на почту",
    "whatsapp not authorized": "шлюз не привязан к телефону (нужен QR)",
}


# Смещение от UTC по коду страны (как в шлюзе, wagate/src/core.js): для проверки приветствия.
UTC_OFFSET = [("7", 3), ("375", 3), ("374", 4), ("994", 4), ("995", 4), ("998", 5),
              ("992", 5), ("993", 5), ("996", 6), ("373", 3)]


def local_hour(phone, now=None):
    """Час у адресата по номеру или None. Казахстан (+7 6xx/7xx) — UTC+5, Россия — UTC+3."""
    d = digits(phone)
    if not d:
        return None
    now = now or dt.datetime.now(dt.UTC)
    if d.startswith(("76", "77")):
        off = 5
    else:
        off = next((o for code, o in sorted(UTC_OFFSET, key=lambda x: -len(x[0]))
                    if d.startswith(code)), None)
    return None if off is None else (now + dt.timedelta(hours=off)).hour


def guard(text, kind, their_text="", phone=None, now=None):
    """Причины не отправлять (пусто — можно): правила писем и «пишет как человек» (voice.md)."""
    rules = [r for r in check_send({"body": text, "to": []}) if not r.startswith(SKIP[kind])]
    hour = local_hour(phone, now) if phone else None
    return rules + humanity.check(text, kind, their_text, local_hour=hour)


def send(phone, text, env=None, opener=urllib.request.urlopen):
    """→ (http_status, json). Токен в сообщения об ошибках не попадает."""
    env = env if env is not None else os.environ
    base = (env.get("GREEN_API_URL") or DEFAULT_API).rstrip("/")
    iid, token = env.get("GREEN_API_ID"), env.get("GREEN_API_TOKEN")
    iid, token = (iid or "").strip(), (token or "").strip()
    if not iid or not token:
        raise LookupError("нет GREEN_API_ID / GREEN_API_TOKEN")
    body = json.dumps({"chatId": f"{digits(phone)}@c.us", "message": text}).encode()
    req = urllib.request.Request(
        f"{base}/waInstance{iid}/sendMessage/{quote(token, safe='')}", data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "camirix-wa-send"})
    try:
        # До 8 с «печатает…» и до минуты на пробуждение Render: короткий таймаут — ложный отказ.
        with opener(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except ValueError:
            payload = {}
        return e.code, payload


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Отправка в WhatsApp через шлюз с проверками")
    ap.add_argument("--phone", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text")
    src.add_argument("--file")
    ap.add_argument("--kind", choices=list(SKIP), default="first")
    ap.add_argument("--their", default="", help="их последнее сообщение (для длины ответа)")
    ap.add_argument("--dry-run", action="store_true", help="только проверить текст")
    a = ap.parse_args(argv)
    text = a.text if a.text is not None else Path(a.file).read_text(encoding="utf-8")
    text = text.strip()

    problems = guard(text, a.kind, a.their, phone=a.phone)
    if problems:
        out({"ok": False, "stage": "guard", "reasons": problems})
        return 1
    if a.dry_run:
        out({"ok": True, "stage": "guard", "dry_run": True})
        return 0
    try:
        status, payload = send(a.phone, text)
    except LookupError as e:
        out({"ok": False, "stage": "config", "error": str(e)})
        return 3
    except TimeoutError:
        # Шлюз мог успеть отправить: повтор или ручная отправка дали бы человеку два сообщения.
        out({"ok": False, "stage": "gateway", "error": "timeout",
             "meaning": "шлюз не ответил вовремя, сообщение могло уйти: сначала проверить "
                        "lastOutgoingMessages, только потом повторять"})
        return 3
    except OSError as e:
        out({"ok": False, "stage": "gateway", "error": f"шлюз недоступен: {e}"})
        return 3
    if status == 200 and payload.get("idMessage"):
        out({"ok": True, "idMessage": payload["idMessage"]})
        return 0
    if status >= 500 and not payload.get("error"):
        out({"ok": False, "stage": "gateway", "status": status,
             "error": f"шлюз недоступен: HTTP {status}"})
        return 3
    err = payload.get("error", f"HTTP {status}")
    extra = {k: v for k, v in payload.items() if k != "error"}
    out({"ok": False, "stage": "gateway", "status": status, "error": err,
         "meaning": REASONS.get(err, ""), **extra})
    return 2


if __name__ == "__main__":
    sys.exit(main())
