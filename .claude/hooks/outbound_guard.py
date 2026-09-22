#!/usr/bin/env python3
"""
Детерминированный шлюз перед необратимыми исходящими действиями.

Не использует модель. Каждая проверка — механическая, повторяемая,
с одинаковым результатом при одинаковом входе. На этих проверках
частота ошибок равна нулю, а не «близка к нулю».

Читает PreToolUse-событие из stdin, при нарушении печатает JSON
с permissionDecision=deny и блокирует вызов инструмента.

FAIL CLOSED: если файл фактов нечитаем или тело письма не разобрать —
блокируем. Невозможность проверить не равна отсутствию проблемы.
"""
import json
import re
import sys
from pathlib import Path

FACTS = Path(__file__).resolve().parents[2] / "camirix" / "facts.yaml"

BANNED_WORDS = [
    "обычно", "как правило", "судя по всему", "довольно",
    "стоит отметить", "важно понимать",
]
BANNED_POSITIONING = [
    "настроим воронку", "наведём порядок в crm", "наведем порядок в crm",
    "внедрим crm", "настроить воронку",
]
FABRICATION = [
    "мы помогли", "наш клиент", "у клиента выросл", "один из наших заказчиков",
    "наши клиенты получ", "кейс:",
]
THIRD_PARTY_STATS = ["idc", "manpowergroup", "slack workforce"]
MONEY = re.compile(
    r"(\d[\d\s ]{2,}\s*(?:₽|руб|р\.)|₽\s*\d|"
    r"\d+\s*(?:тыс|млн|тысяч|миллион)[а-я]*\s*(?:₽|руб)?|"
    r"\b29\s?000\b|\b49\s?000\b|\b69\s?000\b)",
    re.IGNORECASE,
)

SEND_TOOLS = {"mcp__Gmail__send_message", "mcp__Gmail__reply", "mcp__Gmail__forward"}
DELETE_TOOLS = {"mcp__Claude_Code_Remote__delete_trigger"}


def deny(reasons):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "Шлюз outbound_guard заблокировал действие:\n- " + "\n- ".join(reasons),
        },
        "systemMessage": "⛔ outbound_guard: " + reasons[0],
    }))
    sys.exit(0)


def facts_flag(key_path, want="confirmed"):
    """Грубый, но надёжный разбор status у секции. Нечитаемо -> считаем НЕ confirmed."""
    try:
        text = FACTS.read_text(encoding="utf-8")
    except Exception:
        return False
    head = key_path.split(".")[0]
    m = re.search(rf"^{re.escape(head)}:\s*$", text, re.MULTILINE)
    if not m:
        return False
    block = text[m.end():]
    nxt = re.search(r"^\S", block, re.MULTILINE)
    block = block[: nxt.start()] if nxt else block
    for seg in key_path.split(".")[1:]:
        m2 = re.search(rf"^\s+{re.escape(seg)}:\s*$", block, re.MULTILINE)
        if not m2:
            return False
        block = block[m2.end():]
    st = re.search(r"status:\s*(\w+)", block)
    return bool(st and st.group(1) == want)


def facts_value(section, key):
    """Точное значение value у подсекции. Нечитаемо -> None (шлюз тогда блокирует)."""
    try:
        text = FACTS.read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(rf"^{re.escape(section)}:\s*$", text, re.MULTILINE)
    if not m:
        return None
    block = text[m.end():]
    nxt = re.search(r"^\S", block, re.MULTILINE)
    block = block[: nxt.start()] if nxt else block
    m2 = re.search(rf"^\s+{re.escape(key)}:\s*$", block, re.MULTILINE)
    if not m2:
        return None
    block = block[m2.end():]
    v = re.search(r"value:\s*(.+)", block)
    if not v:
        return None
    val = v.group(1).strip().strip('"').strip("'")
    return None if val in ("null", "~", "") else val


