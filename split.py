#!/usr/bin/env python3
"""Расчёт балансов и плана переводов по реестру расходов поездки.

Реестр собран руками из чеков и чата, и у каждой строки указан источник: номер
чека и номера сообщений, из которых следуют плательщик и состав дележа. Строка
без источника здесь запрещена — её отвергают и `check_structure()`, и verify.py.

Реестр по умолчанию — наша поездка (константы `PEOPLE`/`RATE`/`LEDGER`/
`MONEY_MOVES` ниже). Любой другой набор подаётся файлом: `--data <файл.json>`,
схема — в `data/registry.schema.md`. До 30.09.2026 входа не было вовсе, и
«прогнать инструмент на чужом наборе» приходилось делать переходником снаружи
(этап 2 недели 1) — это и было первой находкой обмена.

Чего проверки НЕ доказывают — сказано прямо в выводе. Часть их тождественна по
построению (сумма балансов ноль, переводов не больше n−1), и зелёными они бывают
на испорченных данных; ловит порчу либо эталон (`facts.md`), либо сверка с
первичными данными (`verify.py`). Что именно ловится без эталона, показывает
`--corrupt`.

Запуск:  python split.py                      # наш набор + сверка с facts.md
         python split.py --data X.json        # чужой набор, структурные проверки
         python split.py --corrupt            # порча входа против проверок
         python verify.py                     # сверка реестра с данными в data/
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS = HERE / "facts.md"
CENT = Decimal("0.01")

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


# --------------------------------------------------------------------------- #
# реестр: свой по умолчанию, чужой — файлом
# --------------------------------------------------------------------------- #

def default_registry() -> dict:
    """Наш набор. Читается из глобалей, а не из значений по умолчанию аргументов:
    так внешняя подмена `split.LEDGER = ...` продолжает работать."""
    return {
        "trip": "своя поездка (набор недели 1)",
        "people": list(PEOPLE),
        "rate": RATE,
        "ledger": list(LEDGER),
        "moves": list(MONEY_MOVES),
        "source_ids": None,   # первичных id отдельно нет: реестр и есть источник
    }


def load_registry(path: Path) -> dict:
    """Чужой набор из JSON. Приводит всё к рублям здесь, по курсу на дату строки,
    и отдаёт реестр с `rate = 1.0`: функция `rub()` остаётся нетронутой.

    Схема (обязательное): people, receipts[id,date,payer,amount,currency,for,what,sources].
    Необязательное: rates (один курс на валюту) или rates_by_date, refunds
    (возврат по чеку от третьей стороны), moves (передача денег между участниками).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    people = list(data["people"])
    flat = {k: float(v) for k, v in (data.get("rates") or {}).items()}
    by_date = data.get("rates_by_date") or {}
    for key in list(data):
        if key.startswith("rates_") and key.endswith("_per_brl"):  # совместимость с наборами обмена
            by_date = {"BRL": data[key]}
        elif key.startswith("rates_") and key.endswith("_per_gel"):
            by_date = {"GEL": data[key]}

    def to_rub(amount, currency: str, date: str, what: str) -> float:
        if currency in ("RUB", data.get("base_currency", "RUB")) and currency == "RUB":
            return float(Decimal(str(amount)))
        if currency in flat:
            rate = flat[currency]
        else:
            table = by_date.get(currency) or {}
            if date not in table:
                raise SystemExit(f"{what}: нет курса {currency} на {date} "
                                 f"(в наборе {sorted(table) or 'курсов нет'})")
            rate = table[date]
        return float((Decimal(str(amount)) * Decimal(str(rate))).quantize(CENT, rounding=ROUND_HALF_UP))

    def sources_of(row) -> list:
        """`sources` списком или `source` одной ссылкой: второе встречается в наборах
        обмена, и требовать от чужого формата нашего — значит снова не иметь входа."""
        if row.get("sources"):
            return list(row["sources"])
        return [row["source"]] if row.get("source") else []

    ledger, source_ids = [], []
    for r in data["receipts"]:
        source_ids.append(r["id"])
        ledger.append((r["id"], r["date"], r["payer"],
                       to_rub(r["amount"], r["currency"], r["date"], r["id"]), "RUB",
                       list(r["for"]), r.get("what", ""), sources_of(r)))

    by_id = {r["id"]: r for r in data["receipts"]}
    for v in data.get("refunds") or []:
        source_ids.append(v["id"])
        base = by_id.get(v["receipt"])
        if base is None:
            raise SystemExit(f"{v['id']}: возврат по неизвестному чеку {v['receipt']}")
        # минус-строка: деньги вернулись плательщику, доля каждого уменьшилась
        ledger.append((v["id"], v["date"], v["to"],
                       -to_rub(v["amount"], v["currency"], v["date"], v["id"]), "RUB",
                       list(base["for"]), f"возврат по {base['id']}: {v.get('what', '')}",
                       sources_of(v)))

    moves = [(m["from"], m["to"], to_rub(m["amount"], m["currency"], m.get("date", ""), m.get("id", "move")),
              "RUB", m.get("kind", ""), m.get("note", ""), sources_of(m))
             for m in (data.get("moves") or [])]

    return {
        "trip": data.get("trip", str(path)),
        "people": people,
        "rate": 1.0,             # суммы уже в рублях, пересчёт сделан выше
        "ledger": ledger,
        "moves": moves,
        "source_ids": source_ids,
        "disputed": data.get("disputed") or [],
        "notes": data.get("notes") or [],
    }


