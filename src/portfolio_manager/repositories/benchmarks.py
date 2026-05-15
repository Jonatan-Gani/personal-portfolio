from __future__ import annotations

from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import Benchmark


class BenchmarkRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, b: Benchmark) -> Benchmark:
        self.db.execute(
            """
            INSERT INTO benchmarks (
                benchmark_id, name, symbol, currency, country, price_provider,
                notes, created_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT (benchmark_id) DO UPDATE SET
                name = EXCLUDED.name,
                symbol = EXCLUDED.symbol,
                currency = EXCLUDED.currency,
                country = EXCLUDED.country,
                price_provider = EXCLUDED.price_provider,
                notes = EXCLUDED.notes,
                is_active = EXCLUDED.is_active
            """,
            [
                b.benchmark_id, b.name, b.symbol, b.currency, b.country,
                b.price_provider, b.notes, b.created_at, b.is_active,
            ],
        )
        return b

    def get(self, benchmark_id: str) -> Benchmark:
        rows = self.db.fetchall_dict("SELECT * FROM benchmarks WHERE benchmark_id = ?", [benchmark_id])
        if not rows:
            raise NotFoundError(f"benchmark {benchmark_id!r} not found")
        return Benchmark.model_validate(rows[0])

    def list_active(self) -> list[Benchmark]:
        rows = self.db.fetchall_dict("SELECT * FROM benchmarks WHERE is_active = TRUE ORDER BY name")
        return [Benchmark.model_validate(r) for r in rows]

    def list_all(self) -> list[Benchmark]:
        rows = self.db.fetchall_dict("SELECT * FROM benchmarks ORDER BY is_active DESC, name")
        return [Benchmark.model_validate(r) for r in rows]

    def deactivate(self, benchmark_id: str) -> None:
        self.db.execute(
            "UPDATE benchmarks SET is_active = FALSE WHERE benchmark_id = ?",
            [benchmark_id],
        )

    def delete(self, benchmark_id: str) -> None:
        self.db.execute("DELETE FROM benchmarks WHERE benchmark_id = ?", [benchmark_id])
