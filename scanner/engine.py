"""Orchestrates a scan: walk -> size-prune -> partial hash -> full hash ->
metadata/signature extraction -> group results.

Designed for large libraries (100k+ files):
  * Grouping/pruning is done with SQL GROUP BY on indexed columns instead of
    Python-side dict building.
  * Hashing and metadata extraction only run on files that are actually
    candidates (share a size with at least one other file), and only for
    files whose cached value is missing/invalidated by db.upsert_walked_files.
  * CPU-bound work (hashing, metadata parsing) is spread across a process
    pool so it scales with available cores.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import db as dbmod
from . import hashing, metadata
from .walker import walk

ProgressCB = Callable[[str, int, int], None]

WALK_BATCH = 2000
UPDATE_BATCH = 500


@dataclass
class DuplicateGroup:
    kind: str  # "exact" or "similar"
    category: str
    key: str
    files: list = field(default_factory=list)


def _noop(stage: str, current: int, total: int) -> None:
    pass


def scan(
    root: str,
    db_path: Optional[Path] = None,
    progress_cb: Optional[ProgressCB] = None,
    cancel_event: Optional[threading.Event] = None,
    max_workers: Optional[int] = None,
) -> dict:
    progress_cb = progress_cb or _noop
    cancel_event = cancel_event or threading.Event()
    root = str(Path(root).resolve())
    db_path = db_path or dbmod.default_db_path()
    max_workers = max_workers or min(8, os.cpu_count() or 4)

    conn = dbmod.connect(db_path)

    def cancelled() -> bool:
        return cancel_event.is_set()

    # --- 1. Walk filesystem, bulk upsert into cache ---
    dbmod.mark_stale(conn, root)
    batch: list[tuple] = []
    walked = 0
    for rec in walk(root, cancel_check=cancelled):
        batch.append((rec.path, rec.dir, rec.name, rec.ext, rec.category, rec.size, rec.mtime, rec.ctime))
        walked += 1
        if len(batch) >= WALK_BATCH:
            dbmod.upsert_walked_files(conn, root, batch)
            batch.clear()
            progress_cb("walk", walked, 0)
    if batch:
        dbmod.upsert_walked_files(conn, root, batch)
    progress_cb("walk", walked, walked)

    if cancelled():
        conn.close()
        return {"cancelled": True}

    removed = dbmod.sweep_stale(conn, root)

    # --- 2. Partial hash candidates (same size) ---
    candidates = dbmod.get_partial_hash_candidates(conn, root)
    _run_pool(
        candidates, hashing.partial_hash, max_workers, cancelled,
        lambda rows: dbmod.update_partial_hashes(conn, rows),
        progress_cb, "partial_hash",
    )
    if cancelled():
        conn.close()
        return {"cancelled": True}

    # --- 3. Full hash candidates (same size + partial hash) ---
    candidates = dbmod.get_full_hash_candidates(conn, root)
    _run_pool(
        candidates, hashing.full_hash, max_workers, cancelled,
        lambda rows: dbmod.update_full_hashes(conn, rows),
        progress_cb, "full_hash",
    )
    if cancelled():
        conn.close()
        return {"cancelled": True}

    # --- 4. Metadata / signature extraction for all media & docs ---
    meta_candidates = dbmod.get_metadata_candidates(conn, root)
    _run_metadata_pool(meta_candidates, max_workers, cancelled, conn, progress_cb)
    if cancelled():
        conn.close()
        return {"cancelled": True}

    total_files = dbmod.count_files(conn, root)
    exact_hashes = dbmod.get_exact_group_hashes(conn, root)
    sig_groups = dbmod.get_signature_groups(conn, root)

    conn.close()
    progress_cb("done", 1, 1)
    return {
        "root": root,
        "total_files": total_files,
        "removed": removed,
        "exact_group_count": len(exact_hashes),
        "similar_group_count": len(sig_groups),
        "cancelled": False,
    }


def _adaptive_chunksize(total: int, max_workers: int, cap: int = 64) -> int:
    """Small chunksize for small/large-file workloads so progress streams in
    as each file finishes; larger chunksize for big batches of tiny files so
    IPC overhead doesn't dominate. ProcessPoolExecutor.map only returns a
    chunk's results once the *whole chunk* is done, so a chunksize that's too
    big relative to `total` can make the UI look frozen for the whole stage.
    """
    return max(1, min(cap, total // (max_workers * 4) or 1))


def _run_pool(candidates, worker_fn, max_workers, cancelled, update_fn, progress_cb, stage):
    if not candidates:
        progress_cb(stage, 0, 0)
        return
    paths = [p for p, _size in candidates]
    total = len(paths)
    done = 0
    pending: list = []
    last_emit = time.monotonic()
    chunksize = _adaptive_chunksize(total, max_workers)
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(worker_fn, paths, chunksize=chunksize):
            pending.append(result)
            done += 1
            if len(pending) >= UPDATE_BATCH:
                update_fn(pending)
                pending = []
            now = time.monotonic()
            if done % 50 == 0 or done == total or now - last_emit >= 0.5:
                progress_cb(stage, done, total)
                last_emit = now
            if cancelled():
                break
    if pending:
        update_fn(pending)


def _run_metadata_pool(candidates, max_workers, cancelled, conn, progress_cb):
    if not candidates:
        progress_cb("metadata", 0, 0)
        return
    total = len(candidates)
    done = 0
    pending: list = []
    last_emit = time.monotonic()
    chunksize = _adaptive_chunksize(total, max_workers, cap=32)
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = pool.map(
            metadata.extract, [p for p, _c in candidates], [c for _p, c in candidates], chunksize=chunksize
        )
        for result in futures:
            pending.append(result)
            done += 1
            if len(pending) >= UPDATE_BATCH:
                dbmod.update_metadata(conn, pending)
                pending = []
            now = time.monotonic()
            if done % 50 == 0 or done == total or now - last_emit >= 0.5:
                progress_cb("metadata", done, total)
                last_emit = now
            if cancelled():
                break
    if pending:
        dbmod.update_metadata(conn, pending)


def get_results(root: str, db_path: Optional[Path] = None) -> list[DuplicateGroup]:
    """Load grouped results for display. Called from the GUI after a scan."""
    db_path = db_path or dbmod.default_db_path()
    root = str(Path(root).resolve())
    conn = dbmod.connect(db_path)

    groups: list[DuplicateGroup] = []

    for full_hash in dbmod.get_exact_group_hashes(conn, root):
        rows = dbmod.get_files_by_full_hash(conn, root, full_hash)
        if len(rows) > 1:
            groups.append(DuplicateGroup(kind="exact", category=rows[0]["category"], key=full_hash, files=rows))

    for category, signature in dbmod.get_signature_groups(conn, root):
        rows = dbmod.get_files_by_signature(conn, root, category, signature)
        hashes = {r["full_hash"] for r in rows}
        if len(rows) > 1 and (len(hashes) > 1 or None in hashes):
            groups.append(DuplicateGroup(kind="similar", category=category, key=signature, files=rows))

    conn.close()
    groups.sort(key=lambda g: (-sum(r["size"] for r in g.files), g.kind))
    return groups
