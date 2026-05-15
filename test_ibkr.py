"""Quick manual check of the IBKR price provider against a running TWS / IB Gateway.

Run on the machine where TWS / IB Gateway is running, with the socket API enabled:
    py -3 test_ibkr.py
    py -3 test_ibkr.py AAPL MSFT VOD:LSE:GBP

Adjust PORT below if you are not on TWS paper (7497).
"""
from __future__ import annotations

import sys

from portfolio_manager.providers.ibkr_price import IBKRPriceProvider

PORT = 7497          # 7497 TWS paper · 7496 TWS live · 4002/4001 IB Gateway
MARKET_DATA = 3      # 1 live · 2 frozen · 3 delayed · 4 delayed-frozen

symbols = sys.argv[1:] or ["AAPL"]
provider = IBKRPriceProvider(port=PORT, market_data_type=MARKET_DATA, timeout_seconds=20)

print(f"Connecting to 127.0.0.1:{PORT} (market_data_type={MARKET_DATA})\n")
for sym in symbols:
    try:
        q = provider.get_price(sym)
        print(f"  {sym:<18} {q.price:>14,.4f} {q.currency}  (as of {q.as_of})")
    except Exception as e:  # noqa: BLE001
        print(f"  {sym:<18} ERROR: {e}")

provider._reset()
print("\nDone.")
