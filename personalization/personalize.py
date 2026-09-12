"""
Берёт CSV со списком компаний и генерирует персонализированную вступительную строку
холодного сообщения для каждой через Claude API.

Входной CSV (companies.csv), колонки:
    company  — название компании (обязательно)
    domain   — домен сайта, например example.ru (обязательно)
    about    — короткое описание компании, 1 предложение (опционально, но сильно
               повышает качество персонализации)

Выходной CSV (output.csv): те же колонки + personalized_line.

Установка (один раз):
    pip install anthropic requests

Запуск:
    export ANTHROPIC_API_KEY=sk-ant-...
    python personalize.py companies.csv output.csv
"""

import csv
import os
import sys
import re
import time

import requests
from anthropic import Anthropic

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
Ты — ассистент SDR-агентства, которое помогает B2B SaaS-компаниям в России и СНГ
находить первых клиентов через холодные продажи.

Твоя задача: по названию компании, домену и краткой информации о ней написать ОДНУ
персонализированную вступительную строку для холодного сообщения (email/Telegram)
основателю или CEO этой компании.

Правила:
- 1-2 коротких предложения, не больше 30 слов.
- Только на русском языке.
- Основывайся ТОЛЬКО на предоставленных фактах о компании. Если фактов мало —
  делай общее, но правдоподобное наблюдение (например, о рынке/нише компании),
  никогда не выдумывай раунды инвестиций, цифры или события, которых нет во входных
  данных.
- Никаких клише: "Заметил, что вы...", "Наткнулся на ваш сайт...", "Я изучил ваш продукт и...".
- Тон: как коллега-предприниматель, а не продавец. Конкретика вместо комплиментов.
- Не упоминай в этой строке предложение услуг SDR — это только открывающая строка.
- Верни ТОЛЬКО текст строки, без кавычек, без пояснений.
"""


def fetch_meta_description(domain: str) -> str:
    """Лёгкая попытка достать meta description с сайта компании, если about пуст.
    Не парсит весь сайт, не использует сторонние библиотеки — только og:description
    или обычный <meta name="description">. При любой ошибке возвращает пустую строку.
    """
    for scheme in ("https://", "http://"):
        try:
            resp = requests.get(f"{scheme}{domain}", timeout=8)
            if resp.status_code != 200:
                continue
            html = resp.text
            match = re.search(
                r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])'
                r'[^>]+content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()[:300]
        except requests.RequestException:
            continue
    return ""


def generate_line(client: Anthropic, company: str, domain: str, about: str) -> str:
    user_prompt = f"Компания: {company}\nДомен: {domain}\nКраткая информация о компании (если есть): {about}\n\nНапиши персонализированную вступительную строку по правилам выше."
    response = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


def main() -> None:
    if len(sys.argv) != 3:
        print("Использование: python personalize.py companies.csv output.csv")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Ошибка: задайте переменную окружения ANTHROPIC_API_KEY")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    with open(input_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, 1):
        company = row.get("company", "").strip()
        domain = row.get("domain", "").strip()
        about = row.get("about", "").strip()

        if not about and domain:
            about = fetch_meta_description(domain)

        print(f"[{i}/{len(rows)}] {company} ...")
        try:
            row["personalized_line"] = generate_line(client, company, domain, about)
        except Exception as exc:
            print(f"  ошибка: {exc}")
            row["personalized_line"] = ""

        time.sleep(0.3)  # аккуратно относимся к rate limit'ам

    fieldnames = list(rows[0].keys()) if rows else ["company", "domain", "about", "personalized_line"]
    if "personalized_line" not in fieldnames:
        fieldnames.append("personalized_line")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nГотово: {output_path}")


if __name__ == "__main__":
    main()
