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
    assert r["draft"] == "Понял, спасибо, что ответили. Если ситуация изменится, пишите."
    assert not r["needs_edit"]


def test_refusal_uses_topic_from_card():
    card = {"topic": "с заявками из регионов что-то"}
    assert router.draft("refusal", card).endswith("Если с заявками из регионов что-то изменится, пишите.")


def test_contact_draft_must_be_finished_by_agent():
    r = router.route("+7 700 760 0141 Виктория", "agrotop-kz")
    assert r["contacts"] and r["needs_edit"]


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
