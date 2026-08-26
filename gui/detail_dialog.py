from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtWidgets import QDialog, QTextEdit, QVBoxLayout, QPushButton, QHBoxLayout


def _fmt_time(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return str(ts)


def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"


class DetailDialog(QDialog):
    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Details — {row.get('name', '')}")
        self.resize(640, 480)

        text = QTextEdit(self)
        text.setReadOnly(True)
        text.setFont(text.font())

        meta = {}
        if row.get("meta_json"):
            try:
                meta = json.loads(row["meta_json"])
            except (TypeError, ValueError):
                meta = {}

        lines = [
            f"Name:          {row.get('name')}",
            f"Folder:        {row.get('dir')}",
            f"Full path:     {row.get('path')}",
            f"Category:      {row.get('category')}",
            f"Size:          {_fmt_size(row.get('size') or 0)}",
            f"Modified:      {_fmt_time(row.get('mtime'))}",
            f"Created (fs):  {_fmt_time(row.get('ctime'))}",
            "",
            f"Full hash:     {row.get('full_hash') or '(not computed)'}",
            f"Partial hash:  {row.get('partial_hash') or '(not computed)'}",
            f"Similarity key:{' ' + row.get('signature') if row.get('signature') else ' (none)'}",
        ]
        if meta:
            lines.append("")
            lines.append("Extracted attributes:")
            for k, v in meta.items():
                lines.append(f"  {k}: {v}")
        if row.get("error"):
            lines.append("")
            lines.append(f"Warning: {row['error']}")

        text.setPlainText("\n".join(lines))

        layout = QVBoxLayout(self)
        layout.addWidget(text)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
