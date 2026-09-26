"""
Дымовые тесты двух шлюзов.

Эти скрипты решают, уйдёт ли письмо живому человеку, поэтому их поведение
закреплено тестами, а не проверяется вручную каждый раз. Все случаи ниже —
настоящие провалы 22.09, а не выдуманные примеры.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "outbound_guard.py"
sys.path.insert(0, str(ROOT / "camirix"))

# Настоящее письмо, отправленное Арианстару 22.09. Эталон «чистого» текста.
CLEAN = (
    "Возвращаюсь по вакансии руководителя продаж, и если адрес не тот, буду "
    "признателен за переадресацию. Переход из Excel в Bitrix24 чаще спотыкается "
    "не о саму CRM, а о сведение с 1С: пока отгрузки и остатки переносятся руками, "
    "данные отстают на день-два и доверие к системе падает. У вас 1С и CRM "
    "планируете связывать?"
)


def guard(body, to="foodsafety@arianstar.ru", subject="Тема"):
    """Возвращает (allowed, reason). Пустой stdout = пропущено."""
    ev = {"tool_name": "mcp__Gmail__send_message",
          "tool_input": {"to": [to], "subject": subject, "body": body}}
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(ev),
                       capture_output=True, text=True, cwd="/tmp")
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return True, ""
    d = json.loads(p.stdout)
    assert d["hookSpecificOutput"]["permissionDecision"] == "deny"
    return False, d["hookSpecificOutput"]["permissionDecisionReason"]


class TestOutboundGuard:
    def test_clean_message_passes(self):
        allowed, _ = guard(CLEAN)
        assert allowed, "настоящее отправленное письмо должно проходить"

    def test_unrelated_tool_untouched(self):
        p = subprocess.run([sys.executable, str(GUARD)],
                           input=json.dumps({"tool_name": "Read", "tool_input": {}}),
                           capture_output=True, text=True)
        assert p.stdout.strip() == ""

    @pytest.mark.parametrize("sum_text", ["29 000 ₽", "от 50 тыс руб", "1 млн ₽"])
    def test_any_sum_blocked(self, sum_text):
        # Прайса нет: цену называет владелец на звонке под запрос покупателя.
        allowed, why = guard(CLEAN.replace("падает.", f"падает. Это {sum_text}."))
        assert not allowed and "F1" in why

    def test_fabricated_result_blocked(self):
        allowed, why = guard(CLEAN.replace("падает.", "падает. Мы помогли вырасти на 40%."))
        assert not allowed and "F4" in why

    def test_confirmed_sender_allowed(self):
        # 22.09: пользователь договорился с Camirix, sender_identity стал
        # confirmed. «Мы в Camirix» больше не блокируется.
        allowed, _ = guard(CLEAN + " Мы в Camirix это решаем.")
        assert allowed

    def test_sender_blocked_when_unconfirmed(self, tmp_path, monkeypatch):
        # Логика S1 всё ещё обязана блокировать, если статус когда-нибудь
        # снова станет unconfirmed. Проверяем не через живой facts.yaml
        # (он меняется), а через временную копию с изменённым статусом.
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import outbound_guard as g

        real = (ROOT / "camirix" / "facts.yaml").read_text(encoding="utf-8")
        fake = real.replace(
            "sender_identity:\n  status: confirmed",
            "sender_identity:\n  status: unconfirmed",
        )
        assert fake != real, "не нашли sender_identity: status: confirmed в facts.yaml"
        fake_facts = tmp_path / "facts.yaml"
        fake_facts.write_text(fake, encoding="utf-8")
        monkeypatch.setattr(g, "FACTS", fake_facts)

        out = g.check_send({
            "to": ["foodsafety@arianstar.ru"],
            "subject": "Тема",
            "body": CLEAN + " Мы в Camirix это решаем.",
        })
        assert any("S1" in r for r in out)

    def test_job_application_opener_to_generic_box_blocked(self):
        # МЕЙКИНИМ прочитали такое открытие как отклик соискателя.
        allowed, why = guard("Пишу по вакансии менеджера. " + CLEAN, to="info@avido.by")
        assert not allowed and "S2" in why

    def test_banned_word_blocked(self):
        allowed, why = guard(CLEAN.replace("чаще", "обычно"))
        assert not allowed and "W4" in why

    def test_two_ctas_blocked(self):
        allowed, why = guard(CLEAN + " Удобно созвониться?")
        assert not allowed and "W2" in why

    def test_too_long_blocked(self):
        allowed, why = guard(CLEAN + " " + "ещё слово " * 35)
        assert not allowed and "W1" in why

    def test_truncated_blocked(self):
        allowed, why = guard("Добрый день, пара вопросов.")
        assert not allowed and "W1" in why

    def test_empty_body_fails_closed(self):
        allowed, _ = guard("")
        assert not allowed, "пустое тело нечего проверять, значит нельзя отправлять"


@pytest.fixture
def good_lead():
    return yaml.safe_load("""
