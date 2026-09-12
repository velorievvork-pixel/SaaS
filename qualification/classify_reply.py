"""
Классифицирует входящий ответ лида и предлагает черновик ответа через Claude API.

Установка:
    pip install anthropic

Запуск:
    export ANTHROPIC_API_KEY=sk-ant-...
    python classify_reply.py "текст ответа от лида" [--company "Название компании"]
"""

import argparse
import json
import os
import sys

from anthropic import Anthropic

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
Ты помогаешь SDR-агентству обрабатывать ответы на холодные сообщения от основателей
и CEO B2B SaaS-компаний.

По тексту входящего ответа определи категорию и напиши короткий черновик ответа.

Категории (выбери ровно одну):
- "Заинтересован" — просит подробности, готов на звонок/встречу, задаёт вопросы по услуге
- "Не сейчас" — вежливый отказ с открытой дверью на будущее
- "Не актуально" — явный отказ без намерения продолжать
- "Другое" — автоответ, смена контакта, вопрос не по теме, спам-жалоба и т.п.

Черновик ответа:
- Для "Заинтересован": предложи конкретное время для 15-минутного звонка, коротко.
- Для "Не сейчас": поблагодари, спроси, когда уместно вернуться, не дави.
- Для "Не актуально": короткая благодарность за ответ, без попытки переубедить.
- Для "Другое": короткий уточняющий вопрос или благодарность, по ситуации.

Отвечай в формате JSON:
{"category": "...", "draft_reply": "..."}

Никакого текста вне JSON.
"""


def classify(client: Anthropic, company: str, reply_text: str) -> dict:
    user_prompt = f'Компания: {company}\nТекст ответа от контакта:\n"""\n{reply_text}\n"""'
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text.strip()
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reply_text", help="Текст ответа от лида")
    parser.add_argument("--company", default="(не указана)", help="Название компании")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Ошибка: задайте переменную окружения ANTHROPIC_API_KEY")
        sys.exit(1)

    client = Anthropic(api_key=api_key)
    result = classify(client, args.company, args.reply_text)

    print(f"Категория: {result.get('category')}")
    print(f"Черновик ответа:\n{result.get('draft_reply')}")


if __name__ == "__main__":
    main()
