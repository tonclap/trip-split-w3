#!/usr/bin/env python3
"""Прогон НАШЕГО split.py по набору Максима (github.com/baramba/trip-settle-tbilisi).

Условие этапа 2 — прогнать свой инструмент на чужом наборе БЕЗ правок под него.
Поэтому `split.py` здесь не редактируется ни одной строкой: он импортируется как
модуль, а его константы (PEOPLE, RATE, LEDGER, MONEY_MOVES) подменяются снаружи
значениями, собранными из чужого `data.json`. Всё, что ниже, — адаптация ДАННЫХ.

Три места, где чужая модель не ложится на нашу, и что с ними сделано:

1. **Курс на дату.** У него четыре курса (`rates_rub_per_gel`), у нас один
   `RATE` на всю поездку. Пересчёт в рубли сделан здесь, по курсу на дату чека,
   а в модуль отдан `RATE = 1.0` и все суммы уже в рублях — так функция `rub()`
   остаётся нетронутой и ничего не искажает.
2. **Возвраты.** У нас нет понятия «возврат от продавца»: `MONEY_MOVES` — это
   передача денег между участниками. Возврат по чеку — это уменьшение самого
   расхода, поэтому он отдан отрицательной строкой реестра с тем же составом
   дележа, что у исходного чека.
3. **Спорное.** Его `disputed` в расчёт не входит по условию — переносится в
   отчёт списком, не в реестр.
"""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src" / "data.json"


def load_split_module():
    """Импортировать наш split.py как есть, не трогая файл."""
    root = HERE.parent.parent          # наш split.py берётся из корня репозитория,
    spec = importlib.util.spec_from_file_location(  # копии рядом нет: она бы устарела
        "split_asis", root / "split.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_rub(amount, currency: str, date: str, rates: dict) -> float:
    """Чужая валюта — по курсу на ДАТУ, а не по одному курсу поездки."""
    if currency == "RUB":
        return float(Decimal(str(amount)))
    rate = rates.get(date)
    if rate is None:
        raise SystemExit(f"нет курса на {date} — в наборе {sorted(rates)}")
    return float(round(Decimal(str(amount)) * Decimal(str(rate)), 2))


def build(data: dict):
    people = list(data["people"])
    rates = data["rates_rub_per_gel"]

    ledger = []
    for r in data["receipts"]:
        value = to_rub(r["amount"], r["currency"], r["date"], rates)
        ledger.append((r["id"], r["date"], r["payer"], value, "RUB",
                       list(r["for"]), r["what"], [r["source"]]))

    by_id = {r["id"]: r for r in data["receipts"]}
    for v in data["refunds"]:
        base = by_id.get(v["receipt"])
        if base is None:
            raise SystemExit(f"{v['id']}: возврат по неизвестному чеку {v['receipt']}")
        value = to_rub(v["amount"], v["currency"], v["date"], rates)
        # минус-строка: деньги вернулись плательщику, доля каждого уменьшилась
        ledger.append((v["id"], v["date"], v["to"], -value, "RUB",
                       list(base["for"]), f"возврат по {base['id']}: {v['what']}",
                       [v["source"]]))

    return people, ledger


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    people, ledger = build(data)

    split = load_split_module()
    split.PEOPLE = people
    split.RATE = 1.0          # суммы уже в рублях, пересчёт сделан по дате выше
    split.LEDGER = ledger
    split.MONEY_MOVES = []    # передач между участниками в его наборе нет

    result = split.compute()
    balance = result["balance"]
    transfers = split.plan_transfers(balance)

    print(f"набор: {data['trip']}")
    print(f"курсы по датам: {data['rates_rub_per_gel']}")
    print(f"чеков: {len(data['receipts'])}, возвратов: {len(data['refunds'])}, "
          f"спорного: {len(data['disputed'])}")
    print(f"\nвсего общих расходов: {result['total_shared']:,.2f} ₽".replace(",", " "))
    print(f"личных расходов вне котла: {result['personal']:,.2f} ₽".replace(",", " "))

    print(f"\n{'участник':<8} {'внёс':>12} {'его доля':>12} {'баланс':>12}")
    for p in people:
        print(f"{p:<8} {result['paid'][p]:>12,.2f} {result['owed'][p]:>12,.2f} "
              f"{balance[p]:>12,.2f}".replace(",", " "))
    print(f"\nсумма балансов: {sum(balance.values()):+.2f} ₽")

    print(f"\nплан переводов ({len(transfers)} шт, предел n−1 = {len(people) - 1}):")
    for src, dst, amount in transfers:
        print(f"  {src} → {dst}: {amount:,.2f} ₽".replace(",", " "))

    print("\nспорное (в расчёт не входит):")
    for d in data["disputed"]:
        print(f"  [{d['id']}, {d['source']}] {d['who']}: {d['what']} → {d['action']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
