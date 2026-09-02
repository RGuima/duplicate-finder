from __future__ import annotations

import os
import re
import subprocess
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QFileDialog, QProgressBar, QLabel, QTreeWidget, QTreeWidgetItem,
    QMenu, QMessageBox, QDialog, QAbstractItemView,
)
from send2trash import send2trash

from scanner import engine
from scanner import db as dbmod
from .detail_dialog import DetailDialog, _fmt_size, _fmt_time
from .priority_dialog import FolderPriorityDialog

DELETED_COLOR = QColor("#c0392b")
SUGGESTED_COLOR = QColor("#e67e22")
NO_BACKGROUND = QBrush(Qt.BrushStyle.NoBrush)

ROLE_ROW = Qt.ItemDataRole.UserRole


def _natural_sort_key(name: str):
    """Case-insensitive key that orders embedded digit runs numerically, so
    e.g. 'file9' sorts before 'file10' the way a person would read them."""
    return [
        int(tok) if tok.isdigit() else tok.casefold()
        for tok in re.split(r"(\d+)", name)
    ]


def _resolve_rank(folder: str, rules: list[tuple[str, bool, int]]) -> float:
    """The priority rank for a folder: an exact rule always wins; otherwise
    the most specific ancestor folder marked 'include subfolders' applies;
    otherwise the folder is unranked (worst priority)."""
    norm_folder = os.path.normcase(os.path.normpath(folder))
    best_rank = None
    best_specificity = -1
    for path, recursive, rank in rules:
        norm_rule = os.path.normcase(os.path.normpath(path))
        if norm_folder == norm_rule:
            return rank
        if recursive and norm_folder.startswith(norm_rule + os.sep):
            specificity = len(norm_rule)
            if specificity > best_specificity:
                best_specificity = specificity
                best_rank = rank
    return best_rank if best_rank is not None else float("inf")


class ScanWorker(QThread):
    progress = Signal(str, int, int)
    finished_scan = Signal(dict)

    def __init__(self, root: str, cancel_event: threading.Event):
        super().__init__()
        self.root = root
        self.cancel_event = cancel_event

    def run(self):
        try:
            result = engine.scan(
                self.root,
                progress_cb=lambda stage, cur, tot: self.progress.emit(stage, cur, tot),
                cancel_event=self.cancel_event,
            )
        except Exception as e:  # surface errors instead of a silent crash
            result = {"error": str(e)}
        self.finished_scan.emit(result)


