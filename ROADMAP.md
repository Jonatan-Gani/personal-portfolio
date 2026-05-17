# Roadmap

Potential improvements to grow Portfolio Manager from a personal tracker into a
full production-grade investing app. Grouped by theme; roughly ordered within
each group from "fits the current architecture cleanly" to "larger lift".

Nothing here is committed work — it is a menu of directions.

---

## 1. Onboarding & data entry

- **Example portfolio** — a one-click "load demo data" that seeds a realistic
  multi-currency portfolio (accounts, transactions, a few months of snapshots)
  so a new user immediately sees a populated app instead of empty pages.
- **Position builder** — a guided wizard for standing up an initial portfolio:
  pick accounts, add holdings with current quantity + average cost, and have it
  emit the right `opening_balance` transactions behind the scenes. Lowers the
  barrier for users who don't want to back-enter years of trades.
- **Ticker search / autocomplete** — type "apple", get `AAPL` with the right
  name, currency and exchange pre-filled from a symbol-lookup provider.
- **Broker-specific CSV import templates** — recognise Schwab / Fidelity /
  Trading 212 / IBKR Flex export formats and map them automatically, instead of
  the current generic column mapping.
- **Inline transaction editing** — the update endpoint exists but has no UI;
  add an edit form so corrections don't require delete-and-re-add.
- **Undo** — a short-lived undo for transaction delete/edit.

## 2. Simulation & planning

- **Simulation / paper accounts** — accounts flagged `simulated` whose holdings
  and snapshots are computed exactly like real ones but excluded from headline
  net worth. Lets users model "what if I bought X" without polluting reality.
- **Scenario projection** — project net worth forward under assumed annual
  return, contribution schedule, and inflation.
- **Monte Carlo retirement simulation** — run thousands of return paths to
  estimate the probability of hitting a goal by a target date.
- **Rebalancing simulator** — given target allocations and current drift,
  compute the concrete buy/sell orders needed to get back on target, with a
  tax-impact preview of the sells.
- **Goal tracking** — define goals (down payment, retirement number) and track
  progress against them.

## 3. Analytics & reporting

- **Proper return math** — time-weighted return (TWR) and money-weighted return
  (IRR / XIRR), so performance is correct in the presence of cash flows.
- **Risk metrics** — volatility, Sharpe, Sortino, max drawdown, value-at-risk.
- **Benchmark depth** — multiple benchmarks at once, alpha / beta / tracking
  error, not just a single comparison line.
- **Cost-basis lots** — FIFO / LIFO / specific-lot accounting with realized vs
  unrealized gains broken out per lot.
- **Tax reports** — realized-gains and dividend/interest summaries per tax year,
  exportable.
- **Income projection** — a forward 12-month dividend/interest estimate and a
  dividend calendar.
- **Charts** — net-worth-over-time, allocation donuts, drawdown curve, return
  curves. Currently the data exists but the UI is table-heavy.
- **Correlation / diversification score** — how concentrated the portfolio is.

## 4. Pricing & data quality

- **More providers** — Alpha Vantage, Polygon, Finnhub for equities; CoinGecko
  for crypto; a manual/CSV price feed.
- **IBKR depth** — beyond live prices: import executed trades and dividends via
  the TWS API or Flex Web Service, and reconcile IBKR-reported positions against
  the app's derived holdings (flag mismatches, optionally emit corrections).
- **Historical price backfill** — bulk-fetch past prices so old snapshots and
  returns can be enriched/recomputed.
- **Corporate actions** — auto-ingest splits and dividends rather than entering
  them by hand.
- **Stale-data warnings** — flag any position priced from an old or failed quote
  so snapshot values aren't silently wrong.
- **Provider config in the UI** — make the Settings provider selector actually
  apply without a restart, and add an options editor (e.g. IBKR host/port) so
  switching provider doesn't require editing `config.yaml`.

## 5. Asset coverage

- **Crypto** — on-chain wallet balance import by address.
- **Options & derivatives** — proper contract modeling and valuation.
- **Bonds** — yield, maturity, coupon schedule, accrued interest (an accrual
  service already exists to build on).
- **Real estate / private holdings** — richer manual-valuation tracking with a
  history of appraisals.
- **Linked transactions** — model a buy that atomically consumes cash from a
  specific cash account, instead of two independent rows.

## 6. Multi-user & production hardening

- **Authentication** — login and per-user data isolation; the app is currently
  single-tenant and unauthenticated.
- **Postgres backend option** — DuckDB is a great single-user store but is
  single-writer; a Postgres adapter would enable real concurrency.
- **Audit log** — record who changed what and when.
- **Scheduled + off-site backups** — automate the existing `backup` command and
  push copies somewhere durable.
- **Secrets management** — proper handling of provider API keys and tokens.
- **Read-only sharing** — share a view of a portfolio without edit rights.

## 7. Platform & operations

- **Docker image / docker-compose** — one-command containerised deploy.
- **CI pipeline** — run `ruff`, `pytest`, `mypy` on every push.
- **REST API** — expand `web/routes/api.py` into a documented public API so
  external tools and scripts can read/write.
- **Mobile / PWA** — responsive layout and installable progressive web app.
- **Alerts & notifications** — price targets, allocation-drift thresholds, large
  daily moves.
- **Health-check endpoint & metrics** — for monitored deployments.
- **Open Banking / Plaid** — auto-sync cash-account balances.

---

## Suggested first slice

If picking a handful to do next, these give the most value for the least
architectural risk and play to what already exists:

1. **Example portfolio** — trivial to seed, makes the app demo-able instantly.
2. **Position builder** — directly addresses the biggest onboarding friction.
3. **Inline transaction editing** — the backend is already there.
4. **Charts on the dashboard** — the snapshot data is already structured for it.
5. **TWR / XIRR returns** — turns the Performance page into something accurate.
