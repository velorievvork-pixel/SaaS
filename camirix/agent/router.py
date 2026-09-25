#!/usr/bin/env python3
"""
Разбор ответа лида: что это за ответ и что делать дальше.

Правила взяты из handoff.md и из настоящих ответов 24–25.09 (тесты — на них же).
Черновик — шаблон с подстановками из карточки; агент дописывает его под лида,
а текст всё равно проходит wa_send.py / outbound_guard.py перед отправкой.

    python3 camirix/agent/router.py --text "Здравствуйте не интересует" --lead cbc-astana-kz
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

PHONE = re.compile(
    r"(?<!\d)(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
    r"|(?<!\d)\+?99[268]\d[\s\-()]*\d{2,3}[\s\-()]*\d{2,3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Порядок важен: первое совпадение побеждает.
RULES = [
    (
        "opt_out",
        re.compile(
            r"не\s+пиш|больше\s+не\s+пиш|не\s+беспоко|отпиш|удалите\s+(мой\s+)?номер"
            r"|^\s*стоп\s*$|это\s+спам|жазбаңыз",
            re.IGNORECASE,
        ),
    ),
    (
        "autoreply",
        re.compile(
            r"автоматическ\w+ ответ|спасибо за (ваше )?обращение|ответим в ближайшее"
            r"|out of office|в отпуске до|не в офисе",
            re.IGNORECASE,
        ),
    ),
    (
        "refusal",
        re.compile(
            r"не\s+интерес|не\s+актуальн|не\s+нужн|нет\s+потребност|не\s+планируем"
            r"|уже\s+(есть|внедрили|работаем)|не\s+рассматриваем|откажемся|спасибо,\s*нет",
            re.IGNORECASE,
        ),
    ),
    (
        "price",
        re.compile(
            r"сколько\s+стоит|какая\s+цена|стоимост|прайс|расценк|по\s+деньгам|бюджет",
            re.IGNORECASE,
        ),
    ),
    (
        "proposal",
        re.compile(
            r"\bкп\b|коммерческ\w+\s+предложени|пришлите\s+(информацию|презентаци|предложени)"
            r"|на\s+почту\s+(пришлите|скиньте|отправьте)",
            re.IGNORECASE,
        ),
    ),
    (
        "meeting",
        re.compile(
            r"давайте\s+(созвон|встрет|обсуд)|можно\s+(созвон|встрет)|готов\w*\s+(к\s+)?(созвон|встреч)"
            r"|удобно\s+(во|в|завтра|сегодня)|набер(ите|и)те",
            re.IGNORECASE,
        ),
    ),
    (
        "forwarded",
        re.compile(
            r"переда(дим|м|ла|л|ли)|руководств|с\s+вами\s+свяж|перезвон|рассмотр(ят|им)",
            re.IGNORECASE,
        ),
    ),
]
GREETING = re.compile(
    r"^\s*(здравствуйте|добрый\s+(день|вечер)|привет|салем|сәлеметсіз\s+бе)[\s!.,]*$", re.IGNORECASE
)

ACTION = {  # категория → действие из policy.yaml
    "opt_out": "reply_opt_out",
    "autoreply": None,
    "refusal": "reply_refusal",
    "contact_given": "reply_contact_given",
    "forwarded": "reply_forwarded",
    "greeting": "reply_greeting",
    "price": "reply_price",
    "proposal": "reply_proposal",
    "meeting": "reply_meeting",
    "question": "reply_question",
    "unclear": "reply_unclear",
}

STATUS = {"opt_out": "refused", "refusal": "refused", "autoreply": None, "greeting": "replied"}

TEMPLATES = {
    "refusal": "Понял, спасибо, что ответили. Если {topic} изменится, пишите.",
    "forwarded": "Спасибо! Буду ждать. Подскажите, с кем из руководства лучше держать связь?",
    "price": (
        "Готового прайса нет, стоимость зависит от задачи. Артём, основатель Camirix, назовёт её "
        "на коротком звонке, когда поймёт, что нужно именно вам. Вам удобнее во вторник в 11:00 "
        "или в среду в 15:00 по {tz}?"
    ),
    "proposal": (
        "Пришлю, но без 15 минут разговора оно будет общим. Давайте Артём сначала уточнит задачу, "
        "а КП придёт уже под вас. Вам удобнее во вторник в 11:00 или в среду в 15:00 по {tz}?"
    ),
    "contact_given": (
        "Здравствуйте! Меня зовут Ярослав, я из компании Camirix. Этот номер мне дали в {company}. "
        "Вижу, что вы ищете {vacancy}: {process_short}. Подскажите, {warm_question}"
    ),
}

TZ_NAME = {"KZ": "Астане", "RU": "Москве", "KG": "Бишкеку", "UZ": "Ташкенту"}


def classify(text):
    """→ (категория, найденные контакты)."""
    t = (text or "").strip()
    contacts = PHONE.findall(t) + EMAIL.findall(t)
    if not t:
        return "unclear", contacts
    for name, rx in RULES:
        if rx.search(t):
            if name == "forwarded" and contacts:
                return "contact_given", contacts  # «передала, вот номер Виктории» — важнее номер
            return name, contacts
    if contacts:
        return "contact_given", contacts
    if GREETING.match(t):
        return "greeting", contacts
    if "?" in t:
        return "question", contacts
    return "unclear", contacts


def load_card(lead):
    if not lead:
        return {}
    from lead_gate import load_lead

    p = HERE.parent / "leads" / f"{lead}.yaml"
    return load_lead(p.read_text(encoding="utf-8")) if p.exists() else {}


def draft(category, card):
    tpl = TEMPLATES.get(category)
    if not tpl:
        return ""
    process = str(card.get("process_to_automate") or "").split(".")[0].split(":")[0].strip()
    # Тема для отказа в нужном падеже из карточки не соберёшь: берём готовую фразу, если она есть
    # (поле topic: «с заявками из регионов что-то»), иначе нейтрально.
    topic = str(card.get("topic") or "").strip() or "ситуация"
    fields = {
        "topic": topic,
        "process": (process[:1].lower() + process[1:80]) if process else "этой задачей",
        "tz": TZ_NAME.get(str(card.get("country") or "KZ"), "Астане"),
        "company": card.get("company") or "вашей компании",
        "vacancy": str((card.get("vacancy") or {}).get("title") or "сотрудника").lower(),
        "process_short": process[:120] or "…",
        "warm_question": "…?",
    }
    return tpl.format(**fields)


def route(text, lead=None):
    category, contacts = classify(text)
    card = load_card(lead)
    return {
        "category": category,
        "action": ACTION[category],
        "status": STATUS.get(category, "replied"),
        "contacts": contacts,
        "draft": draft(category, card),
        "needs_edit": "…" in draft(category, card),  # шаблон с дырами агент обязан дописать сам
    }


def main():
    ap = argparse.ArgumentParser(description="Что за ответ и что делать")
    ap.add_argument("--text", required=True)
    ap.add_argument("--lead", help="имя карточки без .yaml, для подстановок в черновик")
    a = ap.parse_args()
    print(json.dumps(route(a.text, a.lead), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
