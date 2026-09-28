#!/usr/bin/env python3
"""Сверка реестра с самими данными — второй, независимый путь к тем же числам.

`split.py` считает балансы «внёс минус доля» на float с округлением до копеек.
Здесь то же самое считается иначе: попарной матрицей долгов на `Decimal`, а
минимальность плана переводов доказывается перебором разбиений, а не сравнением
с n−1. Оба скрипта сверяются с эталоном в `facts.md`, а не друг с другом.

Что проверяется:
  1. сумма, валюта и плательщик каждой строки реестра — против файла чека;
  2. число участников в чеке («Гостей: 4», «5 порций») — против состава дележа;
  3. каждая строка реестра опирается на сообщение чата, и это сообщение называет
     и чек, и состав дележа (иначе состав пришлось бы угадывать);
  4. все ссылки `M..`/`R..` из всех документов ведут в существующее;
  5. доли делятся без потери копеек;
  6. независимо посчитанные балансы совпадают с эталоном;
  7. план из эталона минимален: меньшим числом переводов долги не закрыть;
  8. объём набора соответствует заданию (4–5 человек, 30–40 сообщений, 10–15 чеков,
     две валюты, не меньше двух движений денег между участниками);
  9. гигиена: в папке нет токенов, кодов участника и длинных номеров карт.

Запуск: python verify.py   (код возврата 1 — что-то не сошлось)
"""

from __future__ import annotations

import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from split import LEDGER, MONEY_MOVES, PEOPLE, load_expected

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RECEIPTS = DATA / "receipts"
CHAT = DATA / "chat.md"

RATE = Decimal("34.20")
CENT = Decimal("0.01")

# держатель карты в чеке -> участник; наличные подписаны в чеке русским именем
CARD_HOLDERS = {
    "OLGA": "Оля",
    "MAKSIM": "Макс",
    "TIMUR": "Тимур",
    "VALERIA": "Лера",
    "VSEVOLOD": "Сева",
}

# каким словом сообщение обязано назвать состав дележа
SHARE_WORDS = {
    5: r"пятер|пятеро|пять|всех|все",
    4: r"четвер|четверо|четыре",
    1: r"личн",
}

PEOPLE_COUNT_PATTERNS = [
    r"Пассажиров:\s*(\d+)",
    r"Гостей:\s*(\d+)",
    r"на\s+(\d+)\s+персон",
    r"(\d+)\s+порций",
    r"(\d+)\s+человек",
]

fails: list[str] = []


def fail(msg: str) -> None:
    fails.append(msg)


