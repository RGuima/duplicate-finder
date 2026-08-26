"""Fast recursive directory walk, classifying files as image/video/doc.

Uses an explicit stack with os.scandir (rather than os.walk or a recursive
function) so it can handle very deep trees without hitting the recursion
limit, and scandir avoids a second stat() call per entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v", ".3gp", ".mpg",
    ".mpeg", ".webm",
}
DOC_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    ".rtf", ".odt", ".ods", ".odp", ".csv",
}

CATEGORY_BY_EXT = {}
for _e in IMAGE_EXTS:
    CATEGORY_BY_EXT[_e] = "image"
for _e in VIDEO_EXTS:
    CATEGORY_BY_EXT[_e] = "video"
for _e in DOC_EXTS:
    CATEGORY_BY_EXT[_e] = "doc"


@dataclass
class FileRecord:
    path: str
    dir: str
    name: str
    ext: str
    category: str
    size: int
    mtime: float
    ctime: float


def walk(root: str, cancel_check=None):
    """Yield FileRecord for every media/doc file under root, recursively.

    cancel_check: optional zero-arg callable returning True if the scan
    should stop early.
    """
    stack = [root]
    while stack:
        if cancel_check and cancel_check():
            return
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except (PermissionError, FileNotFoundError, OSError):
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                category = CATEGORY_BY_EXT.get(ext)
                if category is None:
                    continue
                st = entry.stat(follow_symlinks=False)
                yield FileRecord(
                    path=entry.path,
                    dir=current,
                    name=entry.name,
                    ext=ext,
                    category=category,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    ctime=st.st_ctime,
                )
            except (PermissionError, FileNotFoundError, OSError):
                continue
