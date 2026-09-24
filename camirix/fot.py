#!/usr/bin/env python3
"""
Считает годовой ФОТ и его динамику против выручки по реестровым данным.

Зачем. Проверка гипотезы Г-4 показала, что сигнал надо брать не из вакансий,
а из отчётности: по ИНН бесплатно отдаются ССЧ и средняя зарплата по годам плюс
выручка по годам, и тем же запросом приходит имя собственника. Из этих чисел
прямо считается расход, который продукт обещает сократить.

Цифра принадлежит адресату, а не нам, поэтому её можно называть вслух —
в отличие от цены, которой у нас нет.

    python3 fot.py --headcount 93 --salary 83700 --revenue 1100 --revenue-prev 1264
    python3 fot.py --headcount 170 --salary 120000 --revenue 6400 --revenue-prev 4790 \
                   --headcount-prev 45
"""
import argparse

# Страховые взносы. 24.09 выяснилось, что плоские 30% дают ложное «расхождение >30%»
# у малого бизнеса: у МСП 30% берутся только с части зарплаты до порога, выше — 15%.
# С 2025 порог 1,5 МРОТ, до 2025 — 1 МРОТ. Остальные платят 30% до предельной базы
# и 15,1% сверх неё. Взносы на травматизм (≈0,2%) не учитываются.
MROT = {2023: 16_242, 2024: 19_242, 2025: 22_440, 2026: 27_093}
LIMIT_YEAR = {2023: 1_917_000, 2024: 2_225_000, 2025: 2_759_000, 2026: 2_979_000}
MSP_HEAD, MSP_REV_MLN = 250, 2000  # критерии среднего предприятия


def is_msp(headcount, revenue_mln):
    return headcount <= MSP_HEAD and revenue_mln <= MSP_REV_MLN


def monthly_contrib(salary, year, msp):
    """Взносы работодателя с одной среднемесячной зарплаты, ₽."""
    y = min(max(year, min(MROT)), max(MROT))
    if msp:
        base = MROT[y] * (1.5 if y >= 2025 else 1.0)
        return 0.30 * min(salary, base) + 0.15 * max(salary - base, 0)
    lim = LIMIT_YEAR[y] / 12
    return 0.30 * min(salary, lim) + 0.151 * max(salary - lim, 0)


def annual_fot(headcount, salary, year, msp):
    """Годовой ФОТ с взносами."""
    return headcount * 12 * (salary + monthly_contrib(salary, year, msp))


def fmt_rub(v):
    if v >= 1e9:
        return f"{v / 1e9:.2f} млрд ₽".replace(".", ",")
    if v >= 1e6:
        return f"{v / 1e6:.1f} млн ₽".replace(".", ",")
    return f"{v:,.0f} ₽".replace(",", " ")


def main():
    ap = argparse.ArgumentParser(description="ФОТ против выручки по реестровым данным")
    ap.add_argument("--headcount", type=int, required=True, help="ССЧ за последний год")
    ap.add_argument("--salary", type=float, required=True, help="среднемесячная ЗП, ₽")
    ap.add_argument("--revenue", type=float, required=True, help="выручка, млн ₽")
    ap.add_argument("--headcount-prev", type=int, help="ССЧ за предыдущий год")
    ap.add_argument("--salary-prev", type=float, help="средняя ЗП за предыдущий год, ₽")
    ap.add_argument("--revenue-prev", type=float, help="выручка за предыдущий год, млн ₽")
    ap.add_argument("--contributions", type=float,
                    help="уплаченные страховые взносы, млн ₽ — для независимой сверки")
    ap.add_argument("--year", type=int, default=2025, help="год последних данных (ставки взносов)")
    msp_g = ap.add_mutually_exclusive_group()
    msp_g.add_argument("--msp", dest="msp", action="store_true", default=None,
                       help="считать по пониженным ставкам МСП")
    msp_g.add_argument("--no-msp", dest="msp", action="store_false")
    a = ap.parse_args()

    msp = is_msp(a.headcount, a.revenue) if a.msp is None else a.msp
    fot = annual_fot(a.headcount, a.salary, a.year, msp)
    rev = a.revenue * 1e6
    share = fot / rev * 100 if rev else 0

    salary_str = f"{a.salary:,.0f}".replace(",", " ")
    rate = monthly_contrib(a.salary, a.year, msp) / a.salary * 100
    rate_str = f"{rate:.1f}".replace(".", ",")
    print(f"ФОТ за год:        {fmt_rub(fot)}  ({a.headcount} чел. × {salary_str} ₽ × 12, "
          f"взносы {rate_str}% — {'МСП' if msp else 'общие ставки'}, {a.year})")
    print(f"Выручка:           {fmt_rub(rev)}")
    print(f"ФОТ от выручки:    {share:.1f}%".replace(".", ","))

    if a.contributions:
        expected = a.headcount * 12 * monthly_contrib(a.salary, a.year, msp)
        actual = a.contributions * 1e6
        delta = abs(expected - actual) / actual * 100
        print(f"\nСверка по взносам: по расчёту {fmt_rub(expected)}, уплачено {fmt_rub(actual)}, "
              f"расхождение {delta:.0f}%".replace(".", ","))
        if delta > 30:
            print("  расхождение велико — перепроверить ССЧ или среднюю ЗП, "
                  "в письме такую цифру не называть")

    # Главное: растёт ли ФОТ быстрее выручки.
    if a.revenue_prev:
        rev_d = (a.revenue - a.revenue_prev) / a.revenue_prev * 100
        hc_prev = a.headcount_prev or a.headcount
        sal_prev = a.salary_prev or a.salary
        fot_prev = annual_fot(hc_prev, sal_prev, a.year - 1, msp)
        fot_d = (fot - fot_prev) / fot_prev * 100 if fot_prev else 0

        print("\nДинамика год к году:")
        print(f"  выручка  {rev_d:+.1f}%".replace(".", ","))
        print(f"  ФОТ      {fot_d:+.1f}%".replace(".", ","))
        if a.headcount_prev:
            hc_d = (a.headcount - a.headcount_prev) / a.headcount_prev * 100
            print(f"  штат     {hc_d:+.1f}%  ({a.headcount_prev} → {a.headcount})"
                  .replace(".", ","))

        print()
        if fot_d > rev_d + 5:
            print("СИГНАЛ ЕСТЬ: ФОТ растёт быстрее выручки — ровно то, о чём тезис деки.")
            print("В письме это называется цифрами самой компании, без нашей оценки.")
        elif a.headcount_prev and (a.headcount - a.headcount_prev) / a.headcount_prev * 100 > rev_d:
            print("СИГНАЛ ЕСТЬ: штат растёт быстрее выручки.")
        else:
            print("Сигнала нет: выручка обгоняет расходы на людей.")
            print("Компания справляется без нас, письмо будет неуместным.")
    else:
        print("\nДля вывода о сигнале нужен предыдущий год: --revenue-prev "
              "и, желательно, --headcount-prev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
