#!/usr/bin/env python3
"""
Шлюз на ВХОДЕ: лид не попадает в рассылку без полей, отсутствие которых
уже приводило к провалу.

Симметричен outbound_guard.py. Тот проверяет текст перед отправкой, этот —
карточку лида перед тем, как для неё вообще начнут писать текст. Каждая
проверка выведена из конкретного провала 22.09, а не из общих соображений.

Проверок восемь:
  L1  вакансия активна и проверена недавно   (оба «лучших кандидата» оказались архивными)
  L2  численность и выручка со ссылкой        (выручка обязательна только для РФ)
  L3  ЛПР назван по имени и это первое лицо   (писали РОПам и в никуда)
  L4  контакт личный, а не общий ящик         (13 писем из 20 ушли на info@)
  L5  цитата сигнала дословная, со ссылкой    (цитаты пересказывались)
  L6  ICP по численности и выручке
  L7  назван конкретный процесс под автоматизацию
  L10 факты агента перепроверены основной сессией (verified_on) — 24.09 из 6
      карточек агентов сначала проверили только 2
  L9  компании ещё не писали — сверка с реестром camirix/contacted.csv
      (23.09 агент принёс ЗЕНИТ-НОВА как «новый» лид, хотя ей писали в пачке №1)
  L8  явно подтверждено, что вакансия и реестровая карточка — одна компания
      (23.09: дважды чуть не взял тёзку — книгоиздательскую «Группу Традиция»
      вместо промышленного холдинга, и «Food City» вместо «Food City Group».
      Совпадение названия само по себе ничего не доказывает.)

FAIL CLOSED: поля нет — это FAIL. Пустая строка не считается заполненным полем.

    python3 lead_gate.py lead.yaml
    python3 lead_gate.py leads/*.yaml --brief
    python3 lead_gate.py leads/*.yaml --recheck   # чью вакансию пора перепроверить
"""
import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("нужен pyyaml: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

GENERIC = ("info", "mail", "office", "sales", "zakaz", "hello", "ask", "pr",
           "support", "inbox", "contact", "shop", "moscow", "spb", "kazan", "almaty")

ICP_HEAD = (20, 150)  # нижняя граница снижена с 30 до 20 — 23.09, СНГ, решение пользователя
ICP_REV_MLN = (100, 3000)
VACANCY_MAX_AGE_DAYS = 7
RU_DEFAULT_COUNTRY = "RU"
RECHECK_AFTER_DAYS = 5  # перепроверять за 2 дня до того, как L1 начнёт отсекать
CONTACTED = Path(__file__).resolve().parent / "contacted.csv"
ORG_FORMS = {"ооо", "тоо", "оао", "зао", "ао", "пао", "осоо", "сп", "ип", "чп", "уп",
             "llc", "ltd", "too", "jsc", "company"}

DECIDER_OK = ("собственник", "учредител", "генеральн", "гендиректор", "director",
              "основател", "владелец", "founder", "ceo")
DECIDER_BAD = ("роп", "руководитель отдела продаж", "коммерческий директор",
               "менеджер", "hr", "рекрутер")


class _NoDupLoader(yaml.SafeLoader):
    """Повторный ключ — ошибка. 24.09 пустой verified_on из шаблона ниже по файлу
    молча перетёр заполненный выше: PyYAML берёт последнее значение без предупреждения."""


def _no_dup_mapping(loader, node, deep=False):
    seen = set()
    for k_node, _ in node.value:
        k = loader.construct_object(k_node, deep=deep)
        if k in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"ключ «{k}» повторяется — второе значение перетрёт первое",
                k_node.start_mark)
        seen.add(k)
    return loader.construct_mapping(node, deep=deep)


_NoDupLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_mapping)


def load_lead(text):
    return yaml.load(text, Loader=_NoDupLoader) or {}


def has(v):
    return v is not None and str(v).strip() not in ("", "None", "null", "~", "не найдено")


def norm_domain(v):
    """Домены из поля карточки: «a.group / a.kz» → {"a.group", "a.kz"}."""
    out = set()
    for raw in re.split(r"[\s/,;]+", str(v or "").lower()):
        d = re.sub(r"^(https?:)?/*(www\.)?", "", raw).strip(".")
        if "." in d and not d.startswith("#"):
            out.add(d)
    return out


def norm_company(v):
    """«ТОО «Завод ПМК» (бренд …)» → «завод пмк»: без кавычек, орг. формы и скобок."""
    s = str(v or "").lower().split("(")[0]
    s = re.sub(r"[«»\"'`.,]", " ", s)
    words = [w for w in re.split(r"[^\wё-]+", s) if w and w not in ORG_FORMS]
    return " ".join(words)


