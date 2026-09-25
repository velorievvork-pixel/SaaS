#!/usr/bin/env python3
"""
Именные email со страниц «Сотрудники» / «Руководство» на сайтах компаний.

Зачем. Г-7: личную почту первого лица бесплатными методами найти не удавалось.
24.09 нашёлся рабочий источник: сайты на шаблоне Аспро/Битрикс публикуют
сотрудников с email на /company/staff/ — так найден адрес гендиректора КЗПУ.
Этот скрипт обходит типовые адреса таких страниц и достаёт пары
«ФИО + должность + email», отбрасывая общие ящики и незаполненные демо-страницы.

Два режима:
    python3 staff_finder.py kzpu.pro plitstroytorg.ru      # сам скачивает страницы
    python3 staff_finder.py --leads camirix/leads/*.yaml   # домены из карточек
    python3 staff_finder.py --from-file domains.txt --top  # пакет обратной воронки
    python3 staff_finder.py --text page.md --domain tst-ur.ru
        # разбор текста, полученного через web_fetch, когда сайт закрыт для
        # прямого доступа (403, ошибка сертификата)

Результат — кандидаты, а не проверенные контакты. Адрес, опубликованный рядом
с ФИО и должностью, надёжнее подобранного contact_finder.py, но в карточку
его пишут только после того, как страницу открыли глазами (L10, verified_on).
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PATHS = (
    "/company/staff/", "/company/employees/", "/company/team/", "/company/rukovodstvo/",
    "/about/staff/", "/about/team/", "/about/boss/", "/about/rukovodstvo/",
    "/o-kompanii/sotrudniki/", "/o-kompanii/komanda/", "/team/", "/staff/",
    "/contacts/", "/kontakty/", "/",
)
GENERIC = {
    "info", "mail", "office", "sales", "sale", "zakaz", "order", "orders", "hello", "ask",
    "pr", "support", "inbox", "contact", "contacts", "shop", "buh", "buhgalteria", "hr",
    "job", "jobs", "resume", "rabota", "admin", "marketing", "market", "tender", "snab",
    "opt", "roznica", "service", "servis", "help", "reception", "secretary", "priemnaya",
    "noreply", "no-reply", "post", "manager", "director", "ceo", "general",
}
DEMO_DOMAINS = {"site.ru", "example.com", "example.ru", "mail.ru.demo", "aspro.ru",
                "domain.ru", "yoursite.ru", "company.ru", "test.ru"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILTO = re.compile(r"href\s*=\s*[\"']\s*mailto:\s*([^\"'?]+)", re.IGNORECASE)
# Кириллица + казахские/узбекские буквы: «Даулетқызы Алия» иначе теряется.
_UP, _LO = "А-ЯЁӘҒҚҢӨҰҮҺІЎ", "а-яёәғқңөұүһіў"
_W = f"[{_UP}][{_LO}]+"
FIO = re.compile(rf"(?<![{_LO}{_UP}])({_W}(?:-{_W})?)\s+({_W})(?:\s+({_W}))?(?![{_LO}{_UP}])")
ASPRO = ("Надежные и позитивные помощники", "Наши руководители", "aspro", "Аспро")
NOT_NAMES = {"Сообщение", "Позвонить", "Подробнее", "Генеральный", "Коммерческий",
             "Финансовый", "Руководитель", "Менеджер",
             "Написать", "Телефон", "Отдел", "Главная", "Наши", "Контакты", "Сотрудники",
             "Директор", "Заместитель", "Начальник", "Старший", "Ведущий", "Главный"}


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (staff_finder)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None, f"HTTP {r.status}"
            raw = r.read(2_000_000)
            enc = r.headers.get_content_charset() or "utf-8"
            return raw.decode(enc, errors="replace"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # сеть, TLS, таймаут — сайт уходит в режим --text
        return None, type(e).__name__


def to_text(page):
    """HTML → плоский текст с сохранёнными mailto (их текст часто не совпадает с адресом)."""
    mailtos = [m.strip().lower() for m in MAILTO.findall(page)]
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", page)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", t)
    t = re.sub(r"(?i)<a[^>]+href\s*=\s*[\"']\s*mailto:\s*([^\"'?]+)[^>]*>", r" \1 ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return t, mailtos


def is_generic(local):
    base = re.split(r"[._\-+]", local)[0]
    return (local in GENERIC or base in GENERIC or local.isdigit()
            or bool(re.fullmatch(r"[a-z]*\d+", local)))


TOP_RE = re.compile(r"генеральный\s+директор|собственник|учредитель|основатель|"
                    r"владелец|президент|председатель\s+совета|\bceo\b|founder")
MID_RE = re.compile(r"коммерческий директор|финансовый директор|исполнительный директор|"
                    r"технический директор|директор по \w+|заместител\w*|руководител\w*|"
                    r"начальник\w*|управляющ\w*|советник\w*|помощник\w*|head of")
DEPUTY = re.compile(r"(заместител\w*|советник\w*|помощник\w*)\s+(\S+\s+){0,2}$")


def classify_role(ctx):
    """Уровень должности, ближайшей к адресу (последней в тексте карточки).

    Первое лицо — гендиректор, собственник и т. п. или «директор» без уточнения.
    HR-директор, директор по развитию, заместитель и советник гендиректора — нет.
    Берём последнее упоминание: левее может стоять должность соседа по странице."""
    low = ctx.lower()
    found, spans = [], []
    for m in TOP_RE.finditer(low):
        spans.append(m.span())
        if not DEPUTY.search(low[:m.start()]):
            found.append((m.start(), "top", m.group(0)))
    for m in MID_RE.finditer(low):
        spans.append(m.span())
        found.append((m.start(), "mid", m.group(0)))
    for m in re.finditer(r"(?<![\w-])директор(?![\w-])", low):
        if any(a <= m.start() < b for a, b in spans):
            continue  # часть уже найденной должности («генеральный директор»)
        prev = (low[:m.start()].split() or [""])[-1]
        qualified = (re.search(r"(ый|ий|ой|ая|-)$", prev) or prev.startswith("заместител")
                     or low[m.end():].lstrip().startswith("по "))
        found.append((m.start(), "mid" if qualified else "top",
                      "директор (с уточнением)" if qualified else "директор"))
    for m in re.finditer(r"[\w]+-директор", low):
        found.append((m.start(), "mid", m.group(0)))
    if not found:
        return "staff", ""
    _, level, role = max(found, key=lambda f: (f[0], f[1] == "top"))
    return level, role


def nearest_name(before):
    """Последнее ФИО перед адресом и позиция, с которой оно начинается."""
    for m in reversed(list(FIO.finditer(before))):
        parts = [g for g in m.groups() if g and g not in NOT_NAMES]
        if len(parts) >= 2 and m.group(1) not in NOT_NAMES:
            return " ".join(parts), m.start()
    return "", -1


def extract(text, domain, mailtos=()):
    """Кандидаты из текста страницы: [{email, name, role, level, generic}]."""
    domain = domain.lower().removeprefix("www.")
    found, seen = [], set()
    emails = [(m.group(0).lower(), m.start(), m.end()) for m in EMAIL.finditer(text)]
    emails += [(e, -1, -1) for e in mailtos if e not in {x for x, _, _ in emails}]
    prev_end = 0
    for raw_email, pos, end in emails:
        email = raw_email.strip(".")
        seg_start = prev_end
        if end > 0:
            prev_end = end  # граница следующей карточки сдвигается и на пропущенных адресах
        if email in seen:
            continue
        seen.add(email)
        local, _, dom = email.partition("@")
        if dom in DEMO_DOMAINS:
            continue  # незаполненный демо-шаблон
        # Почтовый домен компании может отличаться от сайта (kzpu.pro → mptech.pro),
        # поэтому чужой домен не отбрасываем, а помечаем для ручной проверки.
        foreign = bool(domain) and dom != domain and not dom.endswith("." + domain)
        # Карточка сотрудника — отрезок от предыдущего адреса до этого: левее уже
        # чужая карточка. Порядок ФИО и должности внутри бывает любым.
        before = text[max(seg_start, pos - 300):pos] if pos >= 0 else ""
        # Адреса внутри отрезка (дубли ссылки) убираем: «ceo@» соседа — не должность.
        before = EMAIL.sub(" ", before)
        name, _ = nearest_name(before) if before else ("", -1)
        level, role = classify_role(before)
        found.append({
            "email": email,
            "name": name,
            "role": role,
            "level": level,
            # Ящик должности (general@, info@), подписанный на странице именем, —
            # канал этого человека, а не общий ящик.
            "generic": is_generic(local) and not name,
            "role_box": is_generic(local),
            "foreign_domain": foreign,
        })
    order = {"top": 0, "mid": 1, "staff": 2}
    found.sort(key=lambda r: (r["generic"], order[r["level"]], not r["name"]))
    return found


def scan(domain):
    domain = re.sub(r"^(https?:)?/*(www\.)?", "", domain.strip().lower()).strip("/")
    results, errors, aspro = [], [], False
    base = None
    for scheme in ("https://", "http://"):  # сначала выясняем, открывается ли сайт вообще
        page, err = fetch(scheme + domain + "/", timeout=10)
        if page is not None:
            base = scheme + domain
            break
        errors.append(f"{scheme}{domain}/: {err}")
    if base is None:
        return [], errors, False
    for path in PATHS:
        page, err = fetch(base + path) if path != "/" else (page, None)
        if page is None:
            errors.append(f"{base}{path}: {err}")
            continue
        if any(a.lower() in page.lower() for a in ASPRO):
            aspro = True
        text, mailtos = to_text(page)
        for r in extract(text, domain, mailtos):
            r["url"] = base + path
            results.append(r)
    uniq = {}
    for r in results:
        uniq.setdefault(r["email"], r)
    return list(uniq.values()), errors, aspro


def leads_domains(files):
    try:
        import yaml
    except ImportError:
        sys.exit("нужен pyyaml")
    out = []
    for f in files:
        d = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
        for part in re.split(r"[\s/,;]+", str(d.get("domain") or "")):
            if "." in part:
                out.append(part)
    return out


def read_domains(path):
    """Файл доменов: по одному на строку, «#» — комментарий, URL сводятся к домену."""
    out = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(re.sub(r"^(https?:)?/*(www\.)?", "", line.lower()).split("/")[0])
    return out


