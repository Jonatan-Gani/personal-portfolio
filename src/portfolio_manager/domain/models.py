from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Optional
from uuid import uuid4

from pydantic import BaseModel, BeforeValidator, Field

from .._clock import utcnow
from .enums import AssetClass, InstrumentType, LiabilityType, PositionKind, TransactionType


def _new_id() -> str:
    return str(uuid4())


def _norm_iso(v: object) -> object:
    if isinstance(v, str):
        return v.strip().upper() or None  # empty -> None
    return v


CurrencyStr = Annotated[str, BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v)]
OptCountryStr = Annotated[Optional[str], BeforeValidator(_norm_iso)]


class Asset(BaseModel):
    """Asset metadata. Holdings (quantity, avg cost) are derived from transactions."""
    asset_id: str = Field(default_factory=_new_id)
    symbol: Optional[str] = None
    isin: Optional[str] = None
    name: str
    instrument_type: InstrumentType
    asset_class: AssetClass
    currency: CurrencyStr
    country: OptCountryStr = None
    sector: Optional[str] = None
    price_provider: Optional[str] = None
    account_id: Optional[str] = None
    # Indices this asset's return is measured against, for the currency / market
    # / sector / pick split. Auto-defaulted on creation; overridable.
    market_index_symbol: Optional[str] = None
    sector_index_symbol: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class Liability(BaseModel):
    """Liability metadata. Outstanding principal is derived from transactions
    (opening_balance, principal_change, repayment) — never stored on the row."""
    liability_id: str = Field(default_factory=_new_id)
    name: str
    liability_type: LiabilityType
    currency: CurrencyStr
    interest_rate: Optional[float] = None  # APR as decimal (0.045 = 4.5%)
    account_id: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class CashHolding(BaseModel):
    """Cash account metadata. Balance is derived from transactions."""
    cash_id: str = Field(default_factory=_new_id)
    account_name: str
    currency: CurrencyStr
    country: OptCountryStr = None
    account_id: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class AccountGroup(BaseModel):
    """A grouping of accounts — e.g. "Household", "Retirement", "Strategy A"."""
    group_id: str = Field(default_factory=_new_id)
    name: str
    kind: str = "household"  # household | person | institution | strategy | other
    color: Optional[str] = None
    notes: Optional[str] = None
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class Account(BaseModel):
    """A discrete account at a broker / bank / institution that holds positions."""
    account_id: str = Field(default_factory=_new_id)
    group_id: Optional[str] = None
    name: str
    broker: Optional[str] = None
    account_type: str = "other"  # taxable | ira | roth | k401 | hsa | checking | savings | mortgage | other
    currency: Optional[str] = None
    country: OptCountryStr = None
    notes: Optional[str] = None
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class Transaction(BaseModel):
    transaction_id: str = Field(default_factory=_new_id)
    transaction_date: date
    transaction_type: TransactionType
    entity_kind: PositionKind
    entity_id: str
    quantity: Optional[float] = None
    price: Optional[float] = None
    amount: float
    currency: CurrencyStr
    fees: float = 0.0
    notes: Optional[str] = None
    # FX rate at transaction inception: 1 unit of `currency` in `fx_base_currency`
    # as of `transaction_date`. None if not captured (legacy rows / provider down).
    fx_rate_to_base: Optional[float] = None
    fx_base_currency: Optional[CurrencyStr] = None
    # Market / sector index levels frozen at inception, for the return split.
    market_index_level: Optional[float] = None
    sector_index_level: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow)


class SnapshotMeta(BaseModel):
    snapshot_id: str = Field(default_factory=_new_id)
    taken_at: datetime = Field(default_factory=utcnow)
    base_currency: CurrencyStr
    reporting_currencies: list[str]
    total_assets_base: float = 0.0
    total_liabilities_base: float = 0.0
    total_cash_base: float = 0.0
    net_worth_base: float = 0.0
    notes: Optional[str] = None


class SnapshotPosition(BaseModel):
    snapshot_id: str
    position_kind: PositionKind
    entity_id: str
    name: Optional[str] = None
    instrument_type: Optional[str] = None
    asset_class: Optional[str] = None
    currency: CurrencyStr
    country: OptCountryStr = None
    sector: Optional[str] = None
    quantity: Optional[float] = None
    price_local: Optional[float] = None
    value_local: float
    tags: list[str] = Field(default_factory=list)
    values_by_currency: dict[str, float] = Field(default_factory=dict)


class FXRate(BaseModel):
    rate_date: date
    base_currency: CurrencyStr
    quote_currency: CurrencyStr
    rate: float
    provider: str


class Price(BaseModel):
    price_date: date
    symbol: str
    currency: CurrencyStr
    price: float
    provider: str


class Benchmark(BaseModel):
    benchmark_id: str = Field(default_factory=_new_id)
    name: str
    symbol: str
    currency: CurrencyStr
    country: OptCountryStr = None
    price_provider: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True


class ManualPriceOverride(BaseModel):
    """A user-recorded price for an asset on a given date. Used for assets that don't
    have a live-quotable symbol (e.g. real estate, private holdings)."""
    override_id: str = Field(default_factory=_new_id)
    asset_id: str
    observed_at: datetime = Field(default_factory=utcnow)
    price: float
    currency: CurrencyStr
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class TargetAllocation(BaseModel):
    target_id: str = Field(default_factory=_new_id)
    dimension: str
    bucket: str
    target_weight: float
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
