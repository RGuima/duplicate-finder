from __future__ import annotations

import os
import subprocess
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QFileDialog, QProgressBar, QLabel, QTreeWidget, QTreeWidgetItem,
    QComboBox, QMenu, QMessageBox, QSplitter, QTreeWidgetItemIterator,
    QAbstractItemView,
)
from send2trash import send2trash

from scanner import engine
from .detail_dialog import DetailDialog, _fmt_size, _fmt_time

DELETED_COLOR = QColor("#c0392b")

ROLE_ROW = Qt.ItemDataRole.UserRole


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
    "metadata": "Reading attributes (EXIF/doc info)",
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
        filter_row.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All duplicates", "Exact duplicates only", "Similar only"])
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        filter_row.addWidget(self.filter_combo)
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
        bottom_row.addWidget(self.summary_label, 1)
        bottom_row.addWidget(self.details_btn)
        bottom_row.addWidget(self.open_btn)
        bottom_row.addWidget(self.delete_btn)
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
            f"{result['exact_group_count']} exact-duplicate groups, "
            f"{result['similar_group_count']} similar groups."
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
            if group.kind == "exact":
                header = f"Exact duplicates — {len(files)} files, {_fmt_size(rep_size)} each"
                total_wasted += rep_size * (len(files) - 1)
            else:
                header = f"Similar ({group.category}) — {len(files)} files"
            top = QTreeWidgetItem([header, "", "", ""])
            top.setData(0, ROLE_ROW, {"kind": group.kind})
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
        self.summary_label.setText(f"Estimated reclaimable space from exact duplicates: {_fmt_size(total_wasted)}")
        self.apply_filter()
        self.update_action_buttons()

    def apply_filter(self):
        mode = self.filter_combo.currentText()
        search = self.search_edit.text().strip().lower()

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            data = top.data(0, ROLE_ROW) or {}
            kind = data.get("kind")
            kind_ok = (
                mode == "All duplicates"
                or (mode == "Exact duplicates only" and kind == "exact")
                or (mode == "Similar only" and kind == "similar")
            )
            any_child_visible = False
            for j in range(top.childCount()):
                child = top.child(j)
                row = child.data(0, ROLE_ROW) or {}
                text = f"{row.get('name', '')} {row.get('path', row.get('dir', ''))}".lower()
                visible = kind_ok and (not search or search in text)
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