# --------------------------------------------------------------------------- #
# расчёт
# --------------------------------------------------------------------------- #

def rub(amount: float, currency: str, rate: float | None = None) -> float:
    rate = RATE if rate is None else rate
    return round(amount * rate, 2) if currency == "GEL" else float(amount)


def allocate(value: float, count: int) -> list[float]:
    """Разложить сумму на `count` долей ТОЧНО, до копейки.

    `round(value / count, 2)` теряет или добавляет остаток (13 копеек на 6
    участников делятся как 2,17 x 6 = 13,02), и у такой ошибки два следствия:
    сумма балансов перестаёт быть нулём, а инвариант «сумма балансов = 0»
    краснеет на верном расчёте. Поймано 30.09.2026 на приёмочном наборе этапа 3
    (шестеро участников, остаток −0,08 ₽). Остаток раздаётся по копейке первым
    участникам в порядке состава дележа — правило произвольное, но одинаковое
    для всех прогонов.
    """
    cents = int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    sign = -1 if cents < 0 else 1
    cents = abs(cents)
    base, rest = divmod(cents, count)
    return [sign * (base + (1 if i < rest else 0)) / 100 for i in range(count)]


def compute(registry: dict | None = None) -> dict:
    """Балансы: сколько человек внёс минус его доли и минус переданные ему деньги."""
    reg = registry or default_registry()
    people, rate = reg["people"], reg["rate"]
    paid = {p: 0.0 for p in people}
    owed = {p: 0.0 for p in people}
    total_shared = 0.0
    personal = 0.0

    for _id, _date, payer, amount, cur, share, _what, _src in reg["ledger"]:
        value = rub(amount, cur, rate)
        paid[payer] = round(paid[payer] + value, 2)
        for person, part in zip(share, allocate(value, len(share))):
            owed[person] = round(owed[person] + part, 2)
        if len(share) > 1:
            total_shared = round(total_shared + value, 2)
        else:
            personal = round(personal + value, 2)

    for src, dst, amount, cur, _kind, _note, _s in reg["moves"]:
        value = rub(amount, cur, rate)
        paid[src] = round(paid[src] + value, 2)
        paid[dst] = round(paid[dst] - value, 2)

    balance = {p: round(paid[p] - owed[p], 2) for p in people}
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


# --------------------------------------------------------------------------- #
# проверки: сначала те, что живут без эталона
# --------------------------------------------------------------------------- #

def has_source(sources) -> bool:
    """Источник есть, если в списке есть хоть одна непустая ссылка.

    Было `if not row[7]`: список проверялся на пустоту, а не его содержимое, и
    источник `[""]` проходил молча. Поймано 30.09.2026 на этапе 2."""
    return any(str(s).strip() for s in (sources or []))


