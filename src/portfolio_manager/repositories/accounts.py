from __future__ import annotations

from .._clock import utcnow
from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import Account, AccountGroup


class AccountGroupRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, group: AccountGroup) -> AccountGroup:
        group.updated_at = utcnow()
        self.db.execute(
            """
            INSERT INTO account_groups (
                group_id, name, kind, color, notes, sort_order,
                created_at, updated_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT (group_id) DO UPDATE SET
                name = EXCLUDED.name,
                kind = EXCLUDED.kind,
                color = EXCLUDED.color,
                notes = EXCLUDED.notes,
                sort_order = EXCLUDED.sort_order,
                updated_at = EXCLUDED.updated_at,
                is_active = EXCLUDED.is_active
            """,
            [
                group.group_id, group.name, group.kind, group.color, group.notes,
                group.sort_order, group.created_at, group.updated_at, group.is_active,
            ],
        )
        return group

    def get(self, group_id: str) -> AccountGroup:
        row = self.db.fetchall_dict("SELECT * FROM account_groups WHERE group_id = ?", [group_id])
        if not row:
            raise NotFoundError(f"account group {group_id!r} not found")
        return AccountGroup.model_validate(row[0])

    def list_active(self) -> list[AccountGroup]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM account_groups WHERE is_active = TRUE ORDER BY sort_order, name"
        )
        return [AccountGroup.model_validate(r) for r in rows]

    def list_all(self) -> list[AccountGroup]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM account_groups ORDER BY is_active DESC, sort_order, name"
        )
        return [AccountGroup.model_validate(r) for r in rows]

    def deactivate(self, group_id: str) -> None:
        self.db.execute(
            "UPDATE account_groups SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE group_id = ?",
            [group_id],
        )

    def delete(self, group_id: str) -> None:
        # Detach any accounts before removing the group so we don't leave dangling FKs.
        self.db.execute("UPDATE accounts SET group_id = NULL WHERE group_id = ?", [group_id])
        self.db.execute("DELETE FROM account_groups WHERE group_id = ?", [group_id])


class AccountRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, account: Account) -> Account:
        account.updated_at = utcnow()
        self.db.execute(
            """
            INSERT INTO accounts (
                account_id, group_id, name, broker, account_type, currency, country,
                notes, sort_order, created_at, updated_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (account_id) DO UPDATE SET
                group_id = EXCLUDED.group_id,
                name = EXCLUDED.name,
                broker = EXCLUDED.broker,
                account_type = EXCLUDED.account_type,
                currency = EXCLUDED.currency,
                country = EXCLUDED.country,
                notes = EXCLUDED.notes,
                sort_order = EXCLUDED.sort_order,
                updated_at = EXCLUDED.updated_at,
                is_active = EXCLUDED.is_active
            """,
            [
                account.account_id, account.group_id, account.name, account.broker,
                account.account_type, account.currency, account.country, account.notes,
                account.sort_order, account.created_at, account.updated_at, account.is_active,
            ],
        )
        return account

    def get(self, account_id: str) -> Account:
        row = self.db.fetchall_dict("SELECT * FROM accounts WHERE account_id = ?", [account_id])
        if not row:
            raise NotFoundError(f"account {account_id!r} not found")
        return Account.model_validate(row[0])

    def list_active(self) -> list[Account]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM accounts WHERE is_active = TRUE ORDER BY sort_order, name"
        )
        return [Account.model_validate(r) for r in rows]

    def list_all(self) -> list[Account]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM accounts ORDER BY is_active DESC, sort_order, name"
        )
        return [Account.model_validate(r) for r in rows]

    def list_in_group(self, group_id: str | None) -> list[Account]:
        if group_id is None:
            rows = self.db.fetchall_dict(
                "SELECT * FROM accounts WHERE group_id IS NULL AND is_active = TRUE ORDER BY sort_order, name"
            )
        else:
            rows = self.db.fetchall_dict(
                "SELECT * FROM accounts WHERE group_id = ? AND is_active = TRUE ORDER BY sort_order, name",
                [group_id],
            )
        return [Account.model_validate(r) for r in rows]

    def deactivate(self, account_id: str) -> None:
        self.db.execute(
            "UPDATE accounts SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
            [account_id],
        )

    def delete(self, account_id: str) -> None:
        # Detach holdings before removing — they fall back to "Unassigned".
        self.db.execute("UPDATE assets        SET account_id = NULL WHERE account_id = ?", [account_id])
        self.db.execute("UPDATE cash_holdings SET account_id = NULL WHERE account_id = ?", [account_id])
        self.db.execute("UPDATE liabilities   SET account_id = NULL WHERE account_id = ?", [account_id])
        self.db.execute("DELETE FROM accounts WHERE account_id = ?", [account_id])
