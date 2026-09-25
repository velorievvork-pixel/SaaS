#!/usr/bin/env python3
"""
Самостоятельность агента и обучение на решениях владельца.

Каждое действие, которое агент показывал владельцу, записывается с исходом:
  approved — отправлено как есть;
  edited   — владелец поправил текст (правка сохраняется как пример);
  rejected — «не отправлять».
Действие в режиме learn становится самостоятельным после `graduate_after` одобрений подряд.
Правка или отказ сбрасывают счёт. Для правок и отказов владелец (или агент со слов владельца)
добавляет урок в lessons.md: `learn` — это и есть обучение.

    python3 camirix/agent/autonomy.py decide reply_refusal
    python3 camirix/agent/autonomy.py record reply_refusal approved --lead cbc-astana-kz
    python3 camirix/agent/autonomy.py record first_message edited --before a.txt --after b.txt
    python3 camirix/agent/autonomy.py learn "Не начинать каждое сообщение одинаково" --example "…"
    python3 camirix/agent/autonomy.py status
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
POLICY = HERE / "policy.yaml"
LOG = HERE / "approvals.jsonl"
LESSONS = HERE / "lessons.md"
OUTCOMES = ("approved", "edited", "rejected")


def load_policy(path=POLICY):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_log(path=LOG):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def streak(action, log):
    """Одобрений подряд с конца истории этого действия."""
    n = 0
    for rec in reversed([r for r in log if r["action"] == action]):
        if rec["outcome"] != "approved":
            break
        n += 1
    return n


def decide(action, policy=None, log=None):
    """→ {"mode": "auto"|"ask", "why": ...}. Неизвестное действие — всегда ask."""
    policy = policy if policy is not None else load_policy()
    log = log if log is not None else load_log()
    mode = (policy.get("actions") or {}).get(action)
    need = int(policy.get("graduate_after", 5))
    if mode is None:
        return {"mode": "ask", "why": "действия нет в policy.yaml"}
    if action in (policy.get("never_auto") or []):
        return {"mode": "ask", "why": "в never_auto: всегда с владельцем"}
    if mode == "auto":
        last = [r for r in log if r["action"] == action][-1:]
        if last and last[0]["outcome"] != "approved":
            return {
                "mode": "ask",
                "why": "последний раз владелец поправил или отклонил, снова учимся",
            }
        return {"mode": "auto", "why": "auto в policy.yaml"}
    if mode == "learn":
        s = streak(action, log)
        if s >= need:
            return {"mode": "auto", "why": f"{s} одобрений подряд без правок (порог {need})"}
        return {"mode": "ask", "why": f"учимся: {s} из {need} одобрений подряд"}
    return {"mode": "ask", "why": "ask в policy.yaml"}


def record(action, outcome, lead="", before="", after="", note="", path=LOG, now=None):
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome: {', '.join(OUTCOMES)}")
    rec = {
        "at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
        "action": action,
        "outcome": outcome,
        "lead": lead,
    }
    if outcome == "edited":
        rec.update(before=before, after=after)
    if note:
        rec["note"] = note
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def learn(rule, example="", source="владелец", path=LESSONS, today=None):
    """Урок — правило одной строкой + пример. Агент читает lessons.md перед каждым текстом."""
    day = (today or dt.date.today()).isoformat()
    block = f"\n- **{day}** ({source}): {rule.strip()}"
    if example.strip():
        block += f"\n  Пример: {example.strip()}"
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(block + "\n")
    return block


def status(policy=None, log=None):
    policy = policy if policy is not None else load_policy()
    log = log if log is not None else load_log()
    rows = []
    for action, mode in (policy.get("actions") or {}).items():
        d = decide(action, policy, log)
        n = sum(1 for r in log if r["action"] == action)
        rows.append(
            {"action": action, "policy": mode, "now": d["mode"], "why": d["why"], "decisions": n}
        )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Самостоятельность и обучение агента")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decide")
    p.add_argument("action")
    p = sub.add_parser("record")
    p.add_argument("action")
    p.add_argument("outcome", choices=OUTCOMES)
    p.add_argument("--lead", default="")
    p.add_argument("--before", help="файл с текстом до правки")
    p.add_argument("--after", help="файл с текстом после правки")
    p.add_argument("--note", default="")
    p = sub.add_parser("learn")
    p.add_argument("rule")
    p.add_argument("--example", default="")
    p.add_argument("--source", default="владелец")
    sub.add_parser("status")
    a = ap.parse_args(argv)

    if a.cmd == "decide":
        out = decide(a.action)
    elif a.cmd == "record":
        read = lambda f: Path(f).read_text(encoding="utf-8").strip() if f else ""  # noqa: E731
        out = record(a.action, a.outcome, a.lead, read(a.before), read(a.after), a.note)
    elif a.cmd == "learn":
        out = {"added": learn(a.rule, a.example, a.source)}
    else:
        out = status()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
