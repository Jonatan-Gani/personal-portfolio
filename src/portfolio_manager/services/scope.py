"""Account-scope helpers used by routes/services that need to filter snapshot
positions by which account a holding belongs to.

A "scope" is one of:
  - None / 'all'             — no filter
  - 'unassigned'             — holdings with NULL account_id
  - 'group:<group_id>'       — every active account in that group
  - 'account:<account_id>'   — a single account
"""
from __future__ import annotations


def parse_scope(scope: str | None, c) -> tuple[str, list[str] | None, str]:
    """Resolve a scope string into (label, account_ids_or_None, kind).

    `account_ids` semantics:
      - None              → no account filter (everything)
      - []                → no holdings match (forced empty)
      - [..ids..]         → only holdings tied to one of these ids
      - ['__unassigned__']→ sentinel: only holdings with NULL account_id

    `kind` is one of 'all' | 'unassigned' | 'group' | 'account', useful for UI.
    """
    if not scope or scope == "all":
        return ("All holdings", None, "all")
    if scope == "unassigned":
        return ("Unassigned", ["__unassigned__"], "unassigned")
    if scope.startswith("group:"):
        gid = scope.split(":", 1)[1]
        try:
            g = c.account_groups_repo.get(gid)
            label = f"Group · {g.name}"
        except Exception:  # noqa: BLE001
            label = "Group · ?"
        ids = [a.account_id for a in c.accounts_repo.list_in_group(gid)]
        return (label, ids, "group")
    if scope.startswith("account:"):
        aid = scope.split(":", 1)[1]
        try:
            a = c.accounts_repo.get(aid)
            label = a.name + (f" · {a.broker}" if a.broker else "")
        except Exception:  # noqa: BLE001
            label = "Account · ?"
        return (label, [aid], "account")
    return ("All holdings", None, "all")


def scope_filter_sql(account_ids: list[str] | None) -> tuple[str, list]:
    """Return a SQL fragment to be AND-ed into a query against `snapshot_positions p`
    that constrains which positions are in scope. Caller must ensure the SQL has
    LEFT JOINs to assets/cash_holdings/liabilities on entity_id+position_kind, or use
    the helper `scope_join_sql()` for those joins.

    Returns ("", []) if no filter (scope = all).
    """
    if account_ids is None:
        return ("", [])
    if account_ids == ["__unassigned__"]:
        return ("AND COALESCE(_a.account_id, _c.account_id, _l.account_id) IS NULL", [])
    if not account_ids:
        return ("AND 1=0", [])  # forced empty result
    placeholders = ",".join(["?"] * len(account_ids))
    return (
        f"AND COALESCE(_a.account_id, _c.account_id, _l.account_id) IN ({placeholders})",
        list(account_ids),
    )


def scope_join_sql() -> str:
    """LEFT JOINs to expose the account_id of each snapshot position. The aliases
    `_a`, `_c`, `_l` are used by `scope_filter_sql()` — keep them consistent."""
    return """
    LEFT JOIN assets        _a ON _a.asset_id     = p.entity_id AND p.position_kind = 'asset'
    LEFT JOIN cash_holdings _c ON _c.cash_id      = p.entity_id AND p.position_kind = 'cash'
    LEFT JOIN liabilities   _l ON _l.liability_id = p.entity_id AND p.position_kind = 'liability'
    """
