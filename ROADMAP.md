# Roadmap

Potential improvements to grow Portfolio Manager from a personal tracker into a
full production-grade investing app. Grouped by theme; roughly ordered within
each group from "fits the current architecture cleanly" to "larger lift".

Nothing here is committed work — it is a menu of directions.

---

## Recently shipped

Done and in the codebase — listed so the rest of the roadmap reads against the
current state:

- **Transaction-first entry** — recording a transaction creates its position
  automatically; no more pre-registering an asset.
- **Interactive Brokers price provider** — `ibkr`, backed by a running
  TWS / IB Gateway.
- **Multi-source asset lookup** — OpenFIGI + SEC EDGAR + yfinance merged behind
  `AssetLookupService` for ISIN ⇄ ticker resolution.
- **Transaction-time FX capture** — every transaction pins `fx_rate_to_base`,
  the rate at its inception.
- **`start.py`** — one-command cross-platform setup + run.
- **Example portfolio** — one-click demo-data seed on the Settings page.
- **Lookup-assisted entry** — the transaction form's "Look up" button fills
  name and currency from `AssetLookupService`.
- **Offline dashboard charts** — chart libraries vendored locally by `start.py`,
  with a CDN fallback.
- **Currency-attributed returns** — cost basis in the base currency from pinned
  FX rates, and an unrealized-return split into price effect vs FX effect, shown
  on the Holdings page.
- **Position builder** — a `/position-builder` page for bulk-entering holdings
  you already own.
- **Inline transaction editing** — edit a transaction in place from its row.
- **Cost-basis lots in the UI** — click a Holdings row to drill into its FIFO
  lots and realized sales.
- **Sortino & VaR** — added to the Performance page's risk metrics.
- **`portfolio backfill-fx`** — fills FX rates on transactions recorded before
  FX capture existed.

---

## 1. Onboarding & data entry

- **Broker-specific CSV import templates** — recognise Schwab / Fidelity /
  Trading 212 / IBKR Flex export formats and map them automatically, instead of
  the current generic column mapping.
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

- **Per-reporting-currency cost basis** — currency attribution currently works in
  the base currency. Extend it to other reporting currencies by pinning their
  rates per transaction too, or by chaining historical base→ccy rates.
- **Benchmark depth** — multiple benchmarks at once, alpha / beta / tracking
  error, not just a single comparison line.
- **Tax reports** — realized-gains and dividend/interest summaries per tax year,
  exportable.
- **Income projection** — a forward 12-month dividend/interest estimate and a
  dividend calendar.
- **More chart types** — drawdown curve, return curves, allocation history.
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
- **Provider config in the UI** — the Settings provider dropdown is now
  registry-driven, but selecting a provider needs an app restart to take effect
  and there is no options editor (e.g. IBKR host/port). Make the selection apply
  live and editable.

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
- **Secrets management** — proper handling of provider API keys and tokens
  (OpenFIGI key, IBKR credentials).
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

## Suggested next slice

If picking a handful to do next, these give the most value for the least
architectural risk:

1. **Broker-specific CSV import templates** — recognise common broker exports so
   importing real history is not a manual column-mapping chore.
2. **Tax reports** — realized-gains and dividend/interest summaries per tax year.
3. **Simulation / paper accounts** — model "what if" holdings without polluting
   real net worth.
4. **Provider config in the UI** — make the Settings provider switch apply live
   and add an options editor (IBKR host/port).
5. **Authentication** — the first real step toward a multi-user deployment.
