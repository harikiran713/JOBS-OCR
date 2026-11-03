"""Engine/session management and schema migrations for SQLite."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)

# Bump when adding a migration. Fresh databases are created from the current
# models and stamped with this version; existing databases run each migration
# with a version greater than their own, after an automatic backup.
SCHEMA_VERSION = 2

FTS_DDL = [
    # Page text index used by search and procedure discovery (stages 1–2).
    """CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
        text, page_id UNINDEXED, document_id UNINDEXED, project_id UNINDEXED,
        tokenize = 'porter unicode61 remove_diacritics 2')""",
    # Description index; ``kind`` lets search label AI summaries vs source text.
    """CREATE VIRTUAL TABLE IF NOT EXISTS descriptions_fts USING fts5(
        content, description_id UNINDEXED, job_id UNINDEXED, project_id UNINDEXED, kind UNINDEXED,
        tokenize = 'porter unicode61 remove_diacritics 2')""",
]

def _v2_page_layout_meta(conn) -> None:  # noqa: ANN001
    """v2: per-page document structure (document code, page counter, margin labels)."""
    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(pages)"))}
    if "layout_meta" not in cols:
        conn.execute(text("ALTER TABLE pages ADD COLUMN layout_meta JSON NOT NULL DEFAULT '{}'"))


# version -> callable(connection). Add future upgrade steps here; never edit old ones.
MIGRATIONS: dict[int, Callable] = {2: _v2_page_layout_meta}

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def init_engine(db_path: Path, backups_dir: Path | None = None) -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    existed = db_path.exists()
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _set_pragmas)
    _migrate(engine, existed, db_path, backups_dir)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def _migrate(engine: Engine, existed: bool, db_path: Path, backups_dir: Path | None) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
        row = conn.execute(text("SELECT version FROM schema_version")).fetchone()
        current = row[0] if row else 0

    if current == 0:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            for ddl in FTS_DDL:
                conn.execute(text(ddl))
            conn.execute(text("DELETE FROM schema_version"))
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": SCHEMA_VERSION})
        log.info("Database initialised at schema v%s", SCHEMA_VERSION)
        return

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{current} is newer than this application (v{SCHEMA_VERSION}). "
            "Install a newer version of Marine Maintenance Intelligence; your data has not been modified."
        )

    if current < SCHEMA_VERSION:
        if existed and backups_dir is not None:
            backups_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = backups_dir / f"mmi-pre-v{SCHEMA_VERSION}-{stamp}.sqlite3"
            with engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
            shutil.copy2(db_path, dest)
            log.info("Backed up database to %s before migration", dest)
        for version in range(current + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS[version]
            with engine.begin() as conn:
                step(conn)
                conn.execute(text("UPDATE schema_version SET version = :v"), {"v": version})
            log.info("Migrated database to v%s", version)

    # Idempotent: guarantees FTS tables exist even if a previous run crashed mid-way.
    with engine.begin() as conn:
        for ddl in FTS_DDL:
            conn.execute(text(ddl))


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialised")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on any exception."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