def parse_receipt(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    payable = re.search(r"к оплате\s+([\d\s.,]+?)\s+([A-Z]{3})", text)
    total = payable or re.search(r"ИТОГО:\s*([\d\s.,]+?)\s+([A-Z]{3})", text)
    if not total:
        fail(f"{path.name}: не нашёл строку ИТОГО")
        return {}
    amount = Decimal(total.group(1).replace(" ", "").replace(",", "."))
    currency = total.group(2)

    payer = None
    holder = re.search(r"держатель\s+([A-Z]+)", text)
    cash = re.search(r"наличные\s*\(([^)]+)\)", text)
    if holder:
        payer = CARD_HOLDERS.get(holder.group(1))
        if payer is None:
            fail(f"{path.name}: держатель карты {holder.group(1)} не сопоставлен участнику")
    elif cash:
        payer = cash.group(1).strip()
    elif "наличные" in text:
        fail(f"{path.name}: оплата наличными без имени — плательщика пришлось бы угадывать")

    count = None
    for pattern in PEOPLE_COUNT_PATTERNS:
        found = re.search(pattern, text)
        if found:
            count = int(found.group(1))
            break

    return {"amount": amount, "currency": currency, "payer": payer, "count": count, "text": text}


def rub(amount: Decimal, currency: str) -> Decimal:
    value = amount * RATE if currency == "GEL" else amount
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def check_ledger_against_data(chat: str, messages: set) -> None:
    for rid, _date, payer, amount, cur, share, what, sources in LEDGER:
        path = RECEIPTS / f"{rid}.txt"
        if not path.exists():
            fail(f"{rid}: файла чека нет")
            continue
        receipt = parse_receipt(path)
        if not receipt:
            continue

        if Decimal(str(amount)) != receipt["amount"]:
            fail(f"{rid}: в реестре {amount}, в чеке {receipt['amount']}")
        if cur != receipt["currency"]:
            fail(f"{rid}: в реестре {cur}, в чеке {receipt['currency']}")
        if receipt["payer"] and receipt["payer"] != payer:
            fail(f"{rid}: в реестре платил {payer}, в чеке {receipt['payer']}")
        if receipt["count"] is not None and receipt["count"] != len(share):
            fail(f"{rid}: в чеке {receipt['count']} чел., в реестре делим на {len(share)} "
                 f"({', '.join(share)})")

        if not sources:
            fail(f"{rid}: строка реестра без источника")
            continue
        missing = [m for m in sources if m not in messages]
        if missing:
            fail(f"{rid}: источники {', '.join(missing)} — таких сообщений в чате нет")
            continue

        cited = "\n".join(message_text(chat, m) for m in sources)
        if rid not in cited:
            fail(f"{rid}: ни одно из сообщений {', '.join(sources)} не называет чек {rid}")
        word = SHARE_WORDS.get(len(share))
        if word is None:
            fail(f"{rid}: нет словаря для дележа на {len(share)}")
        elif not re.search(word, cited, re.I):
            fail(f"{rid}: состав дележа на {len(share)} не назван ни в одном из "
                 f"{', '.join(sources)} — это догадка, а не данные")

    for mv in MONEY_MOVES:
        src, dst, amount, cur, kind, _note, sources = mv
        if not sources:
            fail(f"движение {src}→{dst} {amount} {cur} без источника")
            continue
        missing = [m for m in sources if m not in messages]
        if missing:
            fail(f"движение {src}→{dst}: источники {', '.join(missing)} не существуют")
            continue
        cited = "\n".join(message_text(chat, m) for m in sources)
        plain = str(amount).rstrip("0").rstrip(".") if "." in str(amount) else str(amount)
        spaced = f"{int(amount):,}".replace(",", " ")
        if plain not in cited and spaced not in cited:
            fail(f"движение {src}→{dst}: сумма {amount} не названа в {', '.join(sources)}")
        if src not in cited and dst not in cited:
            fail(f"движение {src}→{dst}: участники не названы в {', '.join(sources)}")


def message_text(chat: str, mid: str) -> str:
    found = re.search(rf"\*\*{mid}\*\*(.*?)(?=\n\*\*M\d\d\*\*|\Z)", chat, re.S)
    return found.group(1) if found else ""


def balances_pairwise() -> dict:
    """Балансы попарной матрицей долгов: кто кому сколько должен по каждой строке."""
    debt = {a: {b: Decimal("0") for b in PEOPLE} for a in PEOPLE}
    total_shared = Decimal("0")
    personal = Decimal("0")

    for rid, _date, payer, amount, cur, share, _what, _src in LEDGER:
        value = rub(Decimal(str(amount)), cur)
        per = (value / len(share)).quantize(CENT, rounding=ROUND_HALF_UP)
        if per * len(share) != value:
            fail(f"{rid}: {value} ₽ не делится на {len(share)} без остатка "
                 f"({per} x {len(share)} = {per * len(share)})")
        for person in share:
            if person != payer:
                debt[person][payer] += per
        if len(share) > 1:
            total_shared += value
        else:
            personal += value

    balance = {p: sum(debt[q][p] for q in PEOPLE) - sum(debt[p].values()) for p in PEOPLE}

    for src, dst, amount, cur, _kind, _note, _s in MONEY_MOVES:
        value = rub(Decimal(str(amount)), cur)
        balance[src] += value
        balance[dst] -= value

    return {"balance": balance, "total_shared": total_shared, "personal": personal}


def min_transfers(balance: dict) -> int:
    """Минимум переводов = (кол-во ненулевых) − (макс. число групп, сходящихся в нуль)."""
    nonzero = [v for v in balance.values() if v != 0]
    n = len(nonzero)
    if n == 0:
        return 0
    full = (1 << n) - 1
    sums = [Decimal("0")] * (full + 1)
    for mask in range(1, full + 1):
        low = mask & -mask
        sums[mask] = sums[mask ^ low] + nonzero[low.bit_length() - 1]
    best = [0] * (full + 1)
    for mask in range(1, full + 1):
        low = mask & -mask
        sub = mask
        while sub:
            if sub & low and sums[sub] == 0:
                best[mask] = max(best[mask], best[mask ^ sub] + 1)
            sub = (sub - 1) & mask
    return n - best[full]


def check_links(messages: set) -> None:
    receipts = {p.stem for p in RECEIPTS.glob("R*.txt")}
    for doc in ["facts.md", "README.md", "solution/report.md", "split.py", "verify.py"]:
        path = HERE / doc
        if not path.exists():
            fail(f"{doc}: файла нет")
            continue
        text = path.read_text(encoding="utf-8")
        for mid in sorted(set(re.findall(r"\bM\d{2,3}\b", text))):
            if mid not in messages:
                fail(f"{doc}: ссылка на {mid} — такого сообщения нет")
        for rid in sorted(set(re.findall(r"\bR\d{2,3}\b", text))):
            if rid not in receipts:
                fail(f"{doc}: ссылка на {rid} — такого чека нет")


def check_scope(messages: set) -> None:
    receipts = list(RECEIPTS.glob("R*.txt"))
    if not 4 <= len(PEOPLE) <= 5:
        fail(f"участников {len(PEOPLE)}, задание просит 4–5")
    if not 30 <= len(messages) <= 40:
        fail(f"сообщений {len(messages)}, задание просит 30–40")
    if not 10 <= len(receipts) <= 15:
        fail(f"чеков {len(receipts)}, задание просит 10–15")
    currencies = {row[4] for row in LEDGER}
    if len(currencies) < 2:
        fail(f"валют {len(currencies)}, задание просит две")
    if len(MONEY_MOVES) < 2:
        fail(f"движений денег {len(MONEY_MOVES)}, задание просит пару возвратов")


def check_hygiene() -> None:
    patterns = {
        "токен или секрет": r"(?i)\b(token|secret|api[_-]?key|password)\b",
        "ссылка на бота": r"(?i)t\.me/|@[A-Za-z0-9_]{4,}",
        "номер карты целиком": r"\b\d{13,19}\b",
        # код участника бота — 8 знаков, буквы И цифры вместе: слово из одних букв
        # (OLGA, VSEVOLOD) и число (номер чека) под это не подходят
        "код участника": r"\b(?=[A-Z0-9]{8}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8}\b",
    }
    for path in sorted(HERE.rglob("*")):
        if not path.is_file() or ".git" in path.parts or path.suffix == ".pyc":
            continue
        if path.name == "verify.py":
            continue  # сам список образцов — не утечка; себя этот скрипт не сканирует
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in patterns.items():
            found = re.search(pattern, text)
            if found:
                fail(f"{path.relative_to(HERE)}: {label} — «{found.group(0)}»")


def main() -> int:
    chat = CHAT.read_text(encoding="utf-8")
    messages = set(re.findall(r"\*\*(M\d\d)\*\*", chat))

    check_scope(messages)
    check_links(messages)
    check_ledger_against_data(chat, messages)
    check_hygiene()

    expected = load_expected()
    result = balances_pairwise()

    for person in PEOPLE:
        want = Decimal(str(expected["balance"][person]))
        got = result["balance"][person]
        if got != want:
            fail(f"баланс {person}: независимо посчитано {got}, эталон {want}")
    for key, got in (("total_shared_rub", result["total_shared"]), ("personal_rub", result["personal"])):
        want = Decimal(str(expected[key]))
        if got != want:
            fail(f"{key}: независимо посчитано {got}, эталон {want}")

    least = min_transfers(result["balance"])
    if least != int(expected["transfers_count"]):
        fail(f"минимум переводов {least}, а в эталоне план из "
             f"{int(expected['transfers_count'])} — план не минимален")

    print("независимый пересчёт (Decimal, попарная матрица долгов):")
    for person in PEOPLE:
        print(f"  {person:<6} {result['balance'][person]:>+12}")
    print(f"  котёл {result['total_shared']} ₽ · личное {result['personal']} ₽")
    print(f"  минимально возможное число переводов: {least}")

    if fails:
        print("\nСВЕРКА НЕ ПРОШЛА:")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("\nсверка пройдена: реестр совпадает с чеками и чатом, ссылки целы, "
          "балансы и минимальность плана подтверждены независимым расчётом")
    return 0


if __name__ == "__main__":
    sys.exit(main())
