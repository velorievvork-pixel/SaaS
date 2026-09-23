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
