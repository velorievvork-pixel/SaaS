#!/usr/bin/env python3
"""
Кандидаты корпоративной почты по ФИО руководителя и домену компании.

Зачем. 13 писем из 20 в пачке №1 ушли на общие ящики, и это одна из четырёх
причин, по которым пачка провалилась. При этом нужный ЛПР — гендиректор, а его
ФИО публично отдаётся по ИНН через ЕГРЮЛ-агрегаторы. Недостающее звено между
«знаем имя» и «знаем адрес» — раскладка имени в принятые в РФ шаблоны почты.

Скрипт НЕ ходит в сеть и ничего не проверяет. Он раскладывает ФИО в
транслитерацию и порядок шаблонов по убыванию вероятности. Проверка
существования адреса — отдельный шаг, вручную или через сервис верификации.

Использование:
    python3 contact_finder.py "Римский-Корсаков Владимир Александрович" konik.ru
    python3 contact_finder.py "Иванов Иван" example.ru --json
"""
import argparse
import json
import re
import sys

# Практическая транслитерация для почтовых адресов: без диакритики,
# «щ» как shch, «ю»/«я» как yu/ya, твёрдый и мягкий знаки выбрасываются.
TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(s):
    return "".join(TR.get(c, c) for c in s.lower())


def parse_fio(raw):
    """ФИО в порядке Фамилия Имя Отчество. Двойные фамилии через дефис сохраняются."""
    parts = [p for p in re.split(r"\s+", raw.strip()) if p]
    if not parts:
        raise ValueError("пустое ФИО")
    last = translit(parts[0]).replace("-", "")
    first = translit(parts[1]) if len(parts) > 1 else ""
    middle = translit(parts[2]) if len(parts) > 2 else ""
    return last, first, middle


def candidates(fio, domain):
    last, first, middle = parse_fio(fio)
    d = domain.strip().lower().lstrip("@").removeprefix("www.")
    f = first[:1]
    m = middle[:1]

    # Порядок — по убыванию частоты в российских компаниях среднего размера.
    pats = []
    if first:
        pats += [f"{f}.{last}", f"{f}{last}", f"{first}.{last}", f"{last}.{f}"]
    pats += [last]
    if first:
        pats += [f"{first}", f"{last}{f}", f"{first}_{last}", f"{last}_{first}"]
    if middle:
        pats += [f"{f}{m}{last}", f"{f}.{m}.{last}", f"{last}{f}{m}"]

    seen, out = set(), []
    for raw in pats:
        local = re.sub(r"[^a-z0-9._-]", "", raw)
        if local and local not in seen:
            seen.add(local)
            out.append(f"{local}@{d}")
    return out


GENERIC = ["info", "mail", "office", "sales", "zakaz", "hello", "ask", "pr", "support"]


def main():
    ap = argparse.ArgumentParser(description="Кандидаты почты руководителя")
    ap.add_argument("fio", help="ФИО: Фамилия Имя Отчество")
    ap.add_argument("domain", help="Домен компании, например konik.ru")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        c = candidates(a.fio, a.domain)
    except ValueError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps({"fio": a.fio, "domain": a.domain, "candidates": c},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"ФИО:    {a.fio}")
    print(f"Домен:  {a.domain}")
    print("\nКандидаты, по убыванию вероятности:")
    for i, e in enumerate(c, 1):
        print(f"  {i:2}. {e}")
    print("\nНЕ отправлять вслепую по всему списку — это ударит по доставляемости.")
    print("Проверить существование адреса, затем писать на один.")
    print(f"Общие ящики ({', '.join(GENERIC[:4])}@...) — последнее средство:")
    print("в пачке №1 именно они дали нулевой результат.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
