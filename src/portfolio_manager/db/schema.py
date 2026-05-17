from __future__ import annotations

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS assets (
            asset_id          VARCHAR PRIMARY KEY,
            symbol            VARCHAR,
            name              VARCHAR NOT NULL,
            instrument_type   VARCHAR NOT NULL,
            asset_class       VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            country           VARCHAR,
            sector            VARCHAR,
            quantity          DOUBLE  NOT NULL DEFAULT 0,
            avg_cost          DOUBLE,
            manual_price      DOUBLE,
            price_provider    VARCHAR,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS liabilities (
            liability_id      VARCHAR PRIMARY KEY,
            name              VARCHAR NOT NULL,
            liability_type    VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            principal         DOUBLE  NOT NULL,
            interest_rate     DOUBLE,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS cash_holdings (
            cash_id           VARCHAR PRIMARY KEY,
            account_name      VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            balance           DOUBLE  NOT NULL,
            country           VARCHAR,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id    VARCHAR PRIMARY KEY,
            transaction_date  DATE NOT NULL,
            transaction_type  VARCHAR NOT NULL,
            entity_kind       VARCHAR NOT NULL,
            entity_id         VARCHAR NOT NULL,
            quantity          DOUBLE,
            price             DOUBLE,
            amount            DOUBLE NOT NULL,
            currency          VARCHAR NOT NULL,
            fees              DOUBLE DEFAULT 0,
            notes             VARCHAR,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id           VARCHAR PRIMARY KEY,
            taken_at              TIMESTAMP NOT NULL,
            base_currency         VARCHAR NOT NULL,
            reporting_currencies  VARCHAR[] NOT NULL,
            total_assets_base     DOUBLE,
            total_liabilities_base DOUBLE,
            total_cash_base       DOUBLE,
            net_worth_base        DOUBLE,
            notes                 VARCHAR
        );

        CREATE TABLE IF NOT EXISTS snapshot_positions (
            snapshot_id       VARCHAR NOT NULL,
            position_kind     VARCHAR NOT NULL,
            entity_id         VARCHAR NOT NULL,
            name              VARCHAR,
            instrument_type   VARCHAR,
            asset_class       VARCHAR,
            currency          VARCHAR NOT NULL,
            country           VARCHAR,
            sector            VARCHAR,
            quantity          DOUBLE,
            price_local       DOUBLE,
            value_local       DOUBLE NOT NULL,
            tags              VARCHAR[],
            PRIMARY KEY (snapshot_id, position_kind, entity_id)
        );

        CREATE TABLE IF NOT EXISTS snapshot_position_values (
            snapshot_id       VARCHAR NOT NULL,
            position_kind     VARCHAR NOT NULL,
            entity_id         VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            value             DOUBLE  NOT NULL,
            fx_rate_from_base DOUBLE  NOT NULL,
            PRIMARY KEY (snapshot_id, position_kind, entity_id, currency)
        );

        CREATE TABLE IF NOT EXISTS fx_rates_cache (
            rate_date         DATE NOT NULL,
            base_currency     VARCHAR NOT NULL,
            quote_currency    VARCHAR NOT NULL,
            rate              DOUBLE NOT NULL,
            provider          VARCHAR NOT NULL,
            fetched_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (rate_date, base_currency, quote_currency, provider)
        );

        CREATE TABLE IF NOT EXISTS price_cache (
            price_date        DATE NOT NULL,
            symbol            VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            price             DOUBLE NOT NULL,
            provider          VARCHAR NOT NULL,
            fetched_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (price_date, symbol, provider)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_taken_at        ON snapshots(taken_at);
        CREATE INDEX IF NOT EXISTS idx_snapshot_positions_snap   ON snapshot_positions(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_snapshot_values_snap      ON snapshot_position_values(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_snapshot_values_currency  ON snapshot_position_values(snapshot_id, currency);
        CREATE INDEX IF NOT EXISTS idx_assets_active             ON assets(is_active);
        CREATE INDEX IF NOT EXISTS idx_liabilities_active        ON liabilities(is_active);
        CREATE INDEX IF NOT EXISTS idx_cash_active               ON cash_holdings(is_active);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS benchmarks (
            benchmark_id      VARCHAR PRIMARY KEY,
            name              VARCHAR NOT NULL,
            symbol            VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            country           VARCHAR,
            price_provider    VARCHAR,
            notes             VARCHAR,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );

        CREATE INDEX IF NOT EXISTS idx_benchmarks_active   ON benchmarks(is_active);
        CREATE INDEX IF NOT EXISTS idx_price_cache_symbol  ON price_cache(symbol, price_date);
        """,
    ),
    (
        3,
        """
        -- Manual / observed prices for assets without a live price-provider symbol
        -- (e.g. real estate). The latest row before a snapshot's taken_at wins.
        CREATE TABLE IF NOT EXISTS manual_price_overrides (
            override_id       VARCHAR PRIMARY KEY,
            asset_id          VARCHAR NOT NULL,
            observed_at       TIMESTAMP NOT NULL,
            price             DOUBLE NOT NULL,
            currency          VARCHAR NOT NULL,
            notes             VARCHAR,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_manual_price_asset
            ON manual_price_overrides(asset_id, observed_at);

        -- Backfill existing assets.quantity → OPENING_BALANCE transactions so we don't
        -- lose data when we drop the column. Transactions are now the source of truth.
        INSERT INTO transactions (
            transaction_id, transaction_date, transaction_type, entity_kind, entity_id,
            quantity, price, amount, currency, fees, notes, created_at
        )
        SELECT
            CAST(uuid() AS VARCHAR),
            CAST(created_at AS DATE),
            'opening_balance',
            'asset',
            asset_id,
            quantity,
            NULL,
            0,
            currency,
            0,
            'auto-migrated from assets.quantity in v3',
            created_at
        FROM assets
        WHERE quantity IS NOT NULL AND quantity != 0;

        -- Backfill existing cash_holdings.balance → OPENING_BALANCE
        INSERT INTO transactions (
            transaction_id, transaction_date, transaction_type, entity_kind, entity_id,
            quantity, price, amount, currency, fees, notes, created_at
        )
        SELECT
            CAST(uuid() AS VARCHAR),
            CAST(created_at AS DATE),
            'opening_balance',
            'cash',
            cash_id,
            NULL,
            NULL,
            balance,
            currency,
            0,
            'auto-migrated from cash_holdings.balance in v3',
            created_at
        FROM cash_holdings
        WHERE balance IS NOT NULL AND balance != 0;

        -- Backfill existing assets.manual_price → manual_price_overrides
        INSERT INTO manual_price_overrides (
            override_id, asset_id, observed_at, price, currency, notes, created_at
        )
        SELECT
            CAST(uuid() AS VARCHAR),
            asset_id,
            created_at,
            manual_price,
            currency,
            'auto-migrated from assets.manual_price in v3',
            CURRENT_TIMESTAMP
        FROM assets
        WHERE manual_price IS NOT NULL;

        -- Drop derived columns. DuckDB's ALTER TABLE DROP COLUMN balks when any
        -- index exists on the table, so we recreate the tables without them.
        DROP INDEX IF EXISTS idx_assets_active;
        DROP INDEX IF EXISTS idx_cash_active;

        CREATE TABLE assets_new (
            asset_id          VARCHAR PRIMARY KEY,
            symbol            VARCHAR,
            name              VARCHAR NOT NULL,
            instrument_type   VARCHAR NOT NULL,
            asset_class       VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            country           VARCHAR,
            sector            VARCHAR,
            price_provider    VARCHAR,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );
        INSERT INTO assets_new (
            asset_id, symbol, name, instrument_type, asset_class, currency, country, sector,
            price_provider, notes, tags, created_at, updated_at, is_active
        )
        SELECT asset_id, symbol, name, instrument_type, asset_class, currency, country, sector,
               price_provider, notes, tags, created_at, updated_at, is_active
          FROM assets;
        DROP TABLE assets;
        ALTER TABLE assets_new RENAME TO assets;

        CREATE TABLE cash_holdings_new (
            cash_id           VARCHAR PRIMARY KEY,
            account_name      VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            country           VARCHAR,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );
        INSERT INTO cash_holdings_new (
            cash_id, account_name, currency, country, notes, tags, created_at, updated_at, is_active
        )
        SELECT cash_id, account_name, currency, country, notes, tags, created_at, updated_at, is_active
          FROM cash_holdings;
        DROP TABLE cash_holdings;
        ALTER TABLE cash_holdings_new RENAME TO cash_holdings;

        CREATE INDEX IF NOT EXISTS idx_assets_active            ON assets(is_active);
        CREATE INDEX IF NOT EXISTS idx_cash_active              ON cash_holdings(is_active);
        CREATE INDEX IF NOT EXISTS idx_transactions_entity      ON transactions(entity_kind, entity_id, transaction_date);
        """,
    ),
    (
        4,
        """
        -- Target allocations: a desired weight for a bucket within a dimension.
        -- Drift = current weight − target weight, valued in base currency.
        CREATE TABLE IF NOT EXISTS target_allocations (
            target_id      VARCHAR PRIMARY KEY,
            dimension      VARCHAR NOT NULL,   -- 'asset_class' | 'currency' | 'position_kind' | 'instrument_type' | 'country' | 'sector'
            bucket         VARCHAR NOT NULL,   -- the value within that dimension, e.g. 'equity', 'USD'
            target_weight  DOUBLE  NOT NULL,   -- 0..1
            notes          VARCHAR,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_target_alloc_unique
            ON target_allocations(dimension, bucket);
        """,
    ),
    (
        5,
        """
        -- Bring liabilities in line with assets/cash: transactions are the source of
        -- truth for outstanding principal. Backfill existing principal as
        -- opening_balance transactions, then drop the column.
        INSERT INTO transactions (
            transaction_id, transaction_date, transaction_type, entity_kind, entity_id,
            quantity, price, amount, currency, fees, notes, created_at
        )
        SELECT
            CAST(uuid() AS VARCHAR),
            CAST(created_at AS DATE),
            'opening_balance',
            'liability',
            liability_id,
            NULL,
            NULL,
            principal,
            currency,
            0,
            'auto-migrated from liabilities.principal in v5',
            created_at
        FROM liabilities
        WHERE principal IS NOT NULL AND principal != 0
          AND NOT EXISTS (
              SELECT 1 FROM transactions t
               WHERE t.entity_kind = 'liability' AND t.entity_id = liabilities.liability_id
                 AND t.transaction_type = 'opening_balance'
          );

        DROP INDEX IF EXISTS idx_liabilities_active;

        CREATE TABLE liabilities_new (
            liability_id      VARCHAR PRIMARY KEY,
            name              VARCHAR NOT NULL,
            liability_type    VARCHAR NOT NULL,
            currency          VARCHAR NOT NULL,
            interest_rate     DOUBLE,
            notes             VARCHAR,
            tags              VARCHAR[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active         BOOLEAN DEFAULT TRUE
        );
        INSERT INTO liabilities_new (
            liability_id, name, liability_type, currency, interest_rate,
            notes, tags, created_at, updated_at, is_active
        )
        SELECT liability_id, name, liability_type, currency, interest_rate,
               notes, tags, created_at, updated_at, is_active
          FROM liabilities;
        DROP TABLE liabilities;
        ALTER TABLE liabilities_new RENAME TO liabilities;
        CREATE INDEX IF NOT EXISTS idx_liabilities_active ON liabilities(is_active);
        """,
    ),
    (
        6,
        """
        -- Account groups (e.g. Household, Retirement, Trading) and accounts
        -- (e.g. an IBKR account, a checking account at Chase). Holdings can be assigned
        -- to an account, and rows with a NULL account_id are "Unassigned".
        CREATE TABLE IF NOT EXISTS account_groups (
            group_id      VARCHAR PRIMARY KEY,
            name          VARCHAR NOT NULL,
            kind          VARCHAR NOT NULL DEFAULT 'household',   -- household | person | institution | strategy | other
            color         VARCHAR,                                 -- optional hex (#RRGGBB) for UI
            notes         VARCHAR,
            sort_order    INTEGER DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active     BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS accounts (
            account_id    VARCHAR PRIMARY KEY,
            group_id      VARCHAR,                                 -- nullable: account need not belong to a group
            name          VARCHAR NOT NULL,
            broker        VARCHAR,                                 -- 'Fidelity', 'IBKR', 'Chase', ...
            account_type  VARCHAR NOT NULL DEFAULT 'other',        -- taxable | ira | roth | k401 | hsa | checking | savings | mortgage | other
            currency      VARCHAR,                                 -- declared currency (informational, holdings keep their own)
            country       VARCHAR,
            notes         VARCHAR,
            sort_order    INTEGER DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active     BOOLEAN DEFAULT TRUE
        );
        CREATE INDEX IF NOT EXISTS idx_accounts_group  ON accounts(group_id);
        CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(is_active);

        ALTER TABLE assets        ADD COLUMN IF NOT EXISTS account_id VARCHAR;
        ALTER TABLE cash_holdings ADD COLUMN IF NOT EXISTS account_id VARCHAR;
        ALTER TABLE liabilities   ADD COLUMN IF NOT EXISTS account_id VARCHAR;

        CREATE INDEX IF NOT EXISTS idx_assets_account  ON assets(account_id);
        CREATE INDEX IF NOT EXISTS idx_cash_account    ON cash_holdings(account_id);
        CREATE INDEX IF NOT EXISTS idx_liab_account    ON liabilities(account_id);

        -- App settings: key/value store for user-editable settings that should not
        -- require a config-file edit + restart. Values are JSON-encoded strings.
        CREATE TABLE IF NOT EXISTS app_settings (
            key           VARCHAR PRIMARY KEY,
            value         VARCHAR NOT NULL,                        -- JSON-encoded
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        7,
        """
        -- ISIN code on assets. ISO 6166. 12 chars, alphanumeric.
        -- Optional. Used together with ticker to uniquely identify a security
        -- and verify it against an external lookup service.
        ALTER TABLE assets ADD COLUMN IF NOT EXISTS isin VARCHAR;
        CREATE INDEX IF NOT EXISTS idx_assets_isin ON assets(isin);
        """,
    ),
    (
        8,
        """
        -- FX rate captured at transaction inception. `fx_rate_to_base` converts
        -- one unit of the transaction's `currency` into `fx_base_currency` as of
        -- `transaction_date`. Pinning the rate here lets cost basis and returns
        -- be computed against the rate that was true when the transaction
        -- happened, rather than today's rate. NULL on rows recorded before this
        -- migration, or when the FX provider was unreachable at insert time.
        ALTER TABLE transactions ADD COLUMN IF NOT EXISTS fx_rate_to_base DOUBLE;
        ALTER TABLE transactions ADD COLUMN IF NOT EXISTS fx_base_currency VARCHAR;
        """,
    ),
]

