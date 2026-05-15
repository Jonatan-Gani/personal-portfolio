from __future__ import annotations

from dataclasses import dataclass

from ..db.connection import Database
from ..repositories.targets import VALID_DIMENSIONS, TargetAllocationRepository
from .exposure import ExposureService


@dataclass
class DriftRow:
    bucket: str
    target_weight: float | None     # None if no target set for this bucket (untargeted)
    current_value: float            # in report currency
    current_weight: float           # 0..1
    target_value: float | None      # None if untargeted
    delta_value: float              # current - target (positive = over-allocated)
    delta_weight: float             # current_weight - target_weight (None target → 0)


@dataclass
class DriftReport:
    dimension: str
    report_currency: str
    snapshot_id: str
    total_value: float              # denominator the weights are computed against
    rows: list[DriftRow]
    targets_sum: float              # sum of target weights set for this dimension (<= 1)
    untargeted_share: float         # 1 - targets_sum (the "rest of portfolio" pool)


class DriftService:
    """Compare the latest snapshot's per-bucket weights against user-set targets.
    A bucket is the value of the chosen dimension (e.g. 'equity' for asset_class).
    Surfaces both current/target weight and the dollar amount you'd need to move
    in or out of each bucket to hit the target."""

    def __init__(
        self,
        db: Database,
        targets: TargetAllocationRepository,
        exposure: ExposureService,
    ):
        self.db = db
        self.targets = targets
        self.exposure = exposure

    def report(
        self,
        dimension: str,
        report_currency: str,
        snapshot_id: str | None = None,
        kinds: list[str] | None = None,
    ) -> DriftReport:
        if dimension not in VALID_DIMENSIONS:
            raise ValueError(f"unknown dimension {dimension!r}")
        snap_id = snapshot_id or self._latest_snapshot_id()
        if not snap_id:
            return DriftReport(dimension=dimension, report_currency=report_currency.upper(),
                               snapshot_id="", total_value=0.0, rows=[], targets_sum=0.0, untargeted_share=1.0)

        # 'asset_class' isn't meaningful for liabilities; default kinds appropriately
        if kinds is None:
            kinds = ["asset"] if dimension == "asset_class" else ["asset", "cash"]

        current = self.exposure.by_dimension(dimension, report_currency, snap_id, kinds)
        targets = {t.bucket: t.target_weight for t in self.targets.list_by_dimension(dimension)}

        total = sum(r["value"] for r in current) or 0.0
        targets_sum = sum(targets.values())

        # Build the union of buckets (current U targets)
        rows_map: dict[str, DriftRow] = {}
        for r in current:
            b = r["bucket"] or "(none)"
            t_w = targets.get(b)
            t_val = (t_w * total) if t_w is not None else None
            rows_map[b] = DriftRow(
                bucket=b,
                target_weight=t_w,
                current_value=r["value"],
                current_weight=r["share"],
                target_value=t_val,
                delta_value=(r["value"] - t_val) if t_val is not None else 0.0,
                delta_weight=(r["share"] - t_w) if t_w is not None else 0.0,
            )
        for b, t_w in targets.items():
            if b in rows_map:
                continue
            t_val = t_w * total
            rows_map[b] = DriftRow(
                bucket=b,
                target_weight=t_w,
                current_value=0.0,
                current_weight=0.0,
                target_value=t_val,
                delta_value=-t_val,
                delta_weight=-t_w,
            )

        rows = sorted(rows_map.values(), key=lambda r: abs(r.delta_value), reverse=True)
        return DriftReport(
            dimension=dimension,
            report_currency=report_currency.upper(),
            snapshot_id=snap_id,
            total_value=total,
            rows=rows,
            targets_sum=targets_sum,
            untargeted_share=max(0.0, 1.0 - targets_sum),
        )

    def _latest_snapshot_id(self) -> str | None:
        row = self.db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
        return row[0] if row else None
