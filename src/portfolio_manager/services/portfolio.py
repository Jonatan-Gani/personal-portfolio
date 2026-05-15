from __future__ import annotations

from ..domain.models import Asset, CashHolding, Liability
from ..repositories.assets import AssetRepository
from ..repositories.cash import CashRepository
from ..repositories.liabilities import LiabilityRepository


class PortfolioService:
    def __init__(
        self,
        assets: AssetRepository,
        liabilities: LiabilityRepository,
        cash: CashRepository,
    ):
        self.assets = assets
        self.liabilities = liabilities
        self.cash = cash

    # Assets
    def add_asset(self, asset: Asset) -> Asset:
        return self.assets.upsert(asset)

    def update_asset(self, asset: Asset) -> Asset:
        return self.assets.upsert(asset)

    def remove_asset(self, asset_id: str, hard: bool = False) -> None:
        if hard:
            self.assets.delete(asset_id)
        else:
            self.assets.deactivate(asset_id)

    def list_assets(self, include_inactive: bool = False) -> list[Asset]:
        return self.assets.list_all() if include_inactive else self.assets.list_active()

    # Liabilities
    def add_liability(self, liability: Liability) -> Liability:
        return self.liabilities.upsert(liability)

    def update_liability(self, liability: Liability) -> Liability:
        return self.liabilities.upsert(liability)

    def remove_liability(self, liability_id: str, hard: bool = False) -> None:
        if hard:
            self.liabilities.delete(liability_id)
        else:
            self.liabilities.deactivate(liability_id)

    def list_liabilities(self, include_inactive: bool = False) -> list[Liability]:
        return self.liabilities.list_all() if include_inactive else self.liabilities.list_active()

    # Cash
    def add_cash(self, cash: CashHolding) -> CashHolding:
        return self.cash.upsert(cash)

    def update_cash(self, cash: CashHolding) -> CashHolding:
        return self.cash.upsert(cash)

    def remove_cash(self, cash_id: str, hard: bool = False) -> None:
        if hard:
            self.cash.delete(cash_id)
        else:
            self.cash.deactivate(cash_id)

    def list_cash(self, include_inactive: bool = False) -> list[CashHolding]:
        return self.cash.list_all() if include_inactive else self.cash.list_active()
