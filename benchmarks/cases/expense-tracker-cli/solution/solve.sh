cat > expense_tracker.py <<'APPEOF'
"""A command-line expense tracker: add, list and total, kept in expenses.json."""

import argparse
import json
from pathlib import Path

STORE = Path("expenses.json")


def load(path=STORE):
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(expenses, path=STORE):
    path.write_text(json.dumps(expenses, indent=2) + "\n")


def add(amount, category, note="", path=STORE):
    expenses = load(path)
    expenses.append({"amount": float(amount), "category": category, "note": note})
    save(expenses, path)


def totals(expenses):
    result = {}
    for item in expenses:
        result[item["category"]] = result.get(item["category"], 0) + item["amount"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="expense_tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    add_cmd = sub.add_parser("add", help="add an expense")
    add_cmd.add_argument("amount", type=float)
    add_cmd.add_argument("category")
    add_cmd.add_argument("--note", default="")
    sub.add_parser("list", help="list expenses")
    sub.add_parser("total", help="total spending by category")
    args = parser.parse_args(argv)
    if args.command == "add":
        add(args.amount, args.category, args.note)
        print(f"added {args.amount:.2f} to {args.category}")
    elif args.command == "list":
        for item in load():
            print(f"{item['amount']:.2f}\t{item['category']}\t{item['note']}")
    else:
        for category, amount in sorted(totals(load()).items()):
            print(f"{category}\t{amount:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
APPEOF
mkdir -p tests
cat > tests/test_expense_tracker.py <<'TESTEOF'
import json

from expense_tracker import add, load, totals


def test_add_then_load(tmp_path):
    path = tmp_path / "expenses.json"
    add(12.5, "food", "lunch", path)
    assert json.loads(path.read_text()) == [
        {"amount": 12.5, "category": "food", "note": "lunch"}
    ]
    assert load(path)[0]["category"] == "food"


def test_totals_by_category():
    expenses = [
        {"amount": 2.0, "category": "food"},
        {"amount": 3.0, "category": "food"},
        {"amount": 1.0, "category": "fuel"},
    ]
    assert totals(expenses) == {"food": 5.0, "fuel": 1.0}
TESTEOF