def contacted_domains():
    """Домены из contacted.csv: им уже писали, обходить незачем."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lead_gate import load_contacted
    except ImportError:
        return set()
    return {d for _, doms, _, _ in load_contacted() for d in doms}


def top_candidates(report):
    """Именные адреса первых лиц: то, ради чего обратная воронка и запускается."""
    out = []
    for d, rep in report.items():
        for r in rep["rows"]:
            if r["level"] == "top" and r["name"] and not r["generic"]:
                out.append({"domain": d, **r})
    return out


def show(domain, rows, errors=(), aspro=False):
    named = [r for r in rows if not r["generic"]]
    print(f"\n=== {domain} — именных: {len(named)}, всего адресов: {len(rows)}"
          + ("  [шаблон Аспро]" if aspro else ""))
    for r in rows:
        tag = {"top": "ЛПР?", "mid": "рук.", "staff": "    "}[r["level"]]
        flag = " общий" if r["generic"] else (" ящик должности" if r.get("role_box") else "")
        flag += " чужой домен" if r["foreign_domain"] else ""
        print(f"  {tag} {r['email']:<34} {r['name'] or '—':<32} {r['role']}{flag}"
              + (f"\n        {r['url']}" if r.get("url") else ""))
    if not rows and errors:
        print(f"  страницы не открылись ({len(errors)} попыток), последняя: {errors[-1]}")
        print("  → открыть через web_fetch и прогнать:",
              "staff_finder.py --text <файл> --domain", domain)


def main():
    ap = argparse.ArgumentParser(description="Именные email со страниц сотрудников")
    ap.add_argument("domains", nargs="*")
    ap.add_argument("--leads", nargs="+", help="взять домены из карточек лидов")
    ap.add_argument("--text", help="файл с текстом/HTML страницы (из web_fetch)")
    ap.add_argument("--domain", help="домен для режима --text")
    ap.add_argument("--from-file", help="файл со списком доменов (пакетный режим)")
    ap.add_argument("--top", action="store_true",
                    help="в конце — только именные адреса первых лиц (кандидаты в лиды)")
    ap.add_argument("--include-contacted", action="store_true",
                    help="не пропускать домены из contacted.csv")
    ap.add_argument("--workers", type=int, default=8, help="параллельных сайтов")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.text:
        raw = Path(a.text).read_text(encoding="utf-8")
        text, mailtos = to_text(raw) if "<" in raw and ">" in raw else (raw, [])
        rows = extract(text, a.domain or "", mailtos)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            show(a.domain or a.text, rows)
        return 0

    domains = list(a.domains) + (leads_domains(a.leads) if a.leads else [])
    if a.from_file:
        domains += read_domains(a.from_file)
    if not domains:
        ap.error("нужны домены, --leads, --from-file или --text")
    domains = list(dict.fromkeys(domains))
    if not a.include_contacted and not a.leads:
        done = contacted_domains()
        skipped = [d for d in domains if d in done]
        domains = [d for d in domains if d not in done]
        if skipped and not a.json:
            print(f"пропущено (уже писали): {', '.join(skipped)}")
    report = {}
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for d, (rows, errors, aspro) in zip(domains, pool.map(scan, domains), strict=True):
            report[d] = {"rows": rows, "aspro": aspro, "errors": errors[-3:]}
    if a.json:
        out = {"report": report, "top": top_candidates(report)} if a.top else report
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if not a.top:
        for d, rep in report.items():
            show(d, rep["rows"], rep["errors"], rep["aspro"])
    top = top_candidates(report)
    if a.top or len(domains) > 1:
        dead = sum(1 for rep in report.values() if not rep["rows"] and rep["errors"])
        print(f"\n=== Итог: сайтов {len(domains)}, не открылись {dead}, "
              f"именных адресов первых лиц: {len(top)}")
        for r in top:
            flag = " (ящик должности)" if r["role_box"] else ""
            print(f"  {r['domain']:<24} {r['email']:<32} {r['name']} — {r['role']}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
