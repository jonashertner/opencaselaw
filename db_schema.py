"""
Canonical SQLite schema for Swiss court decisions.

Shared between build_fts5.py (ingestion), mcp_server.py (search),
and pipeline.py (daily FTS import).
Single source of truth — edit here, all consumers pick it up.
"""

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        court TEXT NOT NULL,
        canton TEXT NOT NULL,
        chamber TEXT,
        branch TEXT,
        proceeding_type TEXT,
        procedural_code TEXT,
        appealed_court_raw TEXT,
        appealed_date TEXT,
        appealed_docket TEXT,
        docket_number TEXT NOT NULL,
        docket_number_2 TEXT,
        decision_date TEXT,
        publication_date TEXT,
        marked_for_publication INTEGER,
        language TEXT NOT NULL,
        title TEXT,
        legal_area TEXT,
        regeste TEXT,
        abstract_de TEXT,
        abstract_fr TEXT,
        abstract_it TEXT,
        full_text TEXT,
        decision_type TEXT,
        outcome TEXT,
        source_url TEXT,
        pdf_url TEXT,
        cited_decisions TEXT,
        scraped_at TEXT,
        source TEXT,
        source_id TEXT,
        source_spider TEXT,
        content_hash TEXT,
        json_data TEXT,
        canonical_key TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_decisions_court ON decisions(court);
    CREATE INDEX IF NOT EXISTS idx_decisions_canton ON decisions(canton);
    CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(decision_date);
    CREATE INDEX IF NOT EXISTS idx_decisions_language ON decisions(language);
    CREATE INDEX IF NOT EXISTS idx_decisions_docket ON decisions(docket_number);
    CREATE INDEX IF NOT EXISTS idx_decisions_chamber ON decisions(chamber);
    CREATE INDEX IF NOT EXISTS idx_decisions_branch ON decisions(branch);
    CREATE INDEX IF NOT EXISTS idx_decisions_proceeding ON decisions(proceeding_type);
    CREATE INDEX IF NOT EXISTS idx_decisions_type ON decisions(decision_type);
    CREATE INDEX IF NOT EXISTS idx_decisions_canonical ON decisions(canonical_key);

    -- Joined-docket aliases for consolidated federal proceedings (issue #41).
    -- A "vereinigte Verfahren" / "causes jointes" decision is stored under only
    -- its lead docket; this table maps every SECONDARY docket printed in the
    -- caption to that lead decision_id so a lookup by any joined docket resolves.
    -- Lookup-only, additive: populated by build_fts5._build_docket_aliases from
    -- the caption. The build skips any alias that already exists as a primary
    -- docket_number, so an alias can never override a real lead decision. A
    -- single alias mapping to >1 canonical id is kept as multiple rows so the
    -- serve side can report the ambiguity instead of guessing.
    CREATE TABLE IF NOT EXISTS decision_docket_aliases (
        court                 TEXT NOT NULL,
        alias_docket          TEXT NOT NULL,
        alias_docket_norm     TEXT NOT NULL,
        canonical_decision_id TEXT NOT NULL,
        extraction_method     TEXT NOT NULL,
        PRIMARY KEY (alias_docket_norm, canonical_decision_id)
    );
    CREATE INDEX IF NOT EXISTS idx_docket_alias_lookup
        ON decision_docket_aliases(alias_docket_norm);
    CREATE INDEX IF NOT EXISTS idx_docket_alias_canonical
        ON decision_docket_aliases(canonical_decision_id);

    -- A decision_id a row used to carry before a re-key (a court moved to a
    -- different identity scheme: BS Gerichte 2026-09-17, case number -> the
    -- court's decision number). Populated at insert time from the shard row's
    -- ``previous_decision_id``; lookup-only, so old ids in dashboard URLs,
    -- attest ledgers and users' notes resolve to exactly the decision they
    -- pointed at. Serve side joins on decisions, so an alias whose target was
    -- deduplicated away simply falls through.
    CREATE TABLE IF NOT EXISTS decision_id_aliases (
        previous_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL,
        source      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_id_alias_decision
        ON decision_id_aliases(decision_id);

    CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(
        decision_id UNINDEXED,
        court,
        canton,
        docket_number,
        language,
        title,
        regeste,
        full_text,
        content=decisions,
        content_rowid=rowid,
        tokenize='unicode61 remove_diacritics 2'
    );

    -- Triggers to keep FTS in sync
    CREATE TRIGGER IF NOT EXISTS decisions_ai AFTER INSERT ON decisions BEGIN
        INSERT INTO decisions_fts(rowid, decision_id, court, canton,
            docket_number, language, title, regeste, full_text)
        VALUES (new.rowid, new.decision_id, new.court, new.canton,
            new.docket_number, new.language, new.title, new.regeste,
            new.full_text);
    END;

    CREATE TRIGGER IF NOT EXISTS decisions_ad AFTER DELETE ON decisions BEGIN
        INSERT INTO decisions_fts(decisions_fts, rowid, decision_id, court,
            canton, docket_number, language, title, regeste, full_text)
        VALUES ('delete', old.rowid, old.decision_id, old.court, old.canton,
            old.docket_number, old.language, old.title, old.regeste,
            old.full_text);
    END;

    -- Reindex FTS5 only when an FTS-indexed/stored column actually changed;
    -- skip content_hash-only / date-only / json_data-only updates so the B5
    -- content-hash repair pass doesn't force ~17k needless FTS delete+reinserts.
    CREATE TRIGGER IF NOT EXISTS decisions_au AFTER UPDATE ON decisions
    WHEN old.decision_id IS NOT new.decision_id
      OR old.court IS NOT new.court OR old.canton IS NOT new.canton
      OR old.docket_number IS NOT new.docket_number
      OR old.language IS NOT new.language OR old.title IS NOT new.title
      OR old.regeste IS NOT new.regeste OR old.full_text IS NOT new.full_text
    BEGIN
        INSERT INTO decisions_fts(decisions_fts, rowid, decision_id, court,
            canton, docket_number, language, title, regeste, full_text)
        VALUES ('delete', old.rowid, old.decision_id, old.court, old.canton,
            old.docket_number, old.language, old.title, old.regeste,
            old.full_text);
        INSERT INTO decisions_fts(rowid, decision_id, court, canton,
            docket_number, language, title, regeste, full_text)
        VALUES (new.rowid, new.decision_id, new.court, new.canton,
            new.docket_number, new.language, new.title, new.regeste,
            new.full_text);
    END;
"""

# Coverage tracking schema (completeness / reconciliation)
COVERAGE_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS coverage_targets (
        source_key TEXT PRIMARY KEY,
        source_name TEXT,
        source_kind TEXT,
        start_date TEXT,
        end_date TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS source_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL,
        snapshot_year INTEGER NOT NULL,
        snapshot_date TEXT NOT NULL,
        expected_count INTEGER NOT NULL,
        expected_ids_json TEXT NOT NULL DEFAULT '[]',
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_source_snapshots_unique
        ON source_snapshots(source_key, snapshot_year, snapshot_date);
    CREATE INDEX IF NOT EXISTS idx_source_snapshots_source_year
        ON source_snapshots(source_key, snapshot_year);

    CREATE TABLE IF NOT EXISTS source_discoveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        source_key TEXT NOT NULL,
        decision_id TEXT,
        docket_number TEXT,
        decision_year INTEGER,
        status TEXT NOT NULL DEFAULT 'discovered',
        stub_json TEXT,
        discovered_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_source_discoveries_run
        ON source_discoveries(run_id);
    CREATE INDEX IF NOT EXISTS idx_source_discoveries_source_year
        ON source_discoveries(source_key, decision_year);
    CREATE INDEX IF NOT EXISTS idx_source_discoveries_decision_id
        ON source_discoveries(decision_id);

    CREATE TABLE IF NOT EXISTS source_fetch_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        source_key TEXT NOT NULL,
        decision_id TEXT,
        docket_number TEXT,
        decision_year INTEGER,
        attempt_no INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL,
        error_type TEXT,
        error_message TEXT,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_source_fetch_attempts_run
        ON source_fetch_attempts(run_id);
    CREATE INDEX IF NOT EXISTS idx_source_fetch_attempts_source_year
        ON source_fetch_attempts(source_key, decision_year);
    CREATE INDEX IF NOT EXISTS idx_source_fetch_attempts_decision_id
        ON source_fetch_attempts(decision_id);

    CREATE TABLE IF NOT EXISTS gap_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL,
        decision_year INTEGER NOT NULL,
        decision_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TEXT NOT NULL DEFAULT (datetime('now')),
        first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_error TEXT,
        resolved_at TEXT,
        resolution TEXT,
        notes TEXT,
        UNIQUE(source_key, decision_year, decision_id)
    );
    CREATE INDEX IF NOT EXISTS idx_gap_queue_status_retry
        ON gap_queue(status, next_retry_at);
    CREATE INDEX IF NOT EXISTS idx_gap_queue_source_year
        ON gap_queue(source_key, decision_year);
"""

# Column order for INSERT statements (must match SCHEMA_SQL table definition)
INSERT_COLUMNS = (
    "decision_id", "court", "canton", "chamber",
    "branch", "proceeding_type", "procedural_code",
    "appealed_court_raw", "appealed_date", "appealed_docket",
    "docket_number",
    "docket_number_2", "decision_date", "publication_date", "marked_for_publication",
    "language", "title",
    "legal_area", "regeste", "abstract_de", "abstract_fr", "abstract_it",
    "full_text", "decision_type",
    "outcome", "source_url", "pdf_url", "cited_decisions",
    "scraped_at", "source", "source_id", "source_spider",
    "content_hash", "json_data", "canonical_key",
)

INSERT_SQL = f"""INSERT INTO decisions
    ({', '.join(INSERT_COLUMNS)})
    VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})"""

INSERT_OR_IGNORE_SQL = f"""INSERT OR IGNORE INTO decisions
    ({', '.join(INSERT_COLUMNS)})
    VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})"""