def check_send(ti):
    body = ti.get("body") or ti.get("text") or ""
    subject = ti.get("subject") or ""
    if not body.strip():
        return ["тело письма пустое или не распознано — проверить нечего, значит нельзя отправлять"]
    blob = (subject + "\n" + body).lower()
    out = []

    # F1 — сумма в исходящем. Цену называет Артём на звонке под запрос,
    # прайса не существует, поэтому сумма запрещена всегда. Исключение —
    # только точная строка из pricing.quotable_anchor со status: confirmed.
    m = MONEY.search(subject + "\n" + body)
    if m:
        hit = m.group(0).strip()
        anchor = (facts_value("pricing", "quotable_anchor")
                  if facts_flag("pricing.quotable_anchor") else None)
        if not anchor or anchor not in (subject + "\n" + body):
            out.append(
                f"F1 сумма «{hit}» в тексте. Прайса нет: цену Артём называет на звонке "
                f"под запрос покупателя. Назвать цифру — связать ему руки на переговорах")

    # F3 — 7 дней бесплатно, пока не подтверждено
    if not facts_flag("trial") and re.search(r"(7|семь)\s*дн", blob):
        out.append("F3 обещание бесплатного периода, а trial в facts.yaml не confirmed")

    # F4 — выдуманные результаты
    for p in FABRICATION:
        if p in blob:
            out.append(f"F4 «{p}» — у Camirix нет измеренных результатов "
                       "и нет разрешения называть клиентов")

    # F5 — чужая статистика
    for p in THIRD_PARTY_STATS:
        if p in blob:
            out.append(f"F5 статистика {p.upper()} — чужая, нужна ссылка на источник рядом")

    # F6 — запрещённое позиционирование
    for p in BANNED_POSITIONING:
        if p in blob:
            out.append(f"F6 «{p}» — по деку это описание проблемы, а не решения")

    # S1 — отправитель
    if not facts_flag("sender_identity") and re.search(r"мы в camirix|наша компания camirix", blob):
        out.append("S1 «мы в Camirix» от неподтверждённого отправителя — "
                   "sender_identity не confirmed")

    # S2 — открытие про вакансию на общий ящик
    to = " ".join(ti.get("to") or []).lower()
    generic = any(to.startswith(p) or f"<{p}" in to for p in
                  ("info@", "hello@", "mail@", "office@", "zakaz@", "sales@", "ask@", "pr@"))
    first = body.strip().split(".")[0].lower()
    if generic and re.search(r"пишу по вакансии|по вакансии", first):
        out.append("S2 открытие про вакансию на общий ящик читается как отклик соискателя")

    # W1 — объём
    words = len(re.findall(r"\b[\wЀ-ӿ-]+\b", body))
    if words > 90:
        out.append(f"W1 объём {words} слов, максимум 90 — длинное письмо не дочитывают")
    elif words < 35:
        out.append(f"W1 объём {words} слов — текст похож на обрезанный, отправлять нельзя")

    # W2 — один CTA
    q = body.count("?")
    if q != 1:
        out.append(f"W2 вопросительных знаков {q}, требуется ровно 1")

    # W3 — тире
    d = body.count("—")
    if d > 1:
        out.append(f"W3 тире {d}, допускается максимум 1")

    # W4 — запрещённые слова
    for w in BANNED_WORDS:
        if re.search(rf"\b{re.escape(w)}", blob):
            out.append(f"W4 запрещённое слово «{w}»")
    if re.search(r"интересно[^.?!]{0,20}\b(как|что|какие|сколько|почему)\b", blob):
        out.append("W4 связка «интересно» + вопросительное слово")

    # W5 — шаблон
    if re.search(r"как сейчас\s+\w+", blob):
        out.append("W5 запрещённый шаблон «Как сейчас [устроено]»")

    return out


def check_delete(ti):
    tid = ti.get("trigger_id", "")
    if not re.fullmatch(r"trig_[A-Za-z0-9]{10,}", tid or ""):
        return [f"T2 непохожий на настоящий trigger_id «{tid}» — удалять вслепую нельзя"]
    return []


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        deny(["вход хука не разобран — блокирую, потому что проверить невозможно"])
    name = ev.get("tool_name", "")
    ti = ev.get("tool_input") or {}
    if name in SEND_TOOLS:
        r = check_send(ti)
    elif name in DELETE_TOOLS:
        r = check_delete(ti)
    else:
        sys.exit(0)
    if r:
        deny(r)
    sys.exit(0)


if __name__ == "__main__":
    main()
