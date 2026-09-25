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
    # Прямой вопрос «вы бот?» — честный ответ и всегда через владельца (policy: never_auto).
    (
        "asked_if_bot",
        re.compile(
            r"(ты|вы)\s+(что\s+)?(бот|робот)|это\s+(бот|робот|автоответ)|нейросет|chatgpt|gpt"
            r"|искусственн\w+\s+интеллект|\bии\s+(пишет|отвечает)|живой\s+человек",
            re.IGNORECASE,
        ),
    ),
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
    "asked_if_bot": "reply_bot_question",
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

# Шаблоны по voice.md: коротко, разговорно, по имени, один вопрос. {hi} — «Радмила, » или пусто.
TEMPLATES = {
    "refusal": "{Hi}понял, спасибо, что ответили! Хорошего дня.",
    "forwarded": "{Hi}спасибо! Буду ждать. А как вас зовут, чтобы я знал, кому писать, если что?",
    "forwarded_named": "{Hi}спасибо! Буду ждать.",
    "price": (
        "{Hi}честно, от задачи сильно зависит. Давайте Артём, наш основатель, "
        "за 15 минут посмотрит и сразу скажет цифру. Вам удобнее во вторник или в среду?"
    ),
    "proposal": (
        "{Hi}конечно. Только скажите в двух словах, что сейчас больше всего съедает время, "
        "чтобы я прислал по делу, а не общую презентацию."
    ),
    "contact_given": "{Hi}спасибо большое!",
    # Только черновик для владельца: на «вы бот?» агент сам не отвечает (never_auto). Если владелец
    # возьмёт этот текст, он честный: тексты готовит ассистент, владелец их смотрит. Не отрицать.
    "asked_if_bot": (
        "{Hi}честно, сообщения мне помогает писать ассистент, но переписку я смотрю сам. "
        "А на звонке будет живой Артём, наш основатель."
    ),
    # Новому человеку, чей номер дали: кто дал номер, кто мы одной фразой, один лёгкий вопрос.
    "warmup": (
        "{contact_hi}добрый день! Ваш номер мне {gave} {giver}. Я Ярослав из Camirix, "
        "мы автоматизируем рутину поверх 1С. Скажите, {warm_question}"
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


NAME = r"([А-ЯЁ][а-яё]{2,14})"
SELF_NAME = [
    re.compile(r"(?i:меня\s+зовут)\s+" + NAME),
    re.compile(r"(?:^|[,.!]\s*)(?i:это)\s+" + NAME + r"\b"),
    re.compile(NAME + r",\s*(?i:менеджер|секретар|администратор|бухгалтер|помощник)"),
]
NOT_NAMES = {
    "Здравствуйте",
    "Добрый",
    "Спасибо",
    "Меня",
    "Можете",
    "Передадим",
    "Компания",
    "Менеджер",
    "Обратитесь",
    "Звоните",
    "Пишите",
    "Номер",
    "Телефон",
}


def their_name(text):
    """Имя того, кто ответил («Меня зовут Радмила», «Радмила, менеджер»), или ''."""
    for rx in SELF_NAME:
        m = rx.search(text or "")
        if m and m.group(1) not in NOT_NAMES:
            return m.group(1)
    return ""


def contact_name(text, company=""):
    """Имя человека рядом с переданным номером («+7 700 760 0141 Виктория»), или ''.
    Сначала слово сразу после номера, потом перед ним; название компании именем не считается."""
    t = text or ""
    skip = NOT_NAMES | {their_name(t)} | set(re.findall(r"[А-ЯЁ][а-яё]+", company or ""))
    for m in PHONE.finditer(t):
        for chunk in (t[m.end() : m.end() + 25], t[max(0, m.start() - 25) : m.start()]):
            names = [w for w in re.findall(NAME, chunk) if w not in skip]
            if names:
                return names[0]
    return ""


def nominative(name):
    """Имя годится для обращения, только если похоже на именительный падеж: «Звоните Ерлану»,
    «номер Виктории» — косвенный падеж, «Ерлану, добрый день» звучит как бот. Тогда без имени."""
    return name if name and not name.endswith(("у", "ю", "е", "и", "ом", "ой")) else ""


def short_company(company):
    """«ТОО «Агротоп» (бренд …)» → «Агротоп»: так компанию называет человек, а не реестр."""
    c = re.sub(r"\(.*?\)", "", company or "")
    c = re.sub(r"\b(ТОО|ООО|ОсОО|АО|ЗАО|ИП|LLP|LLC)\b", "", c)
    return re.sub(r"[«»\"']", "", c).strip(" ,.") or ""


def hi(name):
    return f"{name}, " if name else ""


def cap(text):
    return text[:1].upper() + text[1:]


def draft(category, card, text=""):
    """Черновики: [{"to": "them"|"contact", "text": ...}].
    «…» — место, которое агент дописывает сам."""
    me = their_name(text)
    fields = {"Hi": hi(me), "company": short_company(card.get("company")) or "вашей компании"}
    key = "forwarded_named" if category == "forwarded" and me else category
    tpl = TEMPLATES.get(key)
    if not tpl:
        return []
    out = [{"to": "them", "text": cap(tpl.format(**fields))}]
    if category == "contact_given":
        who = nominative(contact_name(text, card.get("company") or ""))
        female = me.endswith(("а", "я"))
        giver = f"{me} из компании {fields['company']}" if me else f"в компании {fields['company']}"
        gave = ("дала" if female else "дал") if me else "дали"
        warm = str(card.get("warm_question") or "…?")
        out.append(
            {
                "to": "contact",
                "text": cap(
                    TEMPLATES["warmup"].format(
                        contact_hi=hi(who), gave=gave, giver=giver, warm_question=warm
                    )
                ),
            }
        )
    return out


def route(text, lead=None):
    category, contacts = classify(text)
    card = load_card(lead)
    drafts = draft(category, card, text)
    return {
        "category": category,
        "action": ACTION[category],
        "status": STATUS.get(category, "replied"),
        "contacts": contacts,
        "their_name": their_name(text),
        "contact_name": contact_name(text, card.get("company") or ""),
        "drafts": drafts,
        "draft": drafts[0]["text"] if drafts else "",
        # шаблон с «…» агент обязан дописать сам, по карточке лида и voice.md
        "needs_edit": any("…" in d["text"] for d in drafts),
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
