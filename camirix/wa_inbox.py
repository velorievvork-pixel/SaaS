#!/usr/bin/env python3
"""
Ответы лидов в WhatsApp: чтение через Green-API и авто-«Молчат».

Зачем. Сообщения в WhatsApp отправляет пользователь, ответы Claude раньше видел
только если пользователь вставил их на страницу «Отправить сегодня». Этот скрипт
закрывает дыру двумя способами:

1. Green-API (если заданы секреты окружения GREEN_API_ID и GREEN_API_TOKEN):
   забирает входящие за последние N минут и сопоставляет их с номерами лидов.
   Только чтение: скрипт вызывает лишь методы из READ_ONLY, отправки в нём нет.
2. Без API: лид со статусом «Отправил», которому не ответили 3 рабочих дня,
   предлагается перевести в «Молчат» (под второе касание), а список ждущих
   ответа идёт в вечернее напоминание.

Вход — документы коллекции outbox страницы «Отправить сегодня», выгруженные
через ArtifactData (out_dir) или одним JSON-списком:
    python3 wa_inbox.py --outbox /tmp/wa/outbox              # каталог *.json
    python3 wa_inbox.py --outbox rows.json --minutes 180 --json

Выход — предложения правок, а не правки: в базу страницы их пишет Claude
через ArtifactData update с if_version.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SILENT_AFTER_WORKDAYS = 3
READ_ONLY = {"getStateInstance", "lastIncomingMessages"}
# Не WhatsApp, а настройка нашего шлюза wagate: какие чаты считать лидами. Остальные (личные)
# шлюз не читает. Метод только сужает то, что шлюз показывает, ничего не отправляет.
LEADS_METHOD = "wagateAllow"
DEFAULT_API = "https://api.green-api.com"
TEXT_TYPES = {"textMessage", "extendedTextMessage", "quotedMessage"}


def digits(phone):
    """Номер → цифры в формате chatId WhatsApp: 8XXXXXXXXXX (РФ/КЗ) → 7XXXXXXXXXX."""
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


def parse_ts(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def workdays_between(start, end):
    """Полные рабочие дни (пн–пт) после даты start до даты end включительно."""
    n, day = 0, start.date()
    while day < end.date():
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            n += 1
    return n


def sent_at(row):
    """Время последней отметки «Отправил»; без истории — updated_at."""
    for h in reversed(row.get("history") or []):
        if str(h.get("text", "")).startswith("Отправил"):
            return parse_ts(h.get("at"))
    return parse_ts(row.get("updated_at"))


def load_outbox(path):
    """Каталог выгрузки ArtifactData или JSON-файл → [{id, version, ...data}]."""
    p = Path(path)
    files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
    rows = []
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        for item in obj if isinstance(obj, list) else [obj]:
            data = item.get("data", item)
            row = dict(data)
            row["id"] = item.get("id") or data.get("id") or f.stem
            row["version"] = item.get("version")
            rows.append(row)
    return rows


def silence_check(rows, now=None):
    """(перевести в «Молчат», ждут ответа) по статусу sent и возрасту отправки."""
    now = now or dt.datetime.now(dt.UTC)
    to_silent, waiting = [], []
    for r in rows:
        if r.get("status") != "sent" or (r.get("reply") or "").strip():
            continue
        t = sent_at(r)
        age = workdays_between(t, now) if t else 0
        item = {"id": r["id"], "company": r.get("company", ""), "version": r.get("version"),
                "sent_at": t.isoformat() if t else "", "workdays": age}
        (to_silent if t and age >= SILENT_AFTER_WORKDAYS else waiting).append(item)
    return to_silent, waiting


# --- Green-API ---------------------------------------------------------------

def api_call(method, query="", env=None, opener=urllib.request.urlopen, body=None):
    if method not in READ_ONLY and method != LEADS_METHOD:
        raise ValueError(f"{method}: скрипт только читает WhatsApp")
    env = env if env is not None else os.environ
    base = (env.get("GREEN_API_URL") or DEFAULT_API).rstrip("/")
    iid, token = env.get("GREEN_API_ID"), env.get("GREEN_API_TOKEN")
    if not iid or not token:
        raise LookupError("нет секретов GREEN_API_ID / GREEN_API_TOKEN")
    url = f"{base}/waInstance{iid}/{method}/{token}" + (f"?{query}" if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"User-Agent": "camirix-wa-inbox",
                                          "Content-Type": "application/json"})
    try:
        with opener(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        # В тексте ошибки нет URL: в нём токен.
        raise RuntimeError(f"Green-API {method}: HTTP {e.code}") from None


def message_text(m):
    if m.get("typeMessage") in TEXT_TYPES:
        ext = (m.get("extendedTextMessage") or {}).get("text")
        return (m.get("textMessage") or ext or "").strip()
    if m.get("typeMessage") == "reactionMessage":
        return "[реакция] " + ((m.get("extendedTextMessageData") or {}).get("text") or "")
    return f"[{m.get('typeMessage', 'сообщение')}]"  # голосовое, файл, фото: смотреть глазами


def match_replies(rows, messages):
    """Входящие из личных чатов с номеров лидов → [{id, company, text, at, ...}]."""
    by_phone = {digits(r.get("phone")): r for r in rows if digits(r.get("phone"))}
    out = []
    for m in sorted(messages or [], key=lambda x: x.get("timestamp", 0)):
        chat = str(m.get("chatId", ""))
        if not chat.endswith("@c.us"):
            continue  # группы и служебные события
        r = by_phone.get(digits(chat.split("@")[0]))
        if not r:
            continue
        ts = dt.datetime.fromtimestamp(m.get("timestamp", 0), dt.UTC)
        sent = sent_at(r)
        out.append({
            "id": r["id"], "company": r.get("company", ""), "version": r.get("version"),
            "status": r.get("status"), "text": message_text(m), "at": ts.isoformat(),
            "sender": m.get("senderName") or m.get("senderContactName") or "",
            # Сообщение раньше нашей отправки — не ответ, а старая переписка.
            "before_sent": bool(sent and ts < sent),
            "id_message": m.get("idMessage", ""),
        })
    return out


def sent_phones(rows):
    """Номера, которым сообщение уже ушло (руками со страницы или через шлюз)."""
    return sorted({digits(r.get("phone")) for r in rows if sent_at(r) and digits(r.get("phone"))})


def register_leads(rows, env=None, opener=urllib.request.urlopen):
    """Сообщает шлюзу номера лидов, чтобы он читал их ответы. Только отправленным: иначе
    новый лид считался бы знакомым и обходил дневной лимит новых чатов.
    У Green-API такого метода нет, тогда пропуск."""
    phones = sent_phones(rows)
    if not phones:
        return 0
    try:
        reply = api_call(LEADS_METHOD, env=env, opener=opener, body={"phones": phones})
        return (reply or {}).get("added", 0)
    except RuntimeError:
        return 0


def poll(rows, minutes, env=None, opener=urllib.request.urlopen):
    state = (api_call("getStateInstance", env=env, opener=opener) or {}).get("stateInstance")
    if state != "authorized":
        return state, []
    register_leads(rows, env=env, opener=opener)
    msgs = api_call("lastIncomingMessages", f"minutes={int(minutes)}", env=env, opener=opener)
    return state, match_replies(rows, msgs)


def main():
    ap = argparse.ArgumentParser(description="Ответы в WhatsApp и авто-«Молчат»")
    ap.add_argument("--outbox", required=True, help="выгрузка outbox: каталог или JSON")
    ap.add_argument("--minutes", type=int, default=180, help="окно Green-API, минут")
    ap.add_argument("--no-api", action="store_true", help="только проверка тишины")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = load_outbox(a.outbox)
    to_silent, waiting = silence_check(rows)
    report = {"api": "off", "replies": [], "to_silent": to_silent, "waiting": waiting}
    if not a.no_api:
        try:
            state, replies = poll(rows, a.minutes)
            report["api"], report["replies"] = state, replies
        except LookupError:
            report["api"] = "no_secrets"
        except (RuntimeError, OSError) as e:
            report["api"] = f"error: {e}"

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print(f"Green-API: {report['api']}")
    for r in report["replies"]:
        flag = "  (до нашей отправки)" if r["before_sent"] else ""
        print(f"  ОТВЕТ {r['company']}: {r['text'][:200]}{flag}")
    for r in to_silent:
        print(f"  → Молчат: {r['company']} ({r['workdays']} раб. дн.)")
    if waiting:
        print("  Ждут ответа: " + ", ".join(f"{r['company']} ({r['workdays']})" for r in waiting))
    return 0


if __name__ == "__main__":
    sys.exit(main())
