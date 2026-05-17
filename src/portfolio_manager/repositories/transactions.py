from __future__ import annotations

from datetime import date

from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import Transaction


class TransactionRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, tx: Transaction) -> Transaction:
        self.db.execute(
            """
            INSERT INTO transactions (
                transaction_id, transaction_date, transaction_type, entity_kind, entity_id,
                quantity, price, amount, currency, fees, notes,
                fx_rate_to_base, fx_base_currency,
                market_index_level, sector_index_level, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                tx.transaction_id, tx.transaction_date, tx.transaction_type.value,
                tx.entity_kind.value, tx.entity_id, tx.quantity, tx.price, tx.amount,
                tx.currency, tx.fees, tx.notes,
                tx.fx_rate_to_base, tx.fx_base_currency,
                tx.market_index_level, tx.sector_index_level, tx.created_at,
            ],
        )
        return tx

    def update(self, tx: Transaction) -> Transaction:
        self.db.execute(
            """
            UPDATE transactions SET
                transaction_date = ?,
                transaction_type = ?,
                entity_kind = ?,
                entity_id = ?,
                quantity = ?,
                price = ?,
                amount = ?,
                currency = ?,
                fees = ?,
                notes = ?,
                fx_rate_to_base = ?,
                fx_base_currency = ?,
                market_index_level = ?,
                sector_index_level = ?
            WHERE transaction_id = ?
            """,
            [
                tx.transaction_date, tx.transaction_type.value, tx.entity_kind.value,
                tx.entity_id, tx.quantity, tx.price, tx.amount, tx.currency, tx.fees,
                tx.notes, tx.fx_rate_to_base, tx.fx_base_currency,
                tx.market_index_level, tx.sector_index_level, tx.transaction_id,
            ],
        )
        return tx

    def delete(self, transaction_id: str) -> None:
        self.db.execute("DELETE FROM transactions WHERE transaction_id = ?", [transaction_id])

    def get(self, transaction_id: str) -> Transaction:
        rows = self.db.fetchall_dict(
            "SELECT * FROM transactions WHERE transaction_id = ?", [transaction_id]
        )
        if not rows:
            raise NotFoundError(f"transaction {transaction_id!r} not found")
        return Transaction.model_validate(rows[0])

    def list_for_entity(self, entity_kind: str, entity_id: str) -> list[Transaction]:
        rows = self.db.fetchall_dict(
            """
            SELECT * FROM transactions
             WHERE entity_kind = ? AND entity_id = ?
             ORDER BY transaction_date DESC, created_at DESC
            """,
            [entity_kind, entity_id],
        )
        return [Transaction.model_validate(r) for r in rows]

    def list_all(
        self,
        *,
        entity_kind: str | None = None,
        entity_id: str | None = None,
        transaction_type: str | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int | None = None,
    ) -> list[Transaction]:
        clauses, params = [], []
        if entity_kind:
            clauses.append("entity_kind = ?"); params.append(entity_kind)
        if entity_id:
            clauses.append("entity_id = ?"); params.append(entity_id)
        if transaction_type:
            clauses.append("transaction_type = ?"); params.append(transaction_type)
        if since is not None:
            clauses.append("transaction_date >= ?"); params.append(since)
        if until is not None:
            clauses.append("transaction_date <= ?"); params.append(until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM transactions {where} ORDER BY transaction_date DESC, created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(limit)
        rows = self.db.fetchall_dict(sql, params)
        return [Transaction.model_validate(r) for r in rows]

    def list_recent(self, limit: int = 100) -> list[Transaction]:
        return self.list_all(limit=limit)
