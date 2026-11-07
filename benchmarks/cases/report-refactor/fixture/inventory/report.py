"""A plain-text stock report, by warehouse."""


def render_report(items, *, sort_by="sku", show_empty=False):
    """Return the stock report for items, as text.

    items: dicts with sku, name, warehouse, qty and unit_cost (a string, in
    dollars). sort_by: "sku", or "value" for the highest stock value first.
    show_empty: keep items whose qty is zero; they are dropped otherwise.
    """
    rows = []
    for item in items:
        qty = int(item["qty"])
        if qty == 0 and not show_empty:
            continue
        cost = float(item["unit_cost"])
        value = round(qty * cost, 2)
        rows.append(
            {
                "sku": str(item["sku"]).strip().upper(),
                "name": item["name"].strip(),
                "warehouse": item["warehouse"].strip() or "unassigned",
                "qty": qty,
                "value": value,
            }
        )
    if sort_by == "value":
        rows.sort(key=lambda r: (-r["value"], r["sku"]))
    elif sort_by == "sku":
        rows.sort(key=lambda r: r["sku"])
    else:
        raise ValueError(f"unknown sort: {sort_by!r}")

    groups = {}
    for row in rows:
        groups.setdefault(row["warehouse"], []).append(row)

    lines = []
    for warehouse in sorted(groups):
        lines.append(f"== {warehouse} ==")
        lines.append(f"{'SKU':<10}{'NAME':<20}{'QTY':>6}{'VALUE':>12}")
        subtotal = 0.0
        for row in groups[warehouse]:
            lines.append(
                f"{row['sku']:<10}{row['name'][:20]:<20}{row['qty']:>6}{row['value']:>12.2f}"
            )
            subtotal += row["value"]
        lines.append(f"{'subtotal':<36}{round(subtotal, 2):>12.2f}")
        lines.append("")
    if not groups:
        lines.append("no stock")
    return "\n".join(lines) + "\n"
