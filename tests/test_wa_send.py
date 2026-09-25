"""wa_send.py: проверки текста перед WhatsApp и разбор ответов шлюза (без сети)."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "camirix"))
import wa_send

FIRST = ("Здравствуйте! Меня зовут Ярослав, я из компании Camirix. Прошу передать сообщение директору, "
         "Светлане Александровне Лазко. Вижу, что в Алматы вы ищете бухгалтера-оператора на реализации, "
         "возвраты, ЭСФ и разноску Kaspi Pay. Такой поток однотипных документов мы автоматизируем поверх 1С, "
         "чтобы с ростом заказов не приходилось добавлять операторов. "
         "Подскажите, как лучше связаться со Светланой Александровной?")
ENV = {"GREEN_API_URL": "http://gw", "GREEN_API_ID": "1101", "GREEN_API_TOKEN": "secret-token-123456"}


class FakeResp(io.BytesIO):
    def __init__(self, status, payload):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestGuard(unittest.TestCase):
    def test_old_first_message_wastes_the_preview(self):
        # Отправленный 25.09 текст: первые 80 символов — «Меня зовут…, я из компании…».
        self.assertTrue(any(r.startswith("H7") for r in wa_send.guard(FIRST, "first")))

    def test_first_message_that_starts_with_them_passes(self):
        text = ("Добрый день! Вижу, что в Алматы вы ищете бухгалтера-оператора на реализации, ЭСФ "
                "и разноску Kaspi Pay. Я Ярослав из Camirix, мы автоматизируем такие документы "
                "поверх 1С, чтобы с ростом заказов не приходилось добавлять людей. Подскажите, "
                "как лучше связаться со Светланой Александровной?")
        self.assertEqual(wa_send.guard(text, "first"), [])

    def test_short_reply_passes_only_as_reply(self):
        text = "Понял, спасибо, что ответили. Если с заявками из регионов что-то изменится, пишите."
        self.assertEqual(wa_send.guard(text, "reply"), [])
        self.assertTrue(any(r.startswith("W1") for r in wa_send.guard(text, "first")))

    def test_money_is_blocked_even_in_a_reply(self):
        reasons = wa_send.guard("Внедрение стоит 150 000 ₽, созвонимся?", "reply")
        self.assertTrue(any(r.startswith("F1") for r in reasons))


class TestHumanVoice(unittest.TestCase):
    def test_bureaucratic_reply_is_blocked(self):
        bad = "Благодарим за ваш ответ! Будем рады сотрудничеству. С уважением, Ярослав"
        reasons = wa_send.guard(bad, "reply")
        self.assertTrue(sum(r.startswith("H1") for r in reasons) >= 3, reasons)

    def test_reply_much_longer_than_theirs_is_blocked(self):
        long = " ".join(["слово"] * 40) + "."
        self.assertTrue(any(r.startswith("H3") for r in wa_send.guard(long, "reply", "не интересует")))

    def test_unfilled_template_is_blocked(self):
        self.assertTrue(any(r.startswith("H4") for r in wa_send.guard("Виктория, добрый день! Скажите, …?", "reply")))

    def test_human_reply_passes(self):
        self.assertEqual(wa_send.guard("Понял, спасибо, что ответили! Хорошего дня.", "reply", "не интересует"), [])


class TestSend(unittest.TestCase):
    def test_ok(self):
        calls = []

        def opener(req, timeout):
            calls.append(req)
            return FakeResp(200, {"idMessage": "ABC"})

        status, payload = wa_send.send("8 701 123 45 67", "Здравствуйте", env=ENV, opener=opener)
        self.assertEqual((status, payload), (200, {"idMessage": "ABC"}))
        self.assertEqual(calls[0].full_url, "http://gw/waInstance1101/sendMessage/secret-token-123456")
        self.assertEqual(json.loads(calls[0].data)["chatId"], "77011234567@c.us")

    def test_refusal_is_returned_not_raised(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 425, "Too Early", {},
                                         io.BytesIO(b'{"error":"off_hours","localTime":"2026-09-29T20:00"}'))

        status, payload = wa_send.send("77011234567", "x", env=ENV, opener=opener)
        self.assertEqual((status, payload["error"]), (425, "off_hours"))

    def test_no_settings(self):
        with self.assertRaises(LookupError):
            wa_send.send("77011234567", "x", env={})


if __name__ == "__main__":
    unittest.main()
