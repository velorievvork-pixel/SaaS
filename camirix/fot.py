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

CONTRIB = 1.3  # страховые взносы, грубо


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
    a = ap.parse_args()

    fot = a.headcount * a.salary * 12 * CONTRIB
    rev = a.revenue * 1e6
    share = fot / rev * 100 if rev else 0

    salary_str = f"{a.salary:,.0f}".replace(",", " ")
    print(f"ФОТ за год:        {fmt_rub(fot)}  ({a.headcount} чел. × "
          f"{salary_str} ₽ × 12 × {CONTRIB})")
    print(f"Выручка:           {fmt_rub(rev)}")
    print(f"ФОТ от выручки:    {share:.1f}%".replace(".", ","))

    if a.contributions:
        implied = a.contributions * 1e6 / 0.3
        delta = abs(implied - fot / CONTRIB) / (fot / CONTRIB) * 100
        print(f"\nСверка по взносам: фонд ≈ {fmt_rub(implied)}, "
              f"расхождение с расчётом {delta:.0f}%".replace(".", ","))
        if delta > 30:
            print("  расхождение велико — перепроверить ССЧ или среднюю ЗП, "
                  "в письме такую цифру не называть")

    # Главное: растёт ли ФОТ быстрее выручки.
    if a.revenue_prev:
        rev_d = (a.revenue - a.revenue_prev) / a.revenue_prev * 100
        hc_prev = a.headcount_prev or a.headcount
        sal_prev = a.salary_prev or a.salary
        fot_prev = hc_prev * sal_prev * 12 * CONTRIB
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