def load_contacted(path=CONTACTED):
    """Реестр законтаченных: [(norm_company, {domains}, date, channel)]. Нет файла — пустой."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append((norm_company(r.get("company")), norm_domain(r.get("domain")),
                             r.get("date", ""), r.get("channel", "")))
    except FileNotFoundError:
        pass
    return rows


def check(lead, contacted=None):
    p = []

    # L10 — карточку перепроверил не тот, кто её собрал: вакансия, источник
    # контакта и identity_check открыты заново основной сессией.
    if not has(lead.get("verified_on")):
        p.append("L10 нет verified_on: перед отправкой открыть заново вакансию, страницу "
                 "контакта и источник identity_check, записать дату и что проверено")

    # L9 — этой компании ещё не писали. Карточка с sent_on — это и есть запись
    # о касании, её не сверяем с самой собой.
    if not has(lead.get("sent_on")):
        reg = load_contacted() if contacted is None else contacted
        doms = norm_domain(lead.get("domain"))
        name = norm_company(lead.get("company"))
        for r_name, r_doms, r_date, r_ch in reg:
            if (doms & r_doms) or (name and name == r_name):
                p.append(f"L9 этой компании уже писали ({r_date}, {r_ch}), "
                         "см. camirix/contacted.csv. Повторное первое касание читается как спам")
                break

    # L1 — вакансия активна и проверена недавно
    v = lead.get("vacancy") or {}
    if not has(v.get("url")):
        p.append("L1 нет ссылки на вакансию")
    elif str(v.get("status", "")).lower() != "active":
        p.append(f"L1 статус вакансии «{v.get('status')}», требуется active — "
                 "архив на hh.ru это истёкшие 30 дней размещения, а не закрытая позиция")
    else:
        d = v.get("checked_on")
        if not has(d):
            p.append("L1 нет даты проверки вакансии")
        else:
            try:
                age = (dt.date.today() - dt.date.fromisoformat(str(d))).days
                if age > VACANCY_MAX_AGE_DAYS:
                    p.append(f"L1 вакансия проверялась {age} дн. назад, "
                             f"лимит {VACANCY_MAX_AGE_DAYS}")
                elif age < 0:
                    p.append(f"L1 дата проверки в будущем: {d}")
            except ValueError:
                p.append(f"L1 дата проверки не разобрана: {d}")

    # L2 — численность и выручка со ссылкой. Выручка обязательна только для России:
    # audit-it.ru отдаёт её бесплатно. В остальном СНГ агрегаторы (statsnet.co и т.п.)
    # прячут финансы за платным отчётом — 23.09, решение пользователя: там численности
    # и отраслевого источника достаточно, без выручки.
    f = lead.get("firmographics") or {}
    country = str(lead.get("country") or RU_DEFAULT_COUNTRY).upper()
    required = ("headcount", "численность")
    if not has(f.get(required[0])):
        p.append(f"L2 не указана {required[1]}")
    if country == RU_DEFAULT_COUNTRY and not has(f.get("revenue_mln_rub")):
        p.append("L2 не указана выручка")
    if not has(f.get("source_url")):
        p.append("L2 нет ссылки на источник численности (и выручки для РФ) — на глаз не считается")

    # L3 — ЛПР назван и это первое лицо
    dm = lead.get("decision_maker") or {}
    name = str(dm.get("name", ""))
    role = str(dm.get("role", "")).lower()
    if not has(name) or len(name.split()) < 2:
        p.append("L3 ЛПР не назван по имени и фамилии")
    # Бэрe «директор» — стандартный титул первого лица в ТОО (Казахстан и часть СНГ),
    # аналог «генеральный директор» в РФ. Считается OK только как отдельное слово,
    # а не как часть составного «коммерческий/финансовый директор» — те остаются в BAD.
    is_ok_role = any(g in role for g in DECIDER_OK) or role.strip() == "директор"
    if not has(role):
        p.append("L3 не указана должность ЛПР")
    elif any(b in role for b in DECIDER_BAD) and not is_ok_role:
        p.append(f"L3 должность «{dm.get('role')}» — решение «расти без расширения "
                 "штата» принимает тот, у кого ФОТ в отчётности, а не продажи")
    elif not is_ok_role:
        p.append(f"L3 должность «{dm.get('role')}» не распознана как первое лицо")

    # L4 — контакт личный
    c = lead.get("contact") or {}
    val = str(c.get("value", "")).strip().lower()
    if not has(val):
        p.append("L4 контакт не найден")
    elif "@" in val:
        local = val.split("@")[0]
        if local in GENERIC or any(local.startswith(g + ".") for g in GENERIC):
            p.append(f"L4 общий ящик «{val}» — в пачке №1 такие дали нулевой результат. "
                     "Имя гендиректора есть в ЕГРЮЛ по ИНН, дальше camirix/contact_finder.py")

    # L5 — дословная цитата со ссылкой
    s = lead.get("signal") or {}
    q = str(s.get("quote", ""))
    if not has(q):
        p.append("L5 нет дословной цитаты сигнала")
    elif len(q.split()) < 4:
        p.append("L5 цитата слишком короткая, похожа на пересказ")
    if not has(s.get("source_url")):
        p.append("L5 нет ссылки на страницу, откуда взята цитата")

    # L6 — ICP
    try:
        h = int(f.get("headcount"))
        if not ICP_HEAD[0] <= h <= ICP_HEAD[1]:
            p.append(f"L6 численность {h} вне ICP {ICP_HEAD[0]}-{ICP_HEAD[1]}")
    except (TypeError, ValueError):
        pass
    try:
        r = float(f.get("revenue_mln_rub"))
        if not ICP_REV_MLN[0] <= r <= ICP_REV_MLN[1]:
            p.append(f"L6 выручка {r} млн вне ICP {ICP_REV_MLN[0]}-{ICP_REV_MLN[1]} млн")
    except (TypeError, ValueError):
        pass

    # L7 — назван процесс
    proc = lead.get("process_to_automate")
    if not has(proc):
        p.append("L7 не назван повторяемый процесс, который можно снять с людей")
    elif len(str(proc).split()) < 4:
        p.append("L7 процесс описан слишком обще, чтобы о нём написать конкретно")

    # L8 — компания из вакансии и компания из реестра явно сверены, а не
    # предполагаются совпадающими по названию. Тёзки — не редкость (см. docstring).
    ident = lead.get("identity_check")
    if not has(ident):
        p.append("L8 не подтверждено, что вакансия и реестровая карточка — одна компания "
                 "(нет identity_check): совпадение названия не доказывает совпадение фирмы")
    elif len(str(ident).split()) < 4:
        p.append("L8 identity_check слишком короткий, похож на заглушку, а не на реальную сверку")

    return p


def main():
    ap = argparse.ArgumentParser(description="Проверка карточки лида перед рассылкой")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--brief", action="store_true", help="только вердикт по каждому файлу")
    ap.add_argument("--recheck", action="store_true",
                    help=f"список неотправленных лидов, чью вакансию пора перепроверить "
                         f"(проверка старше {RECHECK_AFTER_DAYS} дн.)")
    a = ap.parse_args()
    # _TEMPLATE.yaml и прочие файлы с «_» — заготовки, не лиды: при глобе
    # leads/*.yaml они шли в счёт «не готовы» и прятали настоящие провалы.
    a.files = [f for f in a.files if not Path(f).name.startswith("_")]

    if a.recheck:
        due = 0
        for fp in a.files:
            try:
                lead = load_lead(Path(fp).read_text(encoding="utf-8"))
            except Exception:
                continue
            if has(lead.get("sent_on")) or not lead.get("company"):
                continue
            v = lead.get("vacancy") or {}
            try:
                age = (dt.date.today() - dt.date.fromisoformat(str(v.get("checked_on")))).days
            except ValueError:
                age = None
            if age is None or age >= RECHECK_AFTER_DAYS:
                due += 1
                when = "никогда" if age is None else f"{age} дн. назад"
                print(f"↻ {lead.get('company')} — вакансия проверена {when}: "
                      f"{v.get('url', 'нет ссылки')}")
        print(f"\nПерепроверить: {due}")
        return 0

    bad = 0
    for fp in a.files:
        path = Path(fp)
        try:
            lead = load_lead(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"✗ {path.name}: файл не разобран ({e}) — FAIL CLOSED")
            bad += 1
            continue
        probs = check(lead)
        title = lead.get("company") or path.stem
        if probs:
            bad += 1
            print(f"✗ {title} — НЕ ГОТОВ, нарушений {len(probs)}")
            if not a.brief:
                for x in probs:
                    print(f"    {x}")
        else:
            print(f"✓ {title} — готов к касанию")
    print(f"\nГотовы: {len(a.files) - bad} из {len(a.files)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