company: ООО «Коник»
inn: "7802361565"
verified_on: "{today}"
domain: konik.ru
vacancy: {url: "https://hh.ru/vacancy/1", status: active, checked_on: "{today}",
          title: Менеджер, salary: от 90 000 ₽}
firmographics: {headcount: 54, revenue_mln_rub: 458.9,
                source_url: "https://checko.ru/company/7802361565"}
decision_maker: {name: Римский-Корсаков Владимир Александрович,
                 role: генеральный директор, source_url: "https://checko.ru/c"}
contact: {channel: email, value: v.rimskiykorsakov@konik.ru, how_found: contact_finder}
signal: {type: массовый наём, quote: "обработка входящих заявок от оптовых клиентов",
         source_url: "https://hh.ru/vacancy/1"}
process_to_automate: обработка входящих оптовых заявок и оформление отгрузки
identity_check: >
  юр.адрес в карточке checko.ru совпадает с адресом на сайте konik.ru,
  в футере сайта указан тот же ИНН 7802361565
""".replace("{today}", dt.date.today().isoformat()))


class TestLeadGate:
    def test_complete_lead_passes(self, good_lead):
        from lead_gate import check
        assert check(good_lead) == []

    def test_archived_vacancy_rejected(self, good_lead):
        # Архив на hh.ru = истёкшие 30 дней размещения, а не закрытая позиция.
        from lead_gate import check
        good_lead["vacancy"]["status"] = "archived"
        assert any(p.startswith("L1") for p in check(good_lead))

    def test_stale_vacancy_check_rejected(self, good_lead):
        from lead_gate import check
        good_lead["vacancy"]["checked_on"] = "2026-06-06"
        assert any(p.startswith("L1") for p in check(good_lead))

    def test_sales_head_is_not_the_decider(self, good_lead):
        # Решение «расти без расширения штата» принимает тот, у кого ФОТ в отчётности.
        from lead_gate import check
        good_lead["decision_maker"]["role"] = "коммерческий директор"
        assert any(p.startswith("L3") for p in check(good_lead))

    @pytest.mark.parametrize("box", ["info@konik.ru", "sales@konik.ru", "office@konik.ru"])
    def test_generic_mailbox_rejected(self, good_lead, box):
        from lead_gate import check
        good_lead["contact"]["value"] = box
        assert any(p.startswith("L4") for p in check(good_lead))

    def test_paraphrased_quote_rejected(self, good_lead):
        from lead_gate import check
        good_lead["signal"]["quote"] = "автоматизация"
        assert any(p.startswith("L5") for p in check(good_lead))

    def test_out_of_icp_rejected(self, good_lead):
        from lead_gate import check
        good_lead["firmographics"]["headcount"] = 316
        assert any(p.startswith("L6") for p in check(good_lead))

    def test_empty_card_fails_everything(self):
        from lead_gate import check
        assert len(check({})) >= 7

    def test_ru_lead_without_revenue_rejected(self, good_lead):
        # РФ: audit-it.ru отдаёт выручку бесплатно, поэтому для РФ она обязательна.
        from lead_gate import check
        del good_lead["firmographics"]["revenue_mln_rub"]
        assert any(p.startswith("L2") for p in check(good_lead))

    def test_non_ru_lead_without_revenue_passes(self, good_lead):
        # СНГ вне РФ: агрегаторы часто прячут финансы за платным отчётом (23.09,
        # решение пользователя на примере AZMT/Казахстан) — численности достаточно.
        from lead_gate import check
        good_lead["country"] = "KZ"
        del good_lead["firmographics"]["revenue_mln_rub"]
        assert check(good_lead) == []

    def test_low_end_of_widened_icp_passes(self, good_lead):
        # Нижняя граница снижена с 30 до 20 — 23.09, решение пользователя.
        from lead_gate import check
        good_lead["firmographics"]["headcount"] = 21
        assert check(good_lead) == []

    def test_below_widened_icp_floor_rejected(self, good_lead):
        from lead_gate import check
        good_lead["firmographics"]["headcount"] = 19
        assert any(p.startswith("L6") for p in check(good_lead))

    def test_bare_director_title_accepted(self, good_lead):
        # ТОО (Казахстан и часть СНГ): «Директор» — стандартный титул первого лица,
        # не «коммерческий директор» из батча №1.
        from lead_gate import check
        good_lead["decision_maker"]["role"] = "Директор"
        assert check(good_lead) == []

    def test_commercial_director_still_rejected(self, good_lead):
        # Составной титул с «директор» не должен проскакивать через L3 по подстроке.
        from lead_gate import check
        good_lead["decision_maker"]["role"] = "финансовый директор"
        assert any(p.startswith("L3") for p in check(good_lead))

    def test_missing_identity_check_rejected(self, good_lead):
        # 23.09: дважды чуть не взял тёзку (книгоиздательская «Группа Традиция»
        # вместо промышленного холдинга, «Food City» вместо «Food City Group»).
        from lead_gate import check
        del good_lead["identity_check"]
        assert any(p.startswith("L8") for p in check(good_lead))

    def test_stub_identity_check_rejected(self, good_lead):
        from lead_gate import check
        good_lead["identity_check"] = "да"
        assert any(p.startswith("L8") for p in check(good_lead))


class TestContactFinder:
    @pytest.mark.parametrize("fio,expected", [
        ("Щербаков Юрий Яковлевич", "y.shcherbakov@x.ru"),
        ("Ильин Пётр Сергеевич", "p.ilin@x.ru"),
        ("Цой Виктор Робертович", "v.tsoy@x.ru"),
        ("Римский-Корсаков Владимир", "v.rimskiykorsakov@x.ru"),
    ])
    def test_transliteration(self, fio, expected):
        from contact_finder import candidates
        assert candidates(fio, "x.ru")[0] == expected

    def test_no_duplicates(self):
        from contact_finder import candidates
        c = candidates("Иванов Иван Иванович", "x.ru")
        assert len(c) == len(set(c))


class TestContactedRegistry:
    """L9: повторное первое касание. 23.09 агент принёс ЗЕНИТ-НОВА как новый лид."""

    REG = (("зенит-нова", {"zenitnova.by"}, "2026-09-22", "email"),
           ("завод промышленных сит пмк", {"1pmk.kz"}, "2026-09-24", "whatsapp"))

    def test_same_domain_rejected(self, good_lead):
        from lead_gate import check
        good_lead["domain"] = "www.zenitnova.by"
        assert any(x.startswith("L9") for x in check(good_lead, self.REG))

    def test_same_company_other_org_form_rejected(self, good_lead):
        from lead_gate import check
        good_lead["domain"] = "other.kz"
        good_lead["company"] = "ТОО «Завод Промышленных Сит ПМК» (бренд «Первая Метизная Компания»)"
        assert any(x.startswith("L9") for x in check(good_lead, self.REG))

    def test_new_company_passes(self, good_lead):
        from lead_gate import check
        assert not any(x.startswith("L9") for x in check(good_lead, self.REG))

    def test_sent_card_is_not_its_own_duplicate(self, good_lead):
        from lead_gate import check
        good_lead["domain"] = "zenitnova.by"
        good_lead["sent_on"] = "2026-09-22"
        assert not any(x.startswith("L9") for x in check(good_lead, self.REG))

    def test_multi_domain_field_split(self):
        from lead_gate import norm_domain
        assert norm_domain("ironplast.group / ironplast.kz") == {"ironplast.group", "ironplast.kz"}
        assert norm_domain("https://www.svarka.kz/") == {"svarka.kz"}


class TestIndependentVerification:
    """L10: 24.09 из шести карточек агентов сначала перепроверили только две."""

    def test_unverified_card_rejected(self, good_lead):
        from lead_gate import check
        good_lead.pop("verified_on")
        assert any(x.startswith("L10") for x in check(good_lead, ()))

    def test_verified_card_passes(self, good_lead):
        from lead_gate import check
        assert not any(x.startswith("L10") for x in check(good_lead, ()))


class TestStaffFinder:
    """staff_finder.py на реальных случаях 24.09."""

    def test_ceo_on_other_mail_domain(self):
        from staff_finder import extract
        t = ("Руководители\nГенеральный директор ООО \"Костромской завод полимерной упаковки\"\n"
             "Акинфова Виктория Артуровна\nТелефон +7 (4942) 45-48-11\nE-mail\n"
             "[v.akinfova@mptech.pro](mailto:v.akinfova@mptech.pro)\nНаписать сообщение")
        r = extract(t, "kzpu.pro")[0]
        assert r["email"] == "v.akinfova@mptech.pro"
        assert r["level"] == "top" and r["name"] == "Акинфова Виктория Артуровна"
        assert r["foreign_domain"] and not r["generic"]

    def test_mailto_hidden_behind_generic_text(self):
        from staff_finder import extract, to_text
        page = '<p>ОФИС: Челябинск</p><a href="mailto:pinaev.d@tst-ur.ru">74@tst-ur.ru</a>'
        text, mailtos = to_text(page)
        got = {r["email"]: r for r in extract(text, "tst-ur.ru", mailtos)}
        assert "pinaev.d@tst-ur.ru" in got and not got["pinaev.d@tst-ur.ru"]["generic"]
        assert got["74@tst-ur.ru"]["generic"]

    def test_amk_layout_role_box_duplicates_and_kazakh_names(self):
        from staff_finder import extract
        t = ("Генеральный директор Спиридонова Мария general@amk.kz general@amk.kz НАПИСАТЬ "
             "Системный администратор Волков Юрий ceo@amk.kz ceo@amk.kz НАПИСАТЬ "
             "HR менеджер Даулетқызы Алия hr@amk.kz hr@amk.kz НАПИСАТЬ")
        got = {r["email"]: r for r in extract(t, "amk.kz")}
        assert got["general@amk.kz"]["level"] == "top" and not got["general@amk.kz"]["generic"]
        assert got["ceo@amk.kz"]["name"] == "Волков Юрий" and got["ceo@amk.kz"]["level"] == "staff"
        assert got["hr@amk.kz"]["name"] == "Даулетқызы Алия" and got["hr@amk.kz"]["level"] == "staff"

    def test_demo_template_dropped_and_generic_flagged(self):
        from staff_finder import extract
        t = "Иванов Иван\nМенеджер\nivanov@site.ru\nОтдел продаж\ninfo@firma.ru"
        got = {r["email"]: r for r in extract(t, "firma.ru")}
        assert "ivanov@site.ru" not in got
        assert got["info@firma.ru"]["generic"]

    def test_commercial_director_is_not_first_person(self):
        from staff_finder import extract
        t = "Коммерческий директор\nПетров Пётр\nE-mail petrov@firma.ru"
        assert extract(t, "firma.ru")[0]["level"] == "mid"

    def test_role_does_not_leak_from_previous_card(self):
        from staff_finder import extract
        t = ("Генеральный директор\nСидоров Семён\nsidorov@firma.ru\n"
             "Менеджер\nКозлова Анна\nkozlova@firma.ru")
        got = {r["email"]: r for r in extract(t, "firma.ru")}
        assert got["sidorov@firma.ru"]["level"] == "top"
        assert got["kozlova@firma.ru"]["level"] == "staff"


class TestFotContributions:
    """fot.py: 24.09 плоские 30% давали ложное расхождение >30% у МСП (КЗПУ 37%, Плитстройторг 43%)."""

    def test_msp_reduced_rate_above_threshold(self):
        from fot import monthly_contrib
        # 2025: 30% до 1,5 МРОТ (33 660 ₽), 15% сверх
        assert round(monthly_contrib(71_400, 2025, True)) == round(0.3 * 33_660 + 0.15 * (71_400 - 33_660))

    def test_kzpu_contributions_now_reconcile(self):
        from fot import monthly_contrib
        expected = 83 * 12 * monthly_contrib(71_400, 2025, True)
        assert abs(expected - 13.5e6) / 13.5e6 < 0.30

    def test_non_msp_flat_30_below_limit(self):
        from fot import monthly_contrib
        assert monthly_contrib(100_000, 2025, False) == 30_000

    def test_msp_detection(self):
        from fot import is_msp
        assert is_msp(114, 1819) and not is_msp(528, 900) and not is_msp(100, 4100)


class TestDuplicateKeys:
    """24.09: пустой verified_on ниже по файлу перетирал заполненный."""

    def test_duplicate_key_rejected(self):
        from lead_gate import load_lead
        with pytest.raises(yaml.constructor.ConstructorError):
            load_lead('verified_on: "2026-09-24"\ncompany: X\nverified_on: ""\n')

    def test_unique_keys_load(self):
        from lead_gate import load_lead
        assert load_lead('company: X\nverified_on: "2026-09-24"\n')["company"] == "X"


class TestWaInbox:
    """wa_inbox.py: ответы из Green-API и авто-«Молчат». Данные — как на странице 24.09."""

    ROWS: ClassVar[list] = [
        {"id": "welding-company-kz", "company": "WELDING", "phone": "+7 702 243 26 27",
         "status": "sent", "reply": "", "version": 2,
         "history": [{"at": "2026-09-24T11:05:00Z", "text": "Отправил"}]},
        {"id": "amk-metiz-kz", "company": "АМК-Метиз", "phone": "+7 701 762 06 39",
         "status": "replied", "reply": "Ваш контакт передан IT отделу.", "version": 3,
         "history": [{"at": "2026-09-24T08:30:00Z", "text": "Отправил"}]},
        {"id": "office-expert-kz", "company": "Office-Expert", "phone": "",
         "status": "no_whatsapp", "reply": "", "version": 2, "history": []},
    ]

    def test_only_sent_leads_are_registered_with_the_gateway(self):
        import io
        import json as _json

        import wa_inbox
        rows = [*self.ROWS, {"id": "new-lead", "phone": "+7 777 000 11 22", "status": "new", "history": []}]
        assert wa_inbox.sent_phones(rows) == ["77017620639", "77022432627"]
        bodies = []

        def opener(req, timeout):
            bodies.append((req.full_url.split("/")[-2], req.get_method(),
                           _json.loads(req.data) if req.data else None))
            return io.BytesIO(b'{"added": 2}')

        env = {"GREEN_API_ID": "7103", "GREEN_API_TOKEN": "secret"}
        assert wa_inbox.register_leads(rows, env=env, opener=opener) == 2
        assert bodies == [("wagateAllow", "POST", {"phones": ["77017620639", "77022432627"]})]

    def test_silent_after_three_workdays_not_calendar_days(self):
        import wa_inbox
        at = dt.datetime.fromisoformat
        # Отправлено в чт 24.09: пн 28.09 — только 2 рабочих дня (пт, пн).
        silent, waiting = wa_inbox.silence_check(self.ROWS, at("2026-09-28T12:00:00+00:00"))
        assert not silent and [w["id"] for w in waiting] == ["welding-company-kz"]
        silent, _ = wa_inbox.silence_check(self.ROWS, at("2026-09-29T12:00:00+00:00"))
        assert [s["id"] for s in silent] == ["welding-company-kz"]
        assert silent[0]["version"] == 2  # для if_version при записи

    def test_matches_reply_by_phone_ignores_groups_and_strangers(self):
        import wa_inbox
        msgs = [
            {"chatId": "77022432627@c.us", "timestamp": 1790400000, "typeMessage": "textMessage",
             "textMessage": "Директор в отпуске до понедельника", "idMessage": "A1"},
            {"chatId": "120363153000000000@g.us", "timestamp": 1790400001,
             "typeMessage": "textMessage", "textMessage": "группа"},
            {"chatId": "79990000000@c.us", "timestamp": 1790400002,
             "typeMessage": "textMessage", "textMessage": "чужой"},
            {"chatId": "77022432627@c.us", "timestamp": 1790400003,
             "typeMessage": "audioMessage"},
        ]
        got = wa_inbox.match_replies(self.ROWS, msgs)
        assert [g["id"] for g in got] == ["welding-company-kz"] * 2
        assert got[0]["text"].startswith("Директор в отпуске")
        assert got[1]["text"] == "[audioMessage]"  # голосовое — слушать самому
        assert not got[0]["before_sent"]

    def test_already_handled_reply_is_not_shown_again(self):
        import copy

        import wa_inbox
        rows = copy.deepcopy(self.ROWS)
        msg = {"chatId": "77022432627@c.us", "timestamp": 1790400000, "typeMessage": "textMessage",
               "textMessage": "Да, интересно", "idMessage": "A1"}
        assert len(wa_inbox.match_replies(rows, [msg])) == 1
        for r in rows:
            if r["id"] == "welding-company-kz":
                r.setdefault("history", []).append({"text": "Ответ разобран", "id_message": "A1"})
        assert wa_inbox.match_replies(rows, [msg]) == []

    def test_eight_prefix_normalized(self):
        import wa_inbox
        assert wa_inbox.digits("8 (701) 762-06-39") == wa_inbox.digits("+7 701 762 06 39")

    def test_refuses_any_sending_method(self):
        import wa_inbox
        env = {"GREEN_API_ID": "1", "GREEN_API_TOKEN": "t"}
        with pytest.raises(ValueError):
            wa_inbox.api_call("sendMessage", env=env, opener=lambda *a, **k: None)

    def test_poll_skips_journal_when_not_authorized(self):
        import io

        import wa_inbox
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            return io.BytesIO(b'{"stateInstance": "notAuthorized"}')

        env = {"GREEN_API_ID": "7103", "GREEN_API_TOKEN": "secret"}
        state, replies = wa_inbox.poll(self.ROWS, 60, env=env, opener=opener)
        assert state == "notAuthorized" and replies == []
        assert len(calls) == 1 and "/waInstance7103/getStateInstance/" in calls[0]

    def test_http_error_does_not_leak_token(self):
        import urllib.error

        import wa_inbox

        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        env = {"GREEN_API_ID": "1", "GREEN_API_TOKEN": "supersecret"}
        with pytest.raises(RuntimeError) as e:
            wa_inbox.api_call("getStateInstance", env=env, opener=opener)
        assert "supersecret" not in str(e.value)

    def test_loads_artifactdata_dump(self, tmp_path):
        import wa_inbox
        d = tmp_path / "outbox"
        d.mkdir()
        (d / "welding-company-kz.json").write_text(json.dumps(
            {"id": "welding-company-kz", "version": 2, "data": self.ROWS[0]}), encoding="utf-8")
        rows = wa_inbox.load_outbox(tmp_path)
        assert rows[0]["id"] == "welding-company-kz" and rows[0]["version"] == 2


class TestTemplateSkipped:
    def test_glob_ignores_template(self):
        leads = sorted(str(p) for p in (ROOT / "camirix" / "leads").glob("*.yaml"))
        r = subprocess.run([sys.executable, str(ROOT / "camirix" / "lead_gate.py"), *leads,
                            "--brief"], capture_output=True, text=True)
        assert "_TEMPLATE" not in r.stdout
        assert f"из {len(leads) - 1}" in r.stdout


class TestStaffFinderBatch:
    """Пакетный режим обратной воронки: список доменов → именные адреса первых лиц."""

    def test_read_domains_strips_urls_and_comments(self, tmp_path):
        import staff_finder
        f = tmp_path / "d.txt"
        f.write_text("# из поиска 25.09\nhttps://www.kzpu.pro/company/staff/\nTST-UR.ru  # ЛПР?\n\n",
                     encoding="utf-8")
        assert staff_finder.read_domains(f) == ["kzpu.pro", "tst-ur.ru"]

    def test_top_candidates_only_named_first_persons(self):
        import staff_finder
        rows = [
            {"email": "ceo@x.ru", "name": "Иванов Иван", "role": "генеральный директор",
             "level": "top", "generic": False, "role_box": True},
            {"email": "info@x.ru", "name": "", "role": "", "level": "staff",
             "generic": True, "role_box": True},
            {"email": "p.petrov@x.ru", "name": "Петров Пётр", "role": "руководитель",
             "level": "mid", "generic": False, "role_box": False},
        ]
        top = staff_finder.top_candidates({"x.ru": {"rows": rows}})
        assert [t["email"] for t in top] == ["ceo@x.ru"] and top[0]["domain"] == "x.ru"

    def test_contacted_domains_read_from_registry(self):
        import staff_finder
        assert "kzpu.pro" in staff_finder.contacted_domains()


class TestRoleLevels:
    """25.09: пакетный прогон отнёс HR-директора и директора по развитию к первым лицам."""

    @pytest.mark.parametrize("role,level", [
        ("Генеральный директор", "top"), ("Директор", "top"),
        ("Председатель совета директоров", "top"), ("Учредитель", "top"),
        ("HR-директор", "mid"), ("Директор по развитию", "mid"),
        ("Заместитель директора", "mid"), ("Финансовый директор", "mid"),
        ("Технический директор", "mid"), ("Менеджер", "staff"),
    ])
    def test_level(self, role, level):
        import staff_finder
        assert staff_finder.classify_role(role)[0] == level


class TestRoleNearestToEmail:
    def test_deputy_of_general_is_not_first_person(self):
        import staff_finder
        assert staff_finder.classify_role("Первый заместитель генерального директора")[0] == "mid"
        assert staff_finder.classify_role("Советник генерального директора")[0] == "mid"

    def test_neighbour_ceo_without_email_does_not_leak(self):
        """trinixgroup.com 25.09: у гендиректора нет адреса, следующая карточка — финдиректор."""
        import staff_finder
        text = ("Генеральный директор Гачегов Александр Владимирович Написать сообщение "
                "Финансовый директор Темирова Мария Борисовна E-mail m.temirova@trinixgroup.ru")
        r = staff_finder.extract(text, "trinixgroup.com")[0]
        assert r["email"] == "m.temirova@trinixgroup.ru" and r["level"] == "mid"


class TestWaFinder:
    """wa_finder.py: номер WhatsApp только из контактов самой фирмы."""

    def test_2gis_foreign_ads_are_cut(self):
        """Dekmy 25.09: wa.me после «Похожие организации» — чужие фирмы."""
        import wa_finder
        page = ('<h1>Dekmy</h1> ТОО Millina Food <a href="tel:+77292544240">+7 (7292) 544-240</a>'
                ' Похожие организации Реклама <a href="https://wa.me/77471590110">WhatsApp</a>')
        found, cut = wa_finder.find(page, "https://2gis.kz/aktau/firm/1")
        assert cut and found == []

    def test_link_and_label_forms(self):
        import wa_finder
        page = ('<a href="https://api.whatsapp.com/send/?phone=77018013950&text">x</a>'
                ' Телефоны: +7 (727) 357 30 90, + 7 771 780 06 16 (WhatsApp)')
        nums = [f["number"] for f in wa_finder.find(page)[0]]
        assert nums == ["77018013950", "77717800616"]  # городской без пометки не берём

    def test_eight_prefix(self):
        import wa_finder
        assert wa_finder.digits("8 (700) 300-00-67") == "77003000067"


class TestFunnel:
    """funnel.py: одна компания из трёх источников — одна строка воронки."""

    def _setup(self, tmp_path):
        leads = tmp_path / "leads"
        leads.mkdir()
        (leads / "cheber-group-kg.yaml").write_text(
            'company: ОсОО «АйнекСервис» (бренд «Cheber Group»)\ncountry: KG\nsent_on: "2026-09-24"\n'
            'contact:\n  channel: phone\nnotes: >\n  25.09 ОТВЕТ: передадим руководству\n',
            encoding="utf-8")
        (leads / "safement-kz.yaml").write_text(
            'company: ТОО «SAFEMENT»\ncountry: KZ\ndomain: safement.kz\nsent_on: "2026-09-25"\n'
            'contact:\n  channel: whatsapp\n', encoding="utf-8")
        reg = tmp_path / "contacted.csv"
        reg.write_text("company,domain,date,channel,batch\n"
                       "Cheber Group (АйнекСервис),,2026-09-24,whatsapp,2\n"
                       "SAFEMENT,safement.kz,2026-09-25,whatsapp,3\n"
                       "Арианстар,arianstar.ru,2026-09-22,email,1\n", encoding="utf-8")
        ob = tmp_path / "outbox"
        ob.mkdir()
        (ob / "safement-kz.json").write_text(json.dumps(
            {"company": "SAFEMENT", "country": "KZ", "status": "sent", "history": []}),
            encoding="utf-8")
        (ob / "nurtau-kz.json").write_text(json.dumps(
            {"company": "NURTAU", "country": "KZ", "status": "sent", "history": []}),
            encoding="utf-8")
        return leads, reg, ob

    def test_merge_stages_and_issues(self, tmp_path):
        import funnel
        leads, reg, ob = self._setup(tmp_path)
        recs, issues = funnel.build(leads, reg, ob, dt.date(2026, 9, 25))
        by = {r["company"]: r for r in recs}
        assert len(recs) == 4  # Cheber не задвоился, Арианстар только в реестре
        assert by["ОсОО «АйнекСервис» (бренд «Cheber Group»)"]["stage"] == "ответили"
        assert by["Арианстар"]["stage"] == "молчат"  # 22.09 → 25.09: 3 рабочих дня
        assert by["ТОО «SAFEMENT»"]["stage"] == "ждём"
        assert any("NURTAU" in i and "нет карточки" in i for i in issues)
        assert any("NURTAU" in i and "contacted.csv" in i for i in issues)

    def test_summary_counts_replies(self, tmp_path):
        import funnel
        leads, reg, ob = self._setup(tmp_path)
        recs, _ = funnel.build(leads, reg, ob, dt.date(2026, 9, 25))
        s = funnel.summary(recs)["канал"]
        assert s["whatsapp"] == {"отправлено": 3, "ответили": 1}