def check_structure(registry: dict, result: dict, transfers: list) -> list:
    """Провалы, которые видны БЕЗ эталона — по одному реестру и первичным id.

    Сюда не входит ничего, что требует знать правильный ответ: такие проверки
    живут в `check_against_expected()` и в verify.py. Порядок именно такой,
    потому что без эталона у скрипта раньше не оставалось ни одной проверки,
    способной провалиться.
    """
    fails = []
    people = set(registry["people"])
    seen = {}

    for row in registry["ledger"]:
        rid, _date, payer, amount, cur, share, _what, sources = row
        if rid in seen:
            fails.append(f"{rid}: id встречается дважды в реестре")
        seen[rid] = row
        if not has_source(sources):
            fails.append(f"{rid}: строка реестра без источника")
        if payer not in people:
            fails.append(f"{rid}: платил «{payer}», а такого участника в наборе нет")
        if not share:
            fails.append(f"{rid}: состав дележа пуст")
        unknown = [p for p in share if p not in people]
        if unknown:
            fails.append(f"{rid}: в составе дележа не участники набора: {', '.join(unknown)}")
        if len(set(share)) != len(share):
            fails.append(f"{rid}: участник в составе дележа повторяется")
        value = rub(amount, cur, registry["rate"])
        parts = allocate(value, len(share)) if share else []
        if share and abs(sum(parts) - value) >= 0.005:
            fails.append(f"{rid}: доли {sum(parts):.2f} ₽ не равны сумме строки {value:.2f} ₽")
        # возврат обязан быть минусом и не больше исходного чека
        base_id = re.match(r"возврат по (\w+)", _what or "")
        if base_id:
            if value > 0:
                fails.append(f"{rid}: возврат по {base_id.group(1)} записан плюсом — "
                             f"перевёрнутый знак не уменьшает расход, а удваивает его")
            base = seen.get(base_id.group(1))
            if base is not None:
                limit = rub(base[3], base[4], registry["rate"])
                if abs(value) - limit >= 0.005:
                    fails.append(f"{rid}: возврат {abs(value):.2f} ₽ больше самого чека "
                                 f"{base_id.group(1)} на {limit:.2f} ₽")

    for move in registry["moves"]:
        if not has_source(move[6]):
            fails.append(f"движение {move[0]}→{move[1]}: без источника")
        for who in (move[0], move[1]):
            if who not in people:
                fails.append(f"движение {move[0]}→{move[1]}: «{who}» не участник набора")

    # каждая первичная сущность обязана попасть в реестр: иначе удалённая строка
    # не видна вообще — расчёт просто тихо считает меньше
    if registry.get("source_ids"):
        missing = [i for i in registry["source_ids"] if i not in seen]
        if missing:
            fails.append(f"в реестр не попали чеки/возвраты из набора: {', '.join(missing)}")

    # план обязан приводить каждого к нулю: отдал — долг закрылся, получил — требование
    balance = result["balance"]
    moved_out = {p: 0.0 for p in registry["people"]}
    moved_in = {p: 0.0 for p in registry["people"]}
    for src, dst, amount in transfers:
        moved_out[src] += amount
        moved_in[dst] += amount
    for person in registry["people"]:
        rest = round(balance[person] + moved_out[person] - moved_in[person], 2)
        if abs(rest) >= 0.005:
            fails.append(f"после переводов у {person} остаётся {rest:+.2f} ₽, а должно быть 0")

    return fails


# Проверки, зелёные по построению: держать списком, а не выдавать за гарантию.
TAUTOLOGIES = [
    "сумма балансов равна нулю — это Σ внесённого минус Σ долей, "
    "а доли с 30.09.2026 раздаются точно до копейки: ноль выходит из формулы",
    "переводов не больше n−1 — жадный зачёт долгов даёт это по построению",
]


def check_against_expected(registry: dict, result: dict, transfers: list, expected: dict) -> list:
    """Провалы против эталона — единственное, что ловит испорченную сумму, курс
    или подменённого плательщика. Без эталона этих проверок нет вовсе."""
    fails = []
    balance = result["balance"]

    if abs(registry["rate"] - expected["rate"]) >= 1e-9:
        fails.append(f"курс {registry['rate']} не совпал с эталонным {expected['rate']}")

    for key, got in (("total_shared_rub", result["total_shared"]), ("personal_rub", result["personal"])):
        want = expected[key]
        if abs(got - want) >= 0.005:
            fails.append(f"{key}: посчитано {got:.2f} ₽, эталон {want:.2f} ₽")

    for person in registry["people"]:
        want = expected["balance"].get(person)
        if want is None:
            fails.append(f"в эталоне нет баланса для «{person}»")
            continue
        if abs(balance[person] - want) >= 0.005:
            fails.append(f"баланс {person}: посчитано {balance[person]:+.2f} ₽, эталон {want:+.2f} ₽")

    if len(transfers) != int(expected["transfers_count"]):
        fails.append(f"переводов {len(transfers)}, эталон {int(expected['transfers_count'])}")

    got_plan = sorted((s, d, round(a, 2)) for s, d, a in transfers)
    want_plan = sorted((s, d, round(a, 2)) for s, d, a in expected["plan"])
    if got_plan != want_plan:
        fails.append(f"план переводов не совпал с эталоном: {got_plan} против {want_plan}")

    return fails


