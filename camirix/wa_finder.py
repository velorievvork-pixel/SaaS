#!/usr/bin/env python3
"""
Проверка номера WhatsApp на странице компании.

Зачем. 24–25.09 у 3 из 3 проверенных контактов, найденных агентами, номер
не подтвердился в первоисточнике (Cheber, IELTS, Office-Expert). А на страницах
2ГИС ссылки wa.me из блоков «Похожие организации» и «Реклама» принадлежат чужим
фирмам: у Dekmy так «нашлись» два чужих номера. Скрипт открывает страницу и
показывает только номера с явной пометкой WhatsApp в контактах самой фирмы.

    python3 wa_finder.py https://safement.kz/ru/contacts
    python3 wa_finder.py https://kazpan.com/ --phone "+7 700 300 00 67"
    python3 wa_finder.py --text page.md --phone 77018013950   # текст из web_fetch

Код возврата с --phone: 0 — номер подтверждён, 1 — нет. Признаки WhatsApp:
ссылки wa.me/…, api.whatsapp.com/send?phone=…, whatsapp://send?phone=…
и номер, рядом с которым в тексте написано WhatsApp / ватсап.
"""
import argparse
import html as htmllib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from staff_finder import fetch

LINK = re.compile(
    r"(?:wa\.me/|api\.whatsapp\.com/send/?\?(?:[^\"'\s>]*?&)?phone=|whatsapp://send\?phone=)"
    r"\+?(\d{10,15})", re.IGNORECASE)
PHONE = re.compile(r"(?<![\d+])(\+?\d[\d\s()‒–\-]{8,18}\d)(?!\d)")
LABEL = re.compile(r"whats\s?app|ватсап|вотсап|вацап", re.IGNORECASE)
# Всё ниже этих заголовков на 2ГИС — чужие фирмы и реклама.
FOREIGN_BLOCKS = ("Похожие организации", "Реклама", "На правах рекламы", "Жарнама")


def digits(s):
    d = re.sub(r"\D", "", str(s or ""))
    return "7" + d[1:] if len(d) == 11 and d.startswith("8") else d


def own_part(page, url=""):
    """Для 2ГИС отрезаем блоки чужих фирм: всё после первого их заголовка."""
    if "2gis." not in url and "2ГИС" not in page[:5000]:
        return page, False
    cut = min((i for i in (page.find(m) for m in FOREIGN_BLOCKS) if i > 0), default=-1)
    return (page[:cut], True) if cut > 0 else (page, False)


def find(page, url=""):
    """[{number, how, context}] — номера с признаком WhatsApp."""
    page = htmllib.unescape(page)
    part, cut = own_part(page, url)
    out, seen = [], set()
    for m in LINK.finditer(part):
        n = digits(m.group(1))
        if n not in seen:
            seen.add(n)
            out.append({"number": n, "how": "ссылка WhatsApp",
                        "context": re.sub(r"\s+", " ", part[max(0, m.start() - 80):m.end() + 20])})
    text = re.sub(r"<[^>]+>", " ", part)
    phones = list(PHONE.finditer(text))
    for i, m in enumerate(phones):
        n = digits(m.group(1))
        if len(n) < 10 or n in seen:
            continue
        # Пометка относится к номеру, только если стоит между ним и соседними номерами:
        # «+7 727 …, +7 771 … (WhatsApp)» — пометка второго, не первого.
        prev_end = phones[i - 1].end() if i else 0
        next_start = phones[i + 1].start() if i + 1 < len(phones) else len(text)
        before = text[max(prev_end, m.start() - 30):m.start()]
        after = text[m.end():min(next_start, m.end() + 30)]
        if LABEL.search(before) or LABEL.search(after):
            seen.add(n)
            out.append({"number": n, "how": "пометка WhatsApp рядом",
                        "context": re.sub(r"\s+", " ", before + m.group(1) + after).strip()})
    return out, cut


def main():
    ap = argparse.ArgumentParser(description="Номера WhatsApp на странице компании")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--text", help="файл с текстом или HTML страницы (из web_fetch)")
    ap.add_argument("--phone", help="номер, который надо подтвердить")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.text:
        page, url = Path(a.text).read_text(encoding="utf-8"), a.url or ""
    elif a.url:
        page, err = fetch(a.url)
        url = a.url
        if page is None:
            print(f"страница не открылась ({err}) → web_fetch и --text <файл>")
            return 2
    else:
        ap.error("нужен url или --text")
    found, cut = find(page, url)
    want = digits(a.phone) if a.phone else None
    ok = bool(want) and any(f["number"] == want for f in found)
    if a.json:
        print(json.dumps({"found": found, "cut_foreign_blocks": cut, "phone": want,
                          "confirmed": ok if want else None}, ensure_ascii=False, indent=1))
    else:
        if cut:
            print("2ГИС: блоки «Похожие организации» и реклама отброшены")
        for f in found:
            mark = "  ← совпадает" if want and f["number"] == want else ""
            print(f"+{f['number']}  {f['how']}{mark}\n    …{f['context']}…")
        if not found:
            print("номеров с пометкой WhatsApp не найдено")
        if want:
            print("ПОДТВЕРЖДЁН" if ok
                  else f"НЕ ПОДТВЕРЖДЁН: +{want} на странице без пометки WhatsApp")
    return 0 if (ok or not want) else 1


if __name__ == "__main__":
    sys.exit(main())
