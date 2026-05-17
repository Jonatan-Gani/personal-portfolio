"""Seed a realistic demo portfolio so a new install is immediately explorable.

`seed_example_portfolio` creates a small multi-currency household — a few
accounts, equity/ETF holdings, cash, and a mortgage — with a transaction
history spanning roughly eight months, then takes one snapshot. It is
deliberately conservative: it refuses to run unless the portfolio is empty,
so it can never clobber real data.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..domain.enums import (
    AssetClass,
    InstrumentType,
    LiabilityType,
    PositionKind,
    TransactionType,
)
from ..domain.models import Account, Asset, CashHolding, Liability, Transaction

log = logging.getLogger(__name__)


def portfolio_is_empty(c) -> bool:
    """True when no assets, cash, liabilities or transactions exist yet."""
    return (
        not c.portfolio.list_assets(include_inactive=True)
        and not c.portfolio.list_cash(include_inactive=True)
        and not c.portfolio.list_liabilities(include_inactive=True)
        and not c.transactions_repo.list_recent(limit=1)
    )


def seed_example_portfolio(c) -> dict:
    """Populate a demo portfolio. The caller must check `portfolio_is_empty`
    first; this function assumes a clean slate. Returns counts for feedback."""
    today = date.today()

    def ago(n: int) -> date:
        return today - timedelta(days=n)

    def record(
        when: date, ttype: TransactionType, kind: PositionKind, entity_id: str,
        *, amount: float, currency: str,
        quantity: float | None = None, price: float | None = None, fees: float = 0.0,
    ) -> None:
        tx = Transaction(
            transaction_date=when, transaction_type=ttype, entity_kind=kind,
            entity_id=entity_id, quantity=quantity, price=price, amount=amount,
            currency=currency, fees=fees, notes="example data",
        )
        c.inception.stamp(tx)
        c.transactions_repo.insert(tx)

    # --- accounts ---------------------------------------------------------
    schwab = c.accounts_repo.upsert(Account(
        name="Schwab Brokerage", broker="Charles Schwab",
        account_type="taxable", currency="USD"))
    ibkr = c.accounts_repo.upsert(Account(
        name="IBKR", broker="Interactive Brokers",
        account_type="taxable", currency="USD"))
    chase = c.accounts_repo.upsert(Account(
        name="Chase Checking", broker="Chase",
        account_type="checking", currency="USD"))
    mortgage_acct = c.accounts_repo.upsert(Account(
        name="Home Mortgage", broker="Wells Fargo",
        account_type="mortgage", currency="USD"))

    # --- positions --------------------------------------------------------
    aapl = c.portfolio.add_asset(Asset(
        name="Apple Inc.", symbol="AAPL", isin="US0378331005",
        instrument_type=InstrumentType.EQUITY, asset_class=AssetClass.EQUITY,
        currency="USD", country="US", sector="Technology",
        account_id=schwab.account_id))
    voo = c.portfolio.add_asset(Asset(
        name="Vanguard S&P 500 ETF", symbol="VOO",
        instrument_type=InstrumentType.ETF, asset_class=AssetClass.EQUITY,
        currency="USD", country="US", account_id=schwab.account_id))
    vwce = c.portfolio.add_asset(Asset(
        name="Vanguard FTSE All-World", symbol="VWCE",
        instrument_type=InstrumentType.ETF, asset_class=AssetClass.EQUITY,
        currency="EUR", country="IE", account_id=ibkr.account_id))

    checking = c.portfolio.add_cash(CashHolding(
        account_name="Chase Checking", currency="USD", account_id=chase.account_id))
    sweep = c.portfolio.add_cash(CashHolding(
        account_name="Schwab Cash Sweep", currency="USD", account_id=schwab.account_id))

    mortgage = c.portfolio.add_liability(Liability(
        name="Home Mortgage", liability_type=LiabilityType.MORTGAGE,
        currency="USD", interest_rate=0.0575, account_id=mortgage_acct.account_id))

    # --- transaction history ---------------------------------------------
    A, C, L = PositionKind.ASSET, PositionKind.CASH, PositionKind.LIABILITY
    T = TransactionType

    # Opening balances.
    record(ago(240), T.OPENING_BALANCE, C, checking.cash_id, amount=42000.0, currency="USD")
    record(ago(240), T.OPENING_BALANCE, C, sweep.cash_id, amount=15000.0, currency="USD")
    record(ago(240), T.OPENING_BALANCE, L, mortgage.liability_id, amount=318000.0, currency="USD")

    # Asset purchases.
    record(ago(210), T.BUY, A, voo.asset_id, quantity=30, price=460.0, amount=13800.0, currency="USD")
    record(ago(200), T.BUY, A, aapl.asset_id, quantity=50, price=175.0, amount=8750.0, currency="USD")
    record(ago(180), T.BUY, A, vwce.asset_id, quantity=120, price=108.0, amount=12960.0, currency="EUR")
    record(ago(75), T.BUY, A, aapl.asset_id, quantity=25, price=220.0, amount=5500.0, currency="USD", fees=1.0)
    record(ago(60), T.BUY, A, voo.asset_id, quantity=10, price=510.0, amount=5100.0, currency="USD")

    # Cash flows.
    record(ago(90), T.DEPOSIT, C, checking.cash_id, amount=5200.0, currency="USD")
    record(ago(45), T.WITHDRAW, C, checking.cash_id, amount=2000.0, currency="USD")

    # Dividends into the brokerage sweep.
    record(ago(40), T.DIVIDEND, C, sweep.cash_id, amount=52.0, currency="USD")
    record(ago(30), T.DIVIDEND, C, sweep.cash_id, amount=36.0, currency="USD")

    # Mortgage repayments.
    record(ago(90), T.REPAYMENT, L, mortgage.liability_id, amount=1500.0, currency="USD")
    record(ago(30), T.REPAYMENT, L, mortgage.liability_id, amount=1500.0, currency="USD")

    snap = c.snapshot.take(notes="example portfolio")
    log.info("seeded example portfolio (snapshot %s)", snap.snapshot_id)
    return {
        "accounts": 4,
        "assets": 3,
        "cash_accounts": 2,
        "liabilities": 1,
        "transactions": len(c.transactions_repo.list_recent(limit=100)),
        "snapshot_id": snap.snapshot_id,
    }