def check(result: dict, transfers: list, expected: dict, registry: dict | None = None) -> list:
    """Совместимость: структурные проверки плюс сверка с эталоном, одним списком."""
    reg = registry or default_registry()
    return check_structure(reg, result, transfers) + \
        check_against_expected(reg, result, transfers, expected)


# --------------------------------------------------------------------------- #
# диагностика: порча входа обязана красить проверку
# --------------------------------------------------------------------------- #

def corruptions(registry: dict) -> list[tuple[str, dict, str]]:
    """Семь порч входа и то, чем каждая ловится. `catcher` — честное ожидание:
    «структура» ловится без эталона, «эталон» — только сверкой с facts.md,
    «первичные данные» — только verify.py по файлам чеков."""
    out = []

    def variant(label, catcher, mutate):
        reg = copy.deepcopy(registry)
        mutate(reg)
        out.append((label, reg, catcher))

    def set_row(reg, i, field, value):
        row = list(reg["ledger"][i])
        row[field] = value
        reg["ledger"][i] = tuple(row)

    variant("сумма чека изменена на +1000 ₽", "эталон",
            lambda reg: set_row(reg, 0, 3, reg["ledger"][0][3] + 1000))
    variant("плательщик подменён другим участником", "эталон",
            lambda reg: set_row(reg, 0, 2, next(p for p in reg["people"] if p != reg["ledger"][0][2])))
    variant("состав дележа урезан на одного", "эталон",
            lambda reg: set_row(reg, 0, 5, list(reg["ledger"][0][5])[:-1]))
    variant("источник опустошён до [\"\"]", "структура",
            lambda reg: set_row(reg, 0, 7, [""]))
    variant("состав дележа опустошён", "структура",
            lambda reg: set_row(reg, 0, 5, []))
    variant("в составе дележа посторонний", "структура",
            lambda reg: set_row(reg, 0, 5, list(reg["ledger"][0][5]) + ["Посторонний"]))
    variant("строка реестра удалена", "структура" if registry.get("source_ids") else "эталон",
            lambda reg: reg["ledger"].pop(0))

    signed = [i for i, r in enumerate(registry["ledger"]) if r[3] < 0]
    if signed:
        i = signed[0]
        variant("у возврата перевёрнут знак", "структура",
                lambda reg, i=i: set_row(reg, i, 3, -reg["ledger"][i][3]))
    else:
        variant("курс утроен", "эталон",
                lambda reg: reg.update(rate=reg["rate"] * 3))

    return out


def run_corruption_diagnostic(registry: dict, expected: dict | None) -> int:
    print("диагностика: порча входа обязана красить проверку "
          "(_knowledge/testing_standard.md §7.1)\n")
    clean = compute(registry)
    clean_fails = check_structure(registry, clean, plan_transfers(clean["balance"]))
    if expected:
        clean_fails += check_against_expected(registry, clean, plan_transfers(clean["balance"]), expected)
    print(f"чистый набор: {'проверки пройдены' if not clean_fails else 'ПРОВАЛ — ' + '; '.join(clean_fails)}")
    if clean_fails:
        print("\nчистый набор уже красный — диагностика порчи бессмысленна")
        return 1

    unexpected = 0
    for label, reg, catcher in corruptions(registry):
        try:
            result = compute(reg)
            transfers = plan_transfers(result["balance"])
            fails = check_structure(reg, result, transfers)
            where = "структура" if fails else ""
            if expected and not fails:
                fails = check_against_expected(reg, result, transfers, expected)
                where = "эталон" if fails else ""
        except Exception as exc:                       # падение — тоже красный
            fails, where = [f"исключение: {exc!r}"], "структура"
        if fails:
            mark = "✓ покраснело"
            note = f"({where})"
        elif expected is None and catcher == "эталон":
            mark = "— не ловится"
            note = "(ждёт эталона, его нет — это граница инструмента, а не дефект)"
        else:
            mark = "✗ ЗЕЛЁНОЕ"
            note = f"(должно было поймать: {catcher})"
            unexpected += 1
        print(f"  {mark:<14} {label:<42} {note}")

    print(f"\nне пойманных порч, которые должны были поймать: {unexpected}")
    if expected is None:
        print("без эталона (--data без facts.md) ловится только структура: "
              "сумма, курс и подменённый плательщик требуют либо эталона, "
              "либо сверки с первичными данными (verify.py)")
    return 1 if unexpected else 0


