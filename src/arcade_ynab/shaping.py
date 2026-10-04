"""Turn raw YNAB API objects into compact, model-friendly dicts.

Rules applied everywhere:
- Deleted entities are dropped.
- Amounts are converted from milliunits to currency units, with YNAB's
  ``*_formatted`` string alongside when the API provides one.
- YNAB's API names are mapped to the names used in the YNAB app
  (``budgeted`` -> ``assigned``, ``balance`` -> ``available`` on categories,
  ``to_be_budgeted`` -> ``ready_to_assign``).
- Keys with ``None`` values are omitted.
"""

from typing import Any

from arcade_ynab.money import from_milliunits

Raw = dict[str, Any]


def _compact(d: Raw) -> Raw:
    return {k: v for k, v in d.items() if v is not None}


def _amount(src: Raw, field: str, as_name: str | None = None) -> Raw:
    """Return ``{name: currency_amount, name_formatted: str}`` for one amount field."""
    name = as_name or field
    if field not in src:
        return {}
    out: Raw = {name: from_milliunits(src.get(field))}
    formatted = src.get(f"{field}_formatted")
    if formatted is not None:
        out[f"{name}_formatted"] = formatted
    return out


def live(items: list[Raw] | None) -> list[Raw]:
    """Drop entities YNAB marks as deleted."""
    return [item for item in items or [] if not item.get("deleted")]


def plan(p: Raw) -> Raw:
    out = _compact(
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "last_modified_on": p.get("last_modified_on"),
            "first_month": p.get("first_month"),
            "last_month": p.get("last_month"),
            "currency": (p.get("currency_format") or {}).get("iso_code"),
        }
    )
    if "accounts" in p:
        out["accounts"] = [account(a) for a in live(p["accounts"])]
    return out


def account(a: Raw) -> Raw:
    return _compact(
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "type": a.get("type"),
            "on_budget": a.get("on_budget"),
            "closed": a.get("closed"),
            "note": a.get("note"),
            **_amount(a, "balance"),
            **_amount(a, "cleared_balance"),
            **_amount(a, "uncleared_balance"),
            "transfer_payee_id": a.get("transfer_payee_id"),
            "direct_import_linked": a.get("direct_import_linked"),
            "direct_import_in_error": a.get("direct_import_in_error"),
            "last_reconciled_at": a.get("last_reconciled_at"),
        }
    )


def _goal(c: Raw) -> Raw | None:
    if not c.get("goal_type"):
        return None
    return _compact(
        {
            "type": c.get("goal_type"),
            **_amount(c, "goal_target", "target"),
            "target_date": c.get("goal_target_date"),
            "percentage_complete": c.get("goal_percentage_complete"),
            "months_to_budget": c.get("goal_months_to_budget"),
            **_amount(c, "goal_under_funded", "under_funded"),
            **_amount(c, "goal_overall_funded", "overall_funded"),
            **_amount(c, "goal_overall_left", "overall_left"),
            "snoozed": bool(c.get("goal_snoozed_at")) or None,
        }
    )


def category(c: Raw) -> Raw:
    return _compact(
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "category_group_id": c.get("category_group_id"),
            "category_group_name": c.get("category_group_name"),
            "hidden": c.get("hidden") or None,
            "note": c.get("note"),
            **_amount(c, "budgeted", "assigned"),
            **_amount(c, "activity"),
            **_amount(c, "balance", "available"),
            "goal": _goal(c),
        }
    )


def category_group(g: Raw, include_hidden: bool) -> Raw:
    out = _compact({"id": g.get("id"), "name": g.get("name"), "hidden": g.get("hidden") or None})
    if "categories" in g:
        out["categories"] = [
            category(c) for c in live(g["categories"]) if include_hidden or not c.get("hidden")
        ]
    return out


def month(m: Raw) -> Raw:
    return _compact(
        {
            "month": m.get("month"),
            "note": m.get("note"),
            **_amount(m, "to_be_budgeted", "ready_to_assign"),
            "age_of_money": m.get("age_of_money"),
            **_amount(m, "income"),
            **_amount(m, "budgeted", "assigned"),
            **_amount(m, "activity"),
        }
    )


def payee(p: Raw) -> Raw:
    return _compact(
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "transfer_account_id": p.get("transfer_account_id"),
        }
    )


def subtransaction(s: Raw) -> Raw:
    return _compact(
        {
            "id": s.get("id"),
            **_amount(s, "amount"),
            "memo": s.get("memo"),
            "payee_id": s.get("payee_id"),
            "payee_name": s.get("payee_name"),
            "category_id": s.get("category_id"),
            "category_name": s.get("category_name"),
            "transfer_account_id": s.get("transfer_account_id"),
        }
    )


def transaction(t: Raw) -> Raw:
    """Shape a transaction, including the "hybrid" rows returned by category/payee endpoints."""
    out = _compact(
        {
            "id": t.get("id"),
            "date": t.get("date"),
            **_amount(t, "amount"),
            "memo": t.get("memo"),
            "cleared": t.get("cleared"),
            "approved": t.get("approved"),
            "flag_color": t.get("flag_color") or None,
            "flag_name": t.get("flag_name") or None,
            "account_id": t.get("account_id"),
            "account_name": t.get("account_name"),
            "payee_id": t.get("payee_id"),
            "payee_name": t.get("payee_name"),
            "category_id": t.get("category_id"),
            "category_name": t.get("category_name"),
            "transfer_account_id": t.get("transfer_account_id"),
            "import_payee_name": t.get("import_payee_name"),
            "type": t.get("type"),
            "parent_transaction_id": t.get("parent_transaction_id"),
        }
    )
    subs = live(t.get("subtransactions"))
    if subs:
        out["subtransactions"] = [subtransaction(s) for s in subs]
    return out


def scheduled_transaction(t: Raw) -> Raw:
    out = _compact(
        {
            "id": t.get("id"),
            "date_first": t.get("date_first"),
            "date_next": t.get("date_next"),
            "frequency": t.get("frequency"),
            **_amount(t, "amount"),
            "memo": t.get("memo"),
            "flag_color": t.get("flag_color") or None,
            "account_id": t.get("account_id"),
            "account_name": t.get("account_name"),
            "payee_id": t.get("payee_id"),
            "payee_name": t.get("payee_name"),
            "category_id": t.get("category_id"),
            "category_name": t.get("category_name"),
            "transfer_account_id": t.get("transfer_account_id"),
        }
    )
    subs = live(t.get("subtransactions"))
    if subs:
        out["subtransactions"] = [subtransaction(s) for s in subs]
    return out


def money_movement(m: Raw) -> Raw:
    return _compact(
        {
            "id": m.get("id"),
            "month": m.get("month"),
            "moved_at": m.get("moved_at"),
            **_amount(m, "amount"),
            "from_category_id": m.get("from_category_id"),
            "to_category_id": m.get("to_category_id"),
            "note": m.get("note"),
            "money_movement_group_id": m.get("money_movement_group_id"),
        }
    )


def money_movement_group(g: Raw) -> Raw:
    return _compact(
        {
            "id": g.get("id"),
            "month": g.get("month"),
            "group_created_at": g.get("group_created_at"),
            "note": g.get("note"),
        }
    )


def truncate(items: list[Raw], limit: int) -> tuple[list[Raw], Raw]:
    """Apply a result limit; return the kept items and ``total_count``/``truncated`` info."""
    info: Raw = {"total_count": len(items), "truncated": len(items) > limit}
    return items[:limit], info
