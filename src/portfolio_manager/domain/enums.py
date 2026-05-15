from __future__ import annotations

from enum import Enum


class InstrumentType(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    GOVERNMENT_BOND = "government_bond"
    CORPORATE_BOND = "corporate_bond"
    BOND_FUND = "bond_fund"
    CASH = "cash"
    MONEY_MARKET = "money_market"
    REAL_ESTATE = "real_estate"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    PRIVATE = "private"
    OTHER = "other"


class AssetClass(str, Enum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    CASH = "cash"
    REAL_ASSET = "real_asset"
    ALTERNATIVE = "alternative"
    OTHER = "other"


class LiabilityType(str, Enum):
    MORTGAGE = "mortgage"
    LOAN = "loan"
    CREDIT_CARD = "credit_card"
    MARGIN = "margin"
    OTHER = "other"


class TransactionType(str, Enum):
    OPENING_BALANCE = "opening_balance"
    BUY = "buy"
    SELL = "sell"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    REPAYMENT = "repayment"
    PRINCIPAL_CHANGE = "principal_change"
    SPLIT = "split"  # multiplicative quantity adjustment (e.g. 2-for-1 stock split)


class PositionKind(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    CASH = "cash"