# --------------------------------------------------------------------------- #

def money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="балансы и план переводов по реестру поездки")
    ap.add_argument("--data", type=Path, help="реестр чужого набора (JSON); по умолчанию — наш")
    ap.add_argument("--expected", type=Path, help=f"файл эталона; по умолчанию {FACTS.name} для своего набора")
    ap.add_argument("--no-expected", action="store_true", help="не сверяться с эталоном вовсе")
    ap.add_argument("--corrupt", action="store_true", help="диагностика: порча входа против проверок")
    args = ap.parse_args(argv)

    registry = load_registry(args.data) if args.data else default_registry()

    expected = None
    if not args.no_expected:
        path = args.expected or (None if args.data else FACTS)
        if path is not None:
            expected = load_expected(path)

    if args.corrupt:
        return run_corruption_diagnostic(registry, expected)

    result = compute(registry)
    balance = result["balance"]
    transfers = plan_transfers(balance)
    people = registry["people"]

    print(f"набор: {registry['trip']}")
    if registry["rate"] != 1.0:
        print(f"курс: 1 ₾ = {registry['rate']} ₽")
    print(f"всего общих расходов: {money(result['total_shared'])} ₽")
    print(f"личных расходов вне общего котла: {money(result['personal'])} ₽")
    print()
    print(f"{'участник':<8} {'внёс':>12} {'его доля':>12} {'баланс':>12}")
    for p in people:
        print(f"{p:<8} {money(result['paid'][p]):>12} {money(result['owed'][p]):>12} "
              f"{balance[p]:>+12,.2f}".replace(",", " "))
    print(f"\nсумма балансов: {sum(balance.values()):+.2f} ₽")

    if registry["moves"]:
        print("\nдвижения денег между участниками (уже учтены в балансах):")
        for src, dst, amount, cur, kind, note, srcs in registry["moves"]:
            shown = f"{money(amount)} {'₾' if cur == 'GEL' else '₽'}"
            print(f"  {src} → {dst}: {shown} = {money(rub(amount, cur, registry['rate']))} ₽ · "
                  f"{kind}, {note} ({', '.join(srcs)})")

    print(f"\nплан переводов ({len(transfers)} шт, предел n−1 = {len(people) - 1}):")
    for src, dst, amount in transfers:
        print(f"  {src} → {dst}: {money(amount)} ₽")

    if registry.get("disputed"):
        print("\nспорное (в расчёт не входит):")
        for d in registry["disputed"]:
            refs = d.get("sources") or ([d["source"]] if d.get("source") else [])
            print(f"  [{d.get('id', '?')}{' · ' + ', '.join(refs) if refs else ''}] "
                  f"{d.get('who', '')}: {d.get('what', '')}")
            print(f"      → {d.get('action', '')}")

    fails = check_structure(registry, result, transfers)
    if expected:
        fails += check_against_expected(registry, result, transfers, expected)

    if fails:
        print("\nПРОВЕРКИ НЕ ПРОШЛИ:")
        for f in fails:
            print(f"  ✗ {f}")
        return 1

    if expected:
        print("\nпроверки пройдены: реестр цел, и балансы, котёл, план переводов и "
              "число переводов совпали с эталоном facts.md")
    else:
        print("\nструктурные проверки пройдены: реестр цел (источники, состав дележа, "
              "знак и размер возвратов, полнота реестра, план приводит к нулю).")
        print("ЧЕГО ЭТО НЕ ДОКАЗЫВАЕТ: эталона у набора нет, поэтому испорченная сумма, "
              "курс или подменённый плательщик здесь не ловятся — «зелёный» означает "
              "только внутреннюю целостность. Проверить: python split.py --data ... --corrupt")
    for t in TAUTOLOGIES:
        print(f"  (не доказательство) {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
