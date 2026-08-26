"""Content hashing, used as worker functions for a ProcessPoolExecutor.

blake2b is used instead of sha256 because it's noticeably faster on stock
hardware and is available in the stdlib (no extra dependency). A cheap
"partial hash" (first + last 64KB) is computed first to prune same-size
files that clearly differ, before paying for a full-file hash.
"""
from __future__ import annotations

import hashlib

CHUNK = 1024 * 1024
PARTIAL_WINDOW = 64 * 1024


def partial_hash(path: str) -> tuple[str, str | None, str | None]:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            head = f.read(PARTIAL_WINDOW)
            h.update(head)
            f.seek(0, 2)
            size = f.tell()
            if size > PARTIAL_WINDOW:
                f.seek(max(0, size - PARTIAL_WINDOW))
                h.update(f.read(PARTIAL_WINDOW))
        return path, h.hexdigest(), None
    except OSError as e:
        return path, None, str(e)


def full_hash(path: str) -> tuple[str, str | None, str | None]:
    try:
        h = hashlib.blake2b(digest_size=32)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return path, h.hexdigest(), None
    except OSError as e:
        return path, None, str(e)