STAGE_LABELS = {
    "walk": "Scanning files",
    "partial_hash": "Comparing sizes",
    "full_hash": "Hashing candidates",
    "done": "Done",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Duplicate Finder")
        self.resize(1100, 700)

        self.current_root: str | None = None
        self.cancel_event = threading.Event()
        self.worker: ScanWorker | None = None
        self.suggested_items: list[QTreeWidgetItem] = []
        self.suggestion_armed = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- folder picker row ---
        picker_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose a folder to scan for duplicates...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.choose_folder)
        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.cancel_btn.setEnabled(False)
        picker_row.addWidget(self.path_edit, 1)
        picker_row.addWidget(browse_btn)
        picker_row.addWidget(self.scan_btn)
        picker_row.addWidget(self.cancel_btn)
        layout.addLayout(picker_row)

        # --- progress row ---
        progress_row = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_row.addWidget(self.status_label, 1)
        progress_row.addWidget(self.progress_bar, 2)
        layout.addLayout(progress_row)

        # --- filter row ---
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name or path...")
        self.search_edit.textChanged.connect(self.apply_filter)
        filter_row.addWidget(self.search_edit, 1)
        layout.addLayout(filter_row)

        # --- results tree ---
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name / Group", "Size", "Modified", "Folder"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(3, 320)
        # Single-selection is itself a safeguard: it makes it impossible to
        # even select more than one file at a time, let alone delete them.
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemDoubleClicked.connect(self.show_details_for_item)
        self.tree.itemSelectionChanged.connect(self.update_action_buttons)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.tree, 1)

        # --- bottom actions ---
        bottom_row = QHBoxLayout()
        self.summary_label = QLabel("")
        self.details_btn = QPushButton("Show Details")
        self.details_btn.clicked.connect(self.show_details_for_selection)
        self.open_btn = QPushButton("Open Containing Folder")
        self.open_btn.clicked.connect(self.open_selected_folder)
        self.delete_btn = QPushButton("Delete File...")
        self.delete_btn.clicked.connect(self.delete_selected_file)
        self.suggest_btn = QPushButton("Suggest Delete")
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.clicked.connect(self.on_suggest_delete_clicked)
        bottom_row.addWidget(self.summary_label, 1)
        bottom_row.addWidget(self.details_btn)
        bottom_row.addWidget(self.open_btn)
        bottom_row.addWidget(self.delete_btn)
        bottom_row.addWidget(self.suggest_btn)
        layout.addLayout(bottom_row)
        self.update_action_buttons()

    # ------------------------------------------------------------------
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to scan")
        if folder:
            self.path_edit.setText(folder)

    def start_scan(self):
        root = self.path_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, "Invalid folder", "Please choose a valid folder to scan.")
            return
        self.current_root = root
        self.tree.clear()
        self.suggested_items = []
        self.suggestion_armed = False
        self.suggest_btn.setText("Suggest Delete")
        self.suggest_btn.setEnabled(False)
        self.update_action_buttons()
        self.summary_label.setText("")
        self.cancel_event = threading.Event()
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until we know totals
        self.status_label.setText("Starting scan...")

        self.worker = ScanWorker(root, self.cancel_event)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_scan.connect(self.on_scan_finished)
        self.worker.start()

    def cancel_scan(self):
        self.cancel_event.set()
        self.status_label.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)

    def on_progress(self, stage: str, current: int, total: int):
        label = STAGE_LABELS.get(stage, stage)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self.status_label.setText(f"{label}: {current:,} / {total:,}")
        else:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(f"{label}: {current:,}")

    def on_scan_finished(self, result: dict):
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)

        if result.get("error"):
            self.status_label.setText("Scan failed.")
            QMessageBox.critical(self, "Scan error", result["error"])
            return
        if result.get("cancelled"):
            self.status_label.setText("Scan cancelled.")
            return

        self.status_label.setText(
            f"Scanned {result['total_files']:,} files. "
            f"{result['exact_group_count']} exact-duplicate groups."
        )
        self.populate_results()

    # ------------------------------------------------------------------
    def populate_results(self):
        self.tree.clear()
        groups = engine.get_results(self.current_root)
        total_wasted = 0
        for group in groups:
            files = group.files
            rep_size = files[0]["size"]
            header = f"Exact duplicates — {len(files)} files, {_fmt_size(rep_size)} each"
            total_wasted += rep_size * (len(files) - 1)
            top = QTreeWidgetItem([header, "", "", ""])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for row in files:
                row_dict = dict(row)
                row_dict["deleted"] = False
                child = QTreeWidgetItem([
                    row_dict["name"],
                    _fmt_size(row_dict["size"]),
                    _fmt_time(row_dict["mtime"]),
                    row_dict["dir"],
                ])
                child.setData(0, ROLE_ROW, row_dict)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        self.summary_label.setText(f"Estimated reclaimable space: {_fmt_size(total_wasted)}")
        self.apply_filter()
        self.suggested_items = []
        self.suggestion_armed = False
        self.suggest_btn.setText("Suggest Delete")
        self.suggest_btn.setEnabled(self.tree.topLevelItemCount() > 0)
        self.update_action_buttons()

    def apply_filter(self):
        search = self.search_edit.text().strip().lower()

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_child_visible = False
            for j in range(top.childCount()):
                child = top.child(j)
                row = child.data(0, ROLE_ROW) or {}
                text = f"{row.get('name', '')} {row.get('path', row.get('dir', ''))}".lower()
                visible = not search or search in text
                child.setHidden(not visible)
                any_child_visible = any_child_visible or visible
            top.setHidden(not any_child_visible)

    # ------------------------------------------------------------------
    def _selected_item(self) -> QTreeWidgetItem | None:
        # Selection mode is SingleSelection, but double-check anyway: this
        # method (and everything downstream of it) must never act on more
        # than one item.
        items = self.tree.selectedItems()
        if len(items) != 1:
            return None
        return items[0]

    def _selected_row(self) -> dict | None:
        item = self._selected_item()
        if item is None:
            return None
        data = item.data(0, ROLE_ROW)
        if not data or "path" not in data:
            return None
        return data

    def _live_sibling_count(self, item: QTreeWidgetItem) -> int:
        """How many files in this file's group have not been deleted yet."""
        parent = item.parent()
        if parent is None:
            return 0
        count = 0
        for j in range(parent.childCount()):
            row = parent.child(j).data(0, ROLE_ROW) or {}
            if not row.get("deleted"):
                count += 1
        return count

    def show_details_for_selection(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "No selection", "Select a file (not a group header) first.")
            return
        DetailDialog(row, self).exec()

    def show_details_for_item(self, item: QTreeWidgetItem, _column: int):
        data = item.data(0, ROLE_ROW)
        if data and "path" in data:
            DetailDialog(data, self).exec()

    def open_selected_folder(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "No selection", "Select a file (not a group header) first.")
            return
        path = row["path"]
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", path])
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)])

    def update_action_buttons(self):
        item = self._selected_item()
        row = item.data(0, ROLE_ROW) if item is not None else None
        is_file = bool(row and "path" in row)
        self.details_btn.setEnabled(is_file)
        self.open_btn.setEnabled(is_file)
        self.delete_btn.setEnabled(is_file and self._can_delete(item, row))

    def _can_delete(self, item: QTreeWidgetItem, row: dict) -> bool:
        if row.get("deleted"):
            return False
        # The core safeguard: once a group is down to its last surviving
        # copy, that copy can no longer be deleted from here.
        return self._live_sibling_count(item) > 1

    def delete_selected_file(self):
        item = self._selected_item()
        if item is None:
            QMessageBox.information(self, "No selection", "Select exactly one file (not a group header) first.")
            return
        row = item.data(0, ROLE_ROW)
        if not row or "path" not in row:
            QMessageBox.information(self, "No selection", "Select a file (not a group header) first.")
            return
        if row.get("deleted"):
            QMessageBox.information(self, "Already deleted", "This file was already deleted in this session.")
            return
        if not self._can_delete(item, row):
            QMessageBox.warning(
                self,
                "Cannot delete",
                "This is the last remaining copy in this group, so it can't be deleted here.",
            )
            return

        path = row["path"]
        confirm = QMessageBox.question(
            self,
            "Delete file",
            f"Move this single file to the Recycle Bin?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            send2trash(path)
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", f"Could not delete this file:\n{e}")
            return

        row["deleted"] = True
        item.setData(0, ROLE_ROW, row)
        font = item.font(0)
        font.setStrikeOut(True)
        for col in range(self.tree.columnCount()):
            item.setForeground(col, DELETED_COLOR)
            item.setFont(col, font)
        item.setText(0, f"{row['name']}  (deleted — rescan to refresh)")
        self.update_action_buttons()

    # ------------------------------------------------------------------
    # Suggest Delete: a folder-priority-driven bulk-cleanup action.
    #
    # First press: gather every folder that holds a file in some
    # exact-duplicate group, let the user rank those folders (most
    # important first) in the Set Folder Priority dialog, then compute and
    # highlight (in orange) which file in each group would be removed --
    # no disk changes yet. Second press asks for confirmation and only
    # then deletes exactly those files.
    #
    # Per group: the file(s) in the highest-ranked folder are kept; every
    # file in a lower-ranked folder is suggested for removal. If two or
    # more files tie for the top rank (e.g. they're already in the same
    # folder), only one of them -- the longest name, natural-sort-last on
    # ties -- is added to the suggestion, so exactly one keeper survives.
    # Folders with no ranking at all are treated as tied with each other,
    # which reduces to "pick one file to remove from the group" when no
    # priority has been set.
    def _distinct_live_folders(self) -> list[str]:
        folders = set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            live = [
                top.child(j) for j in range(top.childCount())
                if not (top.child(j).data(0, ROLE_ROW) or {}).get("deleted")
            ]
            if len(live) < 2:
                continue
            for child in live:
                folders.add(child.data(0, ROLE_ROW)["dir"])
        return sorted(folders)

    def _compute_suggestions(self, rules: list[tuple[str, bool, int]]) -> list[QTreeWidgetItem]:
        victims = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            live_children = [
                top.child(j) for j in range(top.childCount())
                if (top.child(j).data(0, ROLE_ROW) or {}).get("path")
                and not (top.child(j).data(0, ROLE_ROW) or {}).get("deleted")
            ]
            if len(live_children) < 2:
                continue
            ranked = [
                (child, _resolve_rank(child.data(0, ROLE_ROW)["dir"], rules))
                for child in live_children
            ]
            top_rank = min(r for _, r in ranked)
            top_tier = [c for c, r in ranked if r == top_rank]
            lower_tier = [c for c, r in ranked if r != top_rank]
            victims.extend(lower_tier)
            if len(top_tier) > 1:
                victim = max(
                    top_tier,
                    key=lambda c: (
                        len(c.data(0, ROLE_ROW)["name"]),
                        _natural_sort_key(c.data(0, ROLE_ROW)["name"]),
                    ),
                )
                victims.append(victim)
        return victims

    def _clear_suggestion_highlight(self):
        for item in self.suggested_items:
            row = item.data(0, ROLE_ROW) or {}
            if row.get("deleted"):
                continue
            for col in range(self.tree.columnCount()):
                item.setBackground(col, NO_BACKGROUND)
        self.suggested_items = []
        self.suggestion_armed = False
        self.suggest_btn.setText("Suggest Delete")

    def on_suggest_delete_clicked(self):
        if not self.suggestion_armed:
            folders = self._distinct_live_folders()
            if not folders:
                QMessageBox.information(self, "Suggest Delete", "No duplicate groups to act on.")
                return

            conn = dbmod.connect(dbmod.default_db_path())
            cached = {
                path: (recursive, rank)
                for path, recursive, rank in dbmod.get_all_folder_priorities(conn)
            }

            def sort_key(folder):
                if folder in cached:
                    return (0, cached[folder][1])
                return (1, folder.casefold())

            prefill = [
                (folder, cached[folder][0] if folder in cached else False)
                for folder in sorted(folders, key=sort_key)
            ]

            dialog = FolderPriorityDialog(prefill, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                conn.close()
                return

            entries = dialog.ordered_result()
            dbmod.save_folder_priorities(
                conn, [(path, recursive, rank) for rank, (path, recursive) in enumerate(entries)]
            )
            rules = dbmod.get_all_folder_priorities(conn)
            conn.close()

            victims = self._compute_suggestions(rules)
            if not victims:
                QMessageBox.information(
                    self,
                    "Suggest Delete",
                    "Nothing to suggest: every group's top-priority folder already holds only one copy.",
                )
                return
            self.suggested_items = victims
            for item in victims:
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, SUGGESTED_COLOR)
            self.suggestion_armed = True
            self.suggest_btn.setText(f"Confirm Delete ({len(victims)})")
            return

        victims = self.suggested_items
        total_size = sum((v.data(0, ROLE_ROW) or {}).get("size", 0) for v in victims)
        preview_paths = [(v.data(0, ROLE_ROW) or {}).get("path", "") for v in victims[:20]]
        preview = "\n".join(preview_paths)
        if len(victims) > 20:
            preview += f"\n... and {len(victims) - 20} more"
        confirm = QMessageBox.question(
            self,
            "Confirm suggested deletions",
            f"Move {len(victims)} file(s) to the Recycle Bin? "
            f"Total size: {_fmt_size(total_size)}\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._clear_suggestion_highlight()
            return

        errors = []
        for item in victims:
            row = item.data(0, ROLE_ROW)
            if not row or row.get("deleted"):
                continue
            try:
                send2trash(row["path"])
            except Exception as e:
                errors.append(f"{row['path']}: {e}")
                continue
            row["deleted"] = True
            item.setData(0, ROLE_ROW, row)
            font = item.font(0)
            font.setStrikeOut(True)
            for col in range(self.tree.columnCount()):
                item.setBackground(col, NO_BACKGROUND)
                item.setForeground(col, DELETED_COLOR)
                item.setFont(col, font)
            item.setText(0, f"{row['name']}  (deleted — rescan to refresh)")

        self.suggested_items = []
        self.suggestion_armed = False
        self.suggest_btn.setText("Suggest Delete")
        self.update_action_buttons()
        if errors:
            QMessageBox.warning(self, "Some deletions failed", "\n".join(errors))

    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, ROLE_ROW)
        if not data or "path" not in data:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        details_action = QAction("Show Details", self)
        details_action.triggered.connect(lambda: DetailDialog(data, self).exec())
        open_action = QAction("Open Containing Folder", self)
        open_action.triggered.connect(self.open_selected_folder)
        delete_action = QAction("Delete File...", self)
        delete_action.triggered.connect(self.delete_selected_file)
        delete_action.setEnabled(self._can_delete(item, data))
        menu.addAction(details_action)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
