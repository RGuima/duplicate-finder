"""SQLite-backed cache of scanned file records.

Using SQLite (instead of in-memory Python structures) lets us handle 100k+
files without keeping everything in RAM twice, gives us fast set-based
GROUP BY queries for duplicate detection, and lets rescans of an unchanged
folder skip re-hashing entirely.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    dir TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    category TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    ctime REAL,
    partial_hash TEXT,
    full_hash TEXT,
    error TEXT,
    stale INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_root ON files(root);
CREATE INDEX IF NOT EXISTS idx_files_root_size ON files(root, category, size);
CREATE INDEX IF NOT EXISTS idx_files_root_partial ON files(root, size, partial_hash);
CREATE INDEX IF NOT EXISTS idx_files_root_fullhash ON files(root, full_hash);

-- Remembers, per folder, the user's Suggest Delete keep/remove priority so
-- it can be reapplied automatically the next time that folder shows up in a
-- scan. `recursive` extends a folder's rank to every path nested under it,
-- covering subfolders discovered in a later scan that were never explicitly
-- ranked themselves.
CREATE TABLE IF NOT EXISTS folder_priority (
    path TEXT PRIMARY KEY,
    recursive INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL
);
"""


def default_db_path() -> Path:
    import os

    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DuplicateFinder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "cache.db"


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def mark_stale(conn: sqlite3.Connection, root: str) -> None:
    conn.execute("UPDATE files SET stale = 1 WHERE root = ?", (root,))


def sweep_stale(conn: sqlite3.Connection, root: str) -> int:
    cur = conn.execute("DELETE FROM files WHERE root = ? AND stale = 1", (root,))
    conn.commit()
    return cur.rowcount


def upsert_walked_files(conn: sqlite3.Connection, root: str, records: Sequence[tuple]) -> None:
    """records: (path, dir, name, ext, category, size, mtime, ctime)

    Bulk upsert via a temp staging table so the diff/merge is done in one
    set-based SQL statement instead of one Python round trip per file.
    Unchanged files (same size+mtime) keep their cached hash.
    """
    if not records:
        return
    conn.execute("DROP TABLE IF EXISTS temp.scan_seen")
    conn.execute(
        """
        CREATE TEMP TABLE scan_seen (
            path TEXT PRIMARY KEY, dir TEXT, name TEXT, ext TEXT,
            category TEXT, size INTEGER, mtime REAL, ctime REAL
        )
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO scan_seen VALUES (?,?,?,?,?,?,?,?)", records
    )
    # Update rows that already exist (path matches); reuse cached hash/meta
    # only if size+mtime are unchanged. SQLite's UPDATE...FROM (3.33+) lets
    # us do this as one set-based statement instead of a Python loop.
    conn.execute(
        """
        UPDATE files SET
            root = :root,
            dir = scan_seen.dir,
            name = scan_seen.name,
            ext = scan_seen.ext,
            category = scan_seen.category,
            size = scan_seen.size,
            mtime = scan_seen.mtime,
            ctime = scan_seen.ctime,
            stale = 0,
            partial_hash = CASE WHEN files.size = scan_seen.size AND files.mtime = scan_seen.mtime
                                 THEN files.partial_hash ELSE NULL END,
            full_hash = CASE WHEN files.size = scan_seen.size AND files.mtime = scan_seen.mtime
                              THEN files.full_hash ELSE NULL END,
            error = CASE WHEN files.size = scan_seen.size AND files.mtime = scan_seen.mtime
                         THEN files.error ELSE NULL END
        FROM scan_seen
        WHERE files.path = scan_seen.path
        """,
        {"root": root},
    )
    # Insert rows that are brand new.
    conn.execute(
        """
        INSERT INTO files (path, root, dir, name, ext, category, size, mtime, ctime, stale)
        SELECT path, :root, dir, name, ext, category, size, mtime, ctime, 0
        FROM scan_seen
        WHERE path NOT IN (SELECT path FROM files)
        """,
        {"root": root},
    )
    conn.execute("DROP TABLE scan_seen")
    conn.commit()


def get_partial_hash_candidates(conn: sqlite3.Connection, root: str) -> list[tuple[str, int]]:
    cur = conn.execute(
        """
        SELECT path, size FROM files
        WHERE root = ? AND partial_hash IS NULL
          AND size IN (
              SELECT size FROM files WHERE root = ? GROUP BY size HAVING COUNT(*) > 1
          )
        """,
        (root, root),
    )
    return cur.fetchall()


def update_partial_hashes(conn: sqlite3.Connection, rows: Iterable[tuple[str, str | None, str | None]]) -> None:
    conn.executemany(
        "UPDATE files SET partial_hash = ?, error = COALESCE(?, error) WHERE path = ?",
        [(h, err, p) for p, h, err in rows],
    )
    conn.commit()


def get_full_hash_candidates(conn: sqlite3.Connection, root: str) -> list[tuple[str, int]]:
    cur = conn.execute(
        """
        SELECT path, size FROM files
        WHERE root = ? AND full_hash IS NULL AND partial_hash IS NOT NULL
          AND (size, partial_hash) IN (
              SELECT size, partial_hash FROM files
              WHERE root = ? AND partial_hash IS NOT NULL
              GROUP BY size, partial_hash HAVING COUNT(*) > 1
          )
        """,
        (root, root),
    )
    return cur.fetchall()


def update_full_hashes(conn: sqlite3.Connection, rows: Iterable[tuple[str, str | None, str | None]]) -> None:
    conn.executemany(
        "UPDATE files SET full_hash = ?, error = COALESCE(?, error) WHERE path = ?",
        [(h, err, p) for p, h, err in rows],
    )
    conn.commit()


def get_exact_group_hashes(conn: sqlite3.Connection, root: str) -> list[str]:
    cur = conn.execute(
        """
        SELECT full_hash FROM files
        WHERE root = ? AND full_hash IS NOT NULL
        GROUP BY full_hash HAVING COUNT(*) > 1
        """,
        (root,),
    )
    return [r[0] for r in cur.fetchall()]


def get_files_by_full_hash(conn: sqlite3.Connection, root: str, full_hash: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM files WHERE root = ? AND full_hash = ? ORDER BY path", (root, full_hash)
    )
    return cur.fetchall()


def get_file(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM files WHERE path = ?", (path,))
    return cur.fetchone()


def count_files(conn: sqlite3.Connection, root: str) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM files WHERE root = ?", (root,))
    return cur.fetchone()[0]


def get_all_folder_priorities(conn: sqlite3.Connection) -> list[tuple[str, bool, int]]:
    """All cached folder-priority rules, as (path, recursive, rank)."""
    cur = conn.execute("SELECT path, recursive, rank FROM folder_priority")
    return [(path, bool(recursive), rank) for path, recursive, rank in cur.fetchall()]


def save_folder_priorities(conn: sqlite3.Connection, entries: Sequence[tuple[str, bool, int]]) -> None:
    """Upserts (path, recursive, rank) rules. Only touches the given paths --
    priorities cached for other folder trees are left alone."""
    conn.executemany(
        """
        INSERT INTO folder_priority (path, recursive, rank) VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET recursive = excluded.recursive, rank = excluded.rank
        """,
        [(path, int(recursive), rank) for path, recursive, rank in entries],
    )
    conn.commit()
