#!/usr/bin/env python3
"""Диагностика из `_knowledge/testing_standard.md` §7.1: порча входа ОБЯЗАНА красить проверку.

На чужом наборе эталона в нашем формате нет, поэтому из `check()` работают только
те проверки, которым эталон не нужен. Вопрос ровно один: краснеет ли хоть одна из
них, если испортить входные данные. Каждая порча — отдельная копия набора.

ВНИМАНИЕ: результат «0 из 7» — состояние инструмента НА МОМЕНТ ОБМЕНА, 30.09.2026.
Функция `no_expected()` ниже — копия тогдашней `check()`, а не вызов текущей: файл
заморожен как свидетельство находки. Все три дефекта в тот же день исправлены, и
текущее состояние показывает `python split.py --data <набор> --corrupt` из корня
репозитория (краснеет 5 порч из 8). Разбор — `report.md`, раздел «Постскриптум».
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import adapt

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "src" / "data.json").read_text(encoding="utf-8"))


def no_expected(result, transfers, people, ledger, split):
    """Только те проверки check(), которым не нужен эталон."""
    fails = []
    balance = result["balance"]

    total = round(sum(balance.values()), 2)
    if abs(total) >= 0.005:
        fails.append(f"сумма балансов не ноль: {total:+.2f}")

    for row in ledger:
        if not row[7]:
            fails.append(f"{row[0]}: строка реестра без источника")

    moved_out = {p: 0.0 for p in people}
    moved_in = {p: 0.0 for p in people}
    for src, dst, amount in transfers:
        moved_out[src] += amount
        moved_in[dst] += amount
    for person in people:
        rest = round(balance[person] + moved_out[person] - moved_in[person], 2)
        if abs(rest) >= 0.005:
            fails.append(f"после переводов у {person} остаётся {rest:+.2f}")

    if len(transfers) > len(people) - 1:
        fails.append(f"переводов {len(transfers)}, предел n−1")

    return fails


def run(data):
    people, ledger = adapt.build(data)
    split = adapt.load_split_module()
    split.PEOPLE, split.RATE, split.LEDGER, split.MONEY_MOVES = people, 1.0, ledger, []
    result = split.compute()
    transfers = split.plan_transfers(result["balance"])
    return result, transfers, people, ledger, split


def c_amount(d):
    d["receipts"][0]["amount"] = 33000          # было 30 000
def c_payer(d):
    d["receipts"][2]["payer"] = d["people"][1]  # подменить плательщика
def c_share(d):
    r = next(r for r in d["receipts"] if len(r["for"]) < 5)
    r["for"] = list(d["people"])                # вернуть отсутствовавшего в дележ
def c_refund_sign(d):
    d["refunds"][0]["amount"] = -d["refunds"][0]["amount"]
def c_rate(d):
    k = sorted(d["rates_rub_per_gel"])[0]
    d["rates_rub_per_gel"][k] = 99.0            # курс втрое
def c_drop_receipt(d):
    d["receipts"].pop()                         # чек исчез целиком
def c_no_source(d):
    d["receipts"][0]["source"] = ""             # строка без источника

CASES = [
    ("сумма чека R01: 30 000 → 33 000", c_amount),
    ("плательщик чека подменён", c_payer),
    ("состав дележа: отсутствовавший возвращён", c_share),
    ("знак возврата V01 перевёрнут", c_refund_sign),
    ("курс на дату: 29,4 → 99,0", c_rate),
    ("последний чек удалён", c_drop_receipt),
    ("у чека R01 убран источник", c_no_source),
]


def main() -> int:
    base = run(copy.deepcopy(DATA))
    print("чистый набор:", no_expected(*base) or "проверки зелёные (ожидаемо)")
    print()
    red = 0
    for title, mutate in CASES:
        d = copy.deepcopy(DATA)
        mutate(d)
        try:
            fails = no_expected(*run(d))
        except SystemExit as e:
            fails = [f"скрипт упал: {e}"]
        mark = "КРАСНАЯ" if fails else "зелёная  ← порча не поймана"
        red += bool(fails)
        print(f"{mark:<28} {title}")
        for f in fails:
            print(f"      ✗ {f}")
    print(f"\nпоймано порч: {red} из {len(CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
