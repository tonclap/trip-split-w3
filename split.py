#!/usr/bin/env python3
"""Расчёт балансов и плана переводов по реестру расходов поездки.

Реестр LEDGER собран руками из чеков и чата (data/), и у каждой строки указан
источник: номер чека и номера сообщений, из которых следуют плательщик и состав
дележа. Строка без источника здесь запрещена — её отвергает verify.py.

Проверки не декоративные: посчитанные числа сверяются с эталоном из facts.md
(блок ```expected```), а план переводов — с самими балансами. Испорченная сумма,
перепутанный плательщик или перевёрнутый знак перевода роняют скрипт.

Запуск:  python split.py        # расчёт + сверка с эталоном
         python verify.py       # сверка реестра с данными в data/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS = HERE / "facts.md"

RATE = 34.20  # 1 GEL = 34,20 RUB, курс зафиксирован на 11.09.2026 (M03–M05)

PEOPLE = ["Оля", "Макс", "Тимур", "Лера", "Сева"]
ALL = ["Оля", "Макс", "Тимур", "Лера", "Сева"]
NO_SEVA = ["Оля", "Макс", "Тимур", "Лера"]

# (id, дата, кто платил, сумма, валюта, за кого делим, что, источники)
LEDGER = [
    ("R01", "11.09", "Оля",   540, "GEL", ALL,     "жильё, 3 ночи",       ["M02", "M20", "M21", "M22", "M23", "M24", "M25"]),
    ("R02", "11.09", "Макс",  180, "GEL", NO_SEVA, "ужин 11.09, «Шавма»", ["M09", "M11"]),
    ("R03", "11.09", "Тимур",  45, "GEL", NO_SEVA, "такси из аэропорта",  ["M06"]),
    ("R04", "12.09", "Макс",   96, "GEL", ALL,     "продукты, «Фреш»",    ["M14", "M16"]),
    ("R05", "12.09", "Лера",   62, "GEL", NO_SEVA, "вино и хачапури",     ["M17"]),
    ("R06", "13.09", "Сева",  250, "GEL", ALL,     "экскурсия в Мцхету",  ["M26"]),
    ("R07", "13.09", "Оля",   138, "GEL", ALL,     "обед, «Пурне»",       ["M27"]),
    ("R08", "12.09", "Макс", 3500, "RUB", ALL,     "аренда машины, 2 дня", ["M12", "M13"]),
    ("R09", "13.09", "Тимур", 780, "RUB", ALL,     "бензин",              ["M28"]),
    ("R10", "13.09", "Лера",  145, "GEL", ALL,     "ужин 13.09",          ["M34"]),
    ("R11", "14.09", "Макс",   50, "GEL", ALL,     "такси в аэропорт",    ["M38"]),
    ("R12", "14.09", "Оля",    48, "GEL", ALL,     "завтрак",             ["M37"]),
    ("R13", "13.09", "Тимур",  15, "GEL", ALL,     "парковка",            ["M29"]),
    ("R14", "12.09", "Сева",   28, "GEL", ["Сева"], "такси из аэропорта, личное", ["M19"]),
]

# Движения денег между участниками — всё, что не расход поездки, а передача из рук
# в руки. Правило одно на все три строки: кто отдал, у того долг уменьшился
# (баланс +), кто получил — у того требование уменьшилось (баланс −).
# (кто, кому, сумма, валюта, вид, пояснение, источники)
MONEY_MOVES = [
    ("Оля",   "Лера",   40, "GEL", "заём",          "наличные на личную покупку Леры, вне котла", ["M35", "M36"]),
    ("Лера",  "Оля",    40, "GEL", "возврат",       "заём закрыт полностью",                      ["M39", "M40"]),
    ("Тимур", "Макс", 2000, "RUB", "в счёт долга",  "часть долга Тимура, остальное в Москве",      ["M30", "M32", "M33"]),
]


def rub(amount: float, currency: str) -> float:
    return round(amount * RATE, 2) if currency == "GEL" else float(amount)


def compute() -> dict:
    """Балансы: сколько человек внёс минус его доли и минус переданные ему деньги."""
    paid = {p: 0.0 for p in PEOPLE}
    owed = {p: 0.0 for p in PEOPLE}
    total_shared = 0.0
    personal = 0.0

    for _id, _date, payer, amount, cur, share, _what, _src in LEDGER:
        value = rub(amount, cur)
        paid[payer] += value
        per = round(value / len(share), 2)
        for p in share:
            owed[p] += per
        if len(share) > 1:
            total_shared += value
        else:
            personal += value

    for src, dst, amount, cur, _kind, _note, _s in MONEY_MOVES:
        value = rub(amount, cur)
        paid[src] += value
        paid[dst] -= value

    balance = {p: round(paid[p] - owed[p], 2) for p in PEOPLE}
    return {
        "paid": paid,
        "owed": owed,
        "balance": balance,
        "total_shared": round(total_shared, 2),
        "personal": round(personal, 2),
    }


def plan_transfers(balance: dict) -> list:
    """Жадный план: крупнейший долг гасим крупнейшим требованием."""
    debtors = sorted(([p, -b] for p, b in balance.items() if b < -0.005), key=lambda x: -x[1])
    creditors = sorted(([p, b] for p, b in balance.items() if b > 0.005), key=lambda x: -x[1])
    transfers = []
    di = ci = 0
    while di < len(debtors) and ci < len(creditors):
        amount = round(min(debtors[di][1], creditors[ci][1]), 2)
        if amount > 0.005:
            transfers.append((debtors[di][0], creditors[ci][0], amount))
        debtors[di][1] = round(debtors[di][1] - amount, 2)
        creditors[ci][1] = round(creditors[ci][1] - amount, 2)
        if debtors[di][1] <= 0.005:
            di += 1
        if creditors[ci][1] <= 0.005:
            ci += 1
    return transfers


def load_expected(path: Path = FACTS) -> dict:
    """Эталон из facts.md — блок ```expected```. Числа записаны в файле эталона,
    а не выводятся здесь: иначе скрипт сверялся бы сам с собой."""
    text = path.read_text(encoding="utf-8")
    block = re.search(r"```expected\r?\n(.*?)```", text, re.S)
    if not block:
        raise SystemExit(f"в {path.name} нет блока ```expected``` — сверять не с чем")
    data: dict = {"balance": {}, "plan": []}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = (part.strip() for part in line.partition("="))
        if key.startswith("balance."):
            data["balance"][key.split(".", 1)[1]] = float(value)
        elif key.startswith("plan."):
            src, dst, amount = (v.strip() for v in value.split("|"))
            data["plan"].append((src, dst, float(amount)))
        else:
            data[key] = float(value)
    return data


def check(result: dict, transfers: list, expected: dict) -> list:
    """Список провалов; пустой — всё сошлось."""
    fails = []
    balance = result["balance"]

    total = round(sum(balance.values()), 2)
    if abs(total) >= 0.005:
        fails.append(f"сумма балансов не ноль: {total:+.2f} ₽")

    # строка без источника запрещена: в задании каждая строка расчёта опирается
    # на сообщение или чек, а не на удобство
    for row in LEDGER:
        if not row[7]:
            fails.append(f"{row[0]}: строка реестра без источника")
    for move in MONEY_MOVES:
        if not move[6]:
            fails.append(f"движение {move[0]}→{move[1]}: без источника")

    if abs(RATE - expected["rate"]) >= 1e-9:
        fails.append(f"курс {RATE} не совпал с эталонным {expected['rate']}")

    for key, got in (("total_shared_rub", result["total_shared"]), ("personal_rub", result["personal"])):
        want = expected[key]
        if abs(got - want) >= 0.005:
            fails.append(f"{key}: посчитано {got:.2f} ₽, эталон {want:.2f} ₽")

    for person in PEOPLE:
        want = expected["balance"].get(person)
        if want is None:
            fails.append(f"в эталоне нет баланса для «{person}»")
            continue
        if abs(balance[person] - want) >= 0.005:
            fails.append(f"баланс {person}: посчитано {balance[person]:+.2f} ₽, эталон {want:+.2f} ₽")

    # план обязан приводить каждого к нулю: отдал — долг закрылся, получил — требование
    moved_out = {p: 0.0 for p in PEOPLE}
    moved_in = {p: 0.0 for p in PEOPLE}
    for src, dst, amount in transfers:
        moved_out[src] += amount
        moved_in[dst] += amount
    for person in PEOPLE:
        rest = round(balance[person] + moved_out[person] - moved_in[person], 2)
        if abs(rest) >= 0.005:
            fails.append(f"после переводов у {person} остаётся {rest:+.2f} ₽, а должно быть 0")

    limit = len(PEOPLE) - 1
    if len(transfers) > limit:
        fails.append(f"переводов {len(transfers)}, предел n−1 = {limit}")
    if len(transfers) != int(expected["transfers_count"]):
        fails.append(f"переводов {len(transfers)}, эталон {int(expected['transfers_count'])}")

    got_plan = sorted((s, d, round(a, 2)) for s, d, a in transfers)
    want_plan = sorted((s, d, round(a, 2)) for s, d, a in expected["plan"])
    if got_plan != want_plan:
        fails.append(f"план переводов не совпал с эталоном: {got_plan} против {want_plan}")

    return fails


def main() -> int:
    result = compute()
    balance = result["balance"]
    transfers = plan_transfers(balance)
    expected = load_expected()

    print(f"курс: 1 ₾ = {RATE} ₽")
    print(f"всего общих расходов: {result['total_shared']:,.2f} ₽".replace(",", " "))
    print(f"личных расходов вне общего котла: {result['personal']:,.2f} ₽".replace(",", " "))
    print()
    print(f"{'участник':<8} {'внёс':>12} {'его доля':>12} {'баланс':>12}")
    for p in PEOPLE:
        print(f"{p:<8} {result['paid'][p]:>12,.2f} {result['owed'][p]:>12,.2f} "
              f"{balance[p]:>12,.2f}".replace(",", " "))
    print(f"\nсумма балансов: {abs(round(sum(balance.values()), 2)):.2f} ₽")

    print("\nдвижения денег между участниками (уже учтены в балансах):")
    for src, dst, amount, cur, kind, note, srcs in MONEY_MOVES:
        shown = f"{amount:,.2f} {'₾' if cur == 'GEL' else '₽'}".replace(",", " ")
        in_rub = f"{rub(amount, cur):,.2f}".replace(",", " ")
        print(f"  {src} → {dst}: {shown} = {in_rub} ₽ · {kind}, {note} ({', '.join(srcs)})")

    print(f"\nплан переводов ({len(transfers)} шт, предел n−1 = {len(PEOPLE) - 1}):")
    for src, dst, amount in transfers:
        print(f"  {src} → {dst}: {amount:,.2f} ₽".replace(",", " "))

    fails = check(result, transfers, expected)
    if fails:
        print("\nПРОВЕРКИ НЕ ПРОШЛИ:")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("\nпроверки пройдены: балансы, котёл, план переводов и число переводов "
          "совпали с эталоном facts.md; после плана у каждого ноль")
    return 0


if __name__ == "__main__":
    sys.exit(main())
