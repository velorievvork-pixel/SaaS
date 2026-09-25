"""Агент: разбор настоящих ответов лидов и переход действий в самостоятельный режим."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "camirix" / "agent"))
import autonomy
import router

# Настоящие ответы 24–25.09
REAL = [
    ("Здравствуйте! Передадим ваше сообщение и номер руководству, с вами свяжутся от Cheber group",
     "forwarded"),
    ("Здравствуйте! Меня зовут Радмила, я менеджер компании Агротоп. +7 700 760 0141 Виктория. "
     "Можете обратиться по этому номеру", "contact_given"),
    ("Здравствуйте. +7 777 178 9747", "contact_given"),
    ("Здравствуйте не интересует", "refusal"),
    ("Здравствуйте", "greeting"),
]


@pytest.mark.parametrize(("text", "category"), REAL)
def test_real_replies(text, category):
    assert router.classify(text)[0] == category


@pytest.mark.parametrize(("text", "category"), [
    ("Больше не пишите на этот номер", "opt_out"),
    ("Сколько стоит внедрение?", "price"),
    ("Пришлите КП на почту", "proposal"),
    ("Давайте созвонимся в среду", "meeting"),
    ("А это работает с 1С 8.3?", "question"),
    ("Спасибо за обращение! Мы ответим в ближайшее время.", "autoreply"),
    ("Хорошо", "unclear"),
    ("Передала руководителю, его почта director@example.kz", "contact_given"),
])
def test_other_replies(text, category):
    assert router.classify(text)[0] == category


def test_route_refusal_gives_close_text_and_status():
    r = router.route("Здравствуйте не интересует", "cbc-astana-kz")
    assert (r["action"], r["status"]) == ("reply_refusal", "refused")
    assert r["draft"] == "Понял, спасибо, что ответили! Хорошего дня."
    assert not r["needs_edit"]


def test_contact_draft_must_be_finished_by_agent():
    r = router.route("+7 700 760 0141 Виктория", "agrotop-kz")
    assert r["contacts"] and r["needs_edit"]


AGROTOP = ("Здравствуйте! Меня зовут Радмила, я менеджер компании Агротоп. +7 700 760 0141 Виктория. "
           "Можете обратиться по этому номеру")


def test_names_from_a_real_reply():
    assert router.their_name(AGROTOP) == "Радмила"
    assert router.contact_name(AGROTOP, "ТОО «Агротоп»") == "Виктория"
    drafts = router.route(AGROTOP, "agrotop-kz")["drafts"]
    assert drafts[0] == {"to": "them", "text": "Радмила, спасибо большое!"}
    assert drafts[1]["text"].startswith("Виктория, добрый день! Ваш номер мне дала Радмила из компании Агротоп.")


def test_indirect_case_name_is_not_used_as_greeting():
    drafts = router.draft("contact_given", {"company": "ООО «Ромашка»"}, "Звоните Ерлану +7 701 111 22 33")
    assert drafts[1]["text"].startswith("Добрый день! Ваш номер мне дали в компании Ромашка.")


def test_forwarded_asks_the_name_only_when_unknown():
    cheber = "Здравствуйте! Передадим ваше сообщение и номер руководству, с вами свяжутся от Cheber group"
    assert "как вас зовут" in router.route(cheber)["draft"]
    named = "Это Айгуль, передам руководству"
    assert router.route(named)["draft"] == "Айгуль, спасибо! Буду ждать."


def test_asked_if_bot_is_honest_and_goes_to_owner():
    r = router.route("Вы бот что ли?")
    assert (r["category"], r["action"]) == ("asked_if_bot", "reply_bot_question")
    assert "ассистент" in r["draft"]
    assert autonomy.decide("reply_bot_question")["mode"] == "ask"


@pytest.mark.parametrize("category", ["refusal", "forwarded", "price", "proposal", "asked_if_bot"])
def test_every_template_passes_the_human_check(category):
    import humanity
    for d in router.draft(category, {"company": "ТОО «Агротоп»"}, "Здравствуйте"):
        assert humanity.check(d["text"], "reply", "Здравствуйте, сколько стоит и как это работает у вас?") == []


def test_every_router_action_is_in_the_policy():
    actions = autonomy.load_policy()["actions"]
    assert {a for a in router.ACTION.values() if a} <= set(actions)


POLICY = {"graduate_after": 3, "never_auto": ["handoff"],
          "actions": {"first_message": "learn", "reply_opt_out": "auto", "handoff": "auto", "x": "ask"}}


def rec(action, outcome):
    return {"action": action, "outcome": outcome}


def test_learn_graduates_after_streak():
    log = [rec("first_message", "approved")] * 2
    assert autonomy.decide("first_message", POLICY, log)["mode"] == "ask"
    assert autonomy.decide("first_message", POLICY, [*log, rec("first_message", "approved")])["mode"] == "auto"


def test_edit_resets_the_streak():
    log = [rec("first_message", "approved")] * 3 + [rec("first_message", "edited")]
    assert autonomy.decide("first_message", POLICY, log)["mode"] == "ask"


def test_auto_falls_back_after_a_correction():
    assert autonomy.decide("reply_opt_out", POLICY, [])["mode"] == "auto"
    assert autonomy.decide("reply_opt_out", POLICY, [rec("reply_opt_out", "rejected")])["mode"] == "ask"


def test_never_auto_and_unknown_actions_ask():
    assert autonomy.decide("handoff", POLICY, [])["mode"] == "ask"
    assert autonomy.decide("something_new", POLICY, [])["mode"] == "ask"


def test_record_and_learn_write_files(tmp_path):
    log, lessons = tmp_path / "a.jsonl", tmp_path / "l.md"
    autonomy.record("first_message", "edited", "x", "было", "стало", path=log)
    assert autonomy.load_log(log)[0]["after"] == "стало"
    autonomy.learn("Писать процесс словами из вакансии", "«разноска Kaspi Pay»", path=lessons,
                   today=dt.date(2026, 9, 28))
    assert "**2026-09-28** (владелец): Писать процесс" in lessons.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        autonomy.record("first_message", "maybe", path=log)
