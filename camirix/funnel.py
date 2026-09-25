#!/usr/bin/env python3
"""
Единая воронка Camirix: кому писали, кто ответил, где застряли.

Данные разбросаны по трём местам: карточки leads/*.yaml, реестр contacted.csv
и страница «Отправить сегодня» (ArtifactData, collection outbox). Скрипт сводит
их в одну таблицу по компаниям, считает конверсию по каналу, стране и способу
поиска и показывает расхождения между источниками.

    python3 funnel.py                              # без страницы WhatsApp
    python3 funnel.py --outbox /tmp/wa/outbox       # + выгрузка ArtifactData (out_dir)
    python3 funnel.py --outbox /tmp/wa/outbox --md funnel.md

Этапы: не отправлено → ждём → молчат (3 рабочих дня) → ответили → созвон → сделка;
отдельно — отказ и нет WhatsApp. Созвон и сделку ставит поле `stage:` в карточке
(call / deal / lost): по страницам и почте их не видно.
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lead_gate import load_lead, norm_domain  # noqa: E402
from wa_inbox import load_outbox, workdays_between  # noqa: E402

STAGES = ["не отправлено", "ждём", "молчат", "ответили", "созвон", "сделка",
          "отказ", "нет WhatsApp"]
PAGE_STATUS = {"new": "не отправлено", "sent": "ждём", "silent": "молчат",
               "replied": "ответили", "refused": "отказ", "no_whatsapp": "нет WhatsApp"}
CARD_STAGE = {"call": "созвон", "deal": "сделка", "lost": "отказ"}
REPLIED = {"ответили", "созвон", "сделка", "отказ"}  # «отказ» — тоже живой ответ
TLD_COUNTRY = {"kz": "KZ", "by": "BY", "ru": "RU", "uz": "UZ", "kg": "KG", "рф": "RU"}


def country_by_domain(domains):
    for d in sorted(domains):
        tld = d.rsplit(".", 1)[-1]
        if tld in TLD_COUNTRY:
            return TLD_COUNTRY[tld]
    return "?"


GENERIC_WORDS = {"компания", "company", "group", "групп", "группа", "бренд", "завод", "ооо",
                 "тоо", "осоо", "ltd", "production", "distribution"}


def name_words(company):
    """Значимые слова названия (5+ букв, без ОПФ и общих слов) для сопоставления."""
    words = re.findall(r"[a-zа-яё0-9]{5,}", str(company or "").lower())
    return {w for w in words if w not in GENERIC_WORDS}


def rank(stage):
    order = ["нет WhatsApp", "не отправлено", "ждём", "молчат", "ответили", "отказ",
             "созвон", "сделка"]
    return order.index(stage) if stage in order else -1


def build(leads_dir, contacted_path, outbox=None, today=None):
    today = today or dt.date.today()
    recs, issues = {}, []

    def rec(key, **kw):
        r = recs.setdefault(key, {"key": key, "company": "", "country": "?", "channels": set(),
                                  "source": "прямая", "sent_on": "", "stage": "не отправлено",
                                  "domains": set(), "where": set(), "followup": False})
        for k, v in kw.items():
            if k in ("channels", "domains", "where"):
                r[k] |= set(v)
            elif v and (k != "stage" or rank(v) > rank(r["stage"])):
                r[k] = v
        return r

    # 1. Карточки
    for f in sorted(Path(leads_dir).glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            d = load_lead(f.read_text(encoding="utf-8"))
        except Exception as e:
            issues.append(f"{f.name}: карточка не читается ({e})")
            continue
        ch = str((d.get("contact") or {}).get("channel") or "")
        channel = "whatsapp" if ch in ("whatsapp", "phone", "telegram") else ch or "?"
        notes = str(d.get("notes") or "")
        stage = "ждём" if d.get("sent_on") else "не отправлено"
        if "ОТВЕТ" in notes:
            stage = "ответили"
        if str((d.get("vacancy") or {}).get("status")) == "archived" and not d.get("sent_on"):
            continue  # вакансия ушла в архив до касания — не лид
        stage = CARD_STAGE.get(str(d.get("stage") or ""), stage)
        rec(f.stem, company=d.get("company") or f.stem, country=d.get("country") or "?",
            channels=[channel], sent_on=str(d.get("sent_on") or ""), stage=stage,
            domains=norm_domain(d.get("domain")), where=["карточка"])

    def match(company, doms=()):
        """Та же компания под другим именем: по домену, иначе по общему слову названия
        («Cheber Group (АйнекСервис)» = «ОсОО «АйнекСервис» (бренд «Cheber Group»)»)."""
        for k, r in recs.items():
            if r["domains"] & set(doms):
                return k
        words = name_words(company)
        for k, r in recs.items():
            if words & name_words(r["company"]):
                return k
        return None

    # 2. Реестр отправок: сопоставляем с карточками по домену или названию
    with open(contacted_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            doms = norm_domain(row.get("domain"))
            key = match(row.get("company"), doms)
            key = key or "csv:" + (min(doms) if doms else row.get("company", ""))
            chans = [c for c in re.split(r"[+/ ]", row.get("channel", "")) if c]
            rec(key, company=recs.get(key, {}).get("company") or row.get("company"),
                channels=chans, sent_on=row.get("date", ""), domains=doms,
                source="обратная" if "r" in str(row.get("batch", "")) else None,
                stage="ждём", where=["реестр"])
            if recs[key]["country"] == "?":
                recs[key]["country"] = country_by_domain(doms)

    # 3. Страница WhatsApp
    for row in load_outbox(outbox) if outbox else []:
        base = re.sub(r"-\d+$", "", row["id"])
        if base not in recs:
            base = match(row.get("company")) or base
        page_stage = PAGE_STATUS.get(row.get("status") or "new", "не отправлено")
        r = rec(base, company=recs.get(base, {}).get("company") or row.get("company"),
                country=row.get("country"), channels=["whatsapp"], where=["страница"])
        # Статус страницы главнее догадок по реестру, кроме уже известных ответов и созвонов;
        # у второго шага (…-2) «не отправлено» не отменяет ответ на первый.
        own_row = base == row["id"] and rank(r["stage"]) < rank("ответили")
        if own_row or rank(page_stage) > rank(r["stage"]):
            r["stage"] = page_stage
        if re.search(r"-\d+$", row["id"]):
            r["followup"] = True
        for h in row.get("history") or []:
            if str(h.get("text", "")).startswith("Отправил") and not r["sent_on"]:
                r["sent_on"] = str(h.get("at", ""))[:10]

    # 4. Тишина по почте: 3 рабочих дня без ответа
    for r in recs.values():
        if r["stage"] == "ждём" and r["sent_on"]:
            try:
                sent = dt.datetime.fromisoformat(r["sent_on"][:10])
            except ValueError:
                continue
            if workdays_between(sent, dt.datetime.combine(today, dt.time())) >= 3:
                r["stage"] = "молчат"

    # 5. Расхождения
    for r in recs.values():
        sent = r["stage"] not in ("не отправлено", "нет WhatsApp")
        if sent and "реестр" not in r["where"] and r["key"] and not r["key"].startswith("csv:"):
            issues.append(f"{r['company']}: отправлено, но нет в contacted.csv "
                          "(L9 не защитит от повтора)")
        if sent and "карточка" in r["where"] and not r["sent_on"]:
            issues.append(f"{r['company']}: отправлено, но в карточке нет sent_on")
        if "страница" in r["where"] and "карточка" not in r["where"]:
            issues.append(f"{r['company']}: есть на странице WhatsApp, нет карточки "
                          "(не проверен L10)")
    return list(recs.values()), list(dict.fromkeys(issues))


def summary(recs):
    groups = {"канал": lambda r: "+".join(sorted(r["channels"])) or "?",
              "страна": lambda r: r["country"] or "?",
              "способ поиска": lambda r: r["source"]}
    out = {}
    for name, fn in groups.items():
        g = defaultdict(lambda: {"отправлено": 0, "ответили": 0})
        for r in recs:
            if r["stage"] in ("не отправлено", "нет WhatsApp"):
                continue
            k = fn(r)
            g[k]["отправлено"] += 1
            g[k]["ответили"] += r["stage"] in REPLIED
        out[name] = dict(sorted(g.items(), key=lambda kv: -kv[1]["отправлено"]))
    return out


def render_md(recs, issues, today):
    lines = [f"# Воронка Camirix — {today.isoformat()}", ""]
    total = [r for r in recs if r["stage"] not in ("не отправлено", "нет WhatsApp")]
    replied = [r for r in total if r["stage"] in REPLIED]
    lines += [f"Отправлено: **{len(total)}**, ответили: **{len(replied)}**"
              + (f" ({100 * len(replied) // len(total)}%)" if total else ""), ""]
    for name, g in summary(recs).items():
        lines += [f"### По признаку «{name}»", "", "| | отправлено | ответили | % |",
                  "|---|---|---|---|"]
        for k, v in g.items():
            pct = 100 * v["ответили"] // v["отправлено"] if v["отправлено"] else 0
            lines.append(f"| {k} | {v['отправлено']} | {v['ответили']} | {pct}% |")
        lines.append("")
    lines += ["### По компаниям", "", "| Компания | Страна | Канал | Поиск | Дата | Этап |",
              "|---|---|---|---|---|---|"]
    for r in sorted(recs, key=lambda r: (-rank(r["stage"]), r["sent_on"])):
        fu = " (есть 2-й шаг)" if r["followup"] else ""
        lines.append(f"| {r['company']} | {r['country']} | {'+'.join(sorted(r['channels']))} | "
                     f"{r['source']} | {r['sent_on']} | {r['stage']}{fu} |")
    if issues:
        lines += ["", "### Расхождения", ""] + [f"- {i}" for i in issues]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Сводная воронка Camirix")
    ap.add_argument("--leads", default=str(HERE / "leads"))
    ap.add_argument("--contacted", default=str(HERE / "contacted.csv"))
    ap.add_argument("--outbox", help="выгрузка страницы WhatsApp (ArtifactData out_dir)")
    ap.add_argument("--md", help="записать отчёт в markdown-файл")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    recs, issues = build(a.leads, a.contacted, a.outbox, today)
    if a.json:
        print(json.dumps({"summary": summary(recs), "issues": issues,
                          "leads": [{**r, "channels": sorted(r["channels"]),
                                     "domains": sorted(r["domains"]), "where": sorted(r["where"])}
                                    for r in recs]}, ensure_ascii=False, indent=1))
        return 0
    md = render_md(recs, issues, today)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
