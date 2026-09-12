"""
Собирает компании-кандидаты с публичного API hh.ru по вакансиям, которые сигналят
о раннестадийном B2B SaaS без выстроенного отдела продаж (SDR/Sales Manager/AE).

Использует только официальный публичный API hh.ru (без авторизации, без скрейпинга
HTML-страниц) — https://github.com/hhru/api/blob/master/docs/vacancies.md

Запуск:
    pip install requests
    python hh_scraper.py

Результат: raw_leads.csv в этой же папке.
"""

import csv
import time
import requests

BASE_URL = "https://api.hh.ru"
AREA_RUSSIA = 113  # код региона "Россия" в справочнике hh.ru
PAGES_PER_QUERY = 5
PER_PAGE = 50

SEARCH_QUERIES = [
    "SaaS B2B",
    "SDR продажи",
    "Sales Development Representative",
    "Account Executive SaaS",
    "B2B платформа продажи",
]


def search_vacancies(query: str) -> dict:
    """Возвращает {employer_id: {company, hh_url, vacancy_title, vacancy_url}}."""
    employers = {}
    for page in range(PAGES_PER_QUERY):
        resp = requests.get(
            f"{BASE_URL}/vacancies",
            params={
                "text": query,
                "area": AREA_RUSSIA,
                "page": page,
                "per_page": PER_PAGE,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            employer = item.get("employer") or {}
            emp_id = employer.get("id")
            if not emp_id or emp_id in employers:
                continue
            employers[emp_id] = {
                "employer_id": emp_id,
                "company": employer.get("name", ""),
                "hh_url": employer.get("alternate_url", ""),
                "vacancy_title": item.get("name", ""),
                "vacancy_url": item.get("alternate_url", ""),
            }

        if page + 1 >= data.get("pages", 1):
            break
        time.sleep(0.3)  # не заваливаем публичный API запросами

    return employers


def enrich_with_site_url(employer_id: str) -> str:
    """Достаёт сайт компании из карточки работодателя, если указан."""
    try:
        resp = requests.get(f"{BASE_URL}/employers/{employer_id}", timeout=15)
        resp.raise_for_status()
        return resp.json().get("site_url", "") or ""
    except requests.RequestException:
        return ""


def main() -> None:
    all_employers: dict[str, dict] = {}

    for query in SEARCH_QUERIES:
        print(f"Поиск: {query!r}")
        found = search_vacancies(query)
        all_employers.update(found)
        print(f"  найдено новых компаний: {len(found)}")

    print(f"\nВсего уникальных компаний: {len(all_employers)}")
    print("Достаю сайты компаний (может занять пару минут)...")

    rows = []
    for emp_id, info in all_employers.items():
        info["site_url"] = enrich_with_site_url(emp_id)
        rows.append(info)
        time.sleep(0.2)

    out_path = "raw_leads.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["company", "site_url", "hh_url", "vacancy_title", "vacancy_url"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})

    print(f"Готово: {out_path} ({len(rows)} строк)")
    print("Дальше: вручную проверьте компании (размер, действительно ли SaaS,"
          " есть ли уже отдел продаж) и перенесите подходящие в CRM.")


if __name__ == "__main__":
    main()
