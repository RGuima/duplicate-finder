from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QAbstractItemView, QLineEdit,
)

ROLE_PATH = Qt.ItemDataRole.UserRole
MATCH_COLOR = QColor("#fff2a8")


class FolderPriorityDialog(QDialog):
    """Lets the user rank the folders involved in this scan's duplicate
    groups from most to least important, and mark any of them as covering
    their subfolders too. Suggest Delete keeps files in the
    highest-ranked folder for each group and suggests the rest for removal.

    Reordering supports multi-selection (click, ctrl/shift-click, or drag a
    selection box), drag-and-drop of one or many rows at once, Move
    Up/Down/to Top/to Bottom buttons that act on the whole selection as a
    block, and matching keyboard shortcuts.
    """

    def __init__(self, folders: list[tuple[str, bool]], parent=None):
        """folders: ordered list of (path, recursive) to pre-populate with,
        highest priority first."""
        super().__init__(parent)
        self.setWindowTitle("Set Folder Priority")
        self.resize(760, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Drag one or more folders to reorder, or select rows and use the "
            "buttons below: folders nearer the top are kept, folders nearer "
            "the bottom are suggested for removal when they contain a "
            "duplicate. Check 'Include subfolders' to also cover any "
            "subfolder of that folder, including ones found in future scans."
        ))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Find:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to highlight and jump to matching folders...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 1)
        layout.addLayout(filter_row)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for path, recursive in folders:
            item = QListWidgetItem()
            item.setData(ROLE_PATH, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if recursive else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        self.list_widget.model().rowsMoved.connect(lambda *a: self._renumber())
        self._renumber()
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(QLabel(
            "(the checkbox on each row toggles 'Include subfolders'; select "
            "multiple rows with Ctrl/Shift-click or a drag box to move them together)"
        ))

        move_row = QHBoxLayout()
        top_btn = QPushButton("Move to Top")
        top_btn.clicked.connect(self._move_to_top)
        up_btn = QPushButton("Move Up")
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        down_btn = QPushButton("Move Down")
        down_btn.clicked.connect(lambda: self._move_selected(1))
        bottom_btn = QPushButton("Move to Bottom")
        bottom_btn.clicked.connect(self._move_to_bottom)
        move_row.addWidget(top_btn)
        move_row.addWidget(up_btn)
        move_row.addWidget(down_btn)
        move_row.addWidget(bottom_btn)
        move_row.addStretch(1)
        layout.addLayout(move_row)

        QShortcut(QKeySequence("Alt+Up"), self, activated=lambda: self._move_selected(-1))
        QShortcut(QKeySequence("Alt+Down"), self, activated=lambda: self._move_selected(1))
        QShortcut(QKeySequence("Ctrl+Home"), self, activated=self._move_to_top)
        QShortcut(QKeySequence("Ctrl+End"), self, activated=self._move_to_bottom)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton("Confirm")
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.list_widget.selectedIndexes()})

    def _reselect(self, items: list[QListWidgetItem]) -> None:
        self.list_widget.clearSelection()
        for item in items:
            item.setSelected(True)
        if items:
            self.list_widget.setCurrentItem(items[0])
            self.list_widget.scrollToItem(items[0])

    def _move_selected(self, delta: int) -> None:
        """Moves every selected row by one position (up if delta<0, down if
        delta>0), preserving the block's relative order. A selected item
        that is already pinned against the top/bottom (or against another
        pinned item ahead of it) is left in place instead of hopping over
        its blocked neighbor, so a whole block anchored at an edge is a
        no-op rather than getting scrambled."""
        rows = self._selected_rows()
        if not rows:
            return
        items = [self.list_widget.item(r) for r in rows]
        if delta < 0:
            limit = -1
            for item in items:  # ascending: topmost selected item first
                row = self.list_widget.row(item)
                target = row - 1
                if target <= limit:
                    limit = row
                    continue
                self.list_widget.takeItem(row)
                self.list_widget.insertItem(target, item)
                limit = target
        else:
            limit = self.list_widget.count()
            for item in reversed(items):  # descending: bottommost selected item first
                row = self.list_widget.row(item)
                target = row + 1
                if target >= limit:
                    limit = row
                    continue
                self.list_widget.takeItem(row)
                self.list_widget.insertItem(target, item)
                limit = target
        self._reselect(items)
        self._renumber()

    def _move_to_top(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        items = [self.list_widget.takeItem(r) for r in reversed(rows)]
        items.reverse()
        for i, item in enumerate(items):
            self.list_widget.insertItem(i, item)
        self._reselect(items)
        self._renumber()

    def _move_to_bottom(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        items = [self.list_widget.takeItem(r) for r in reversed(rows)]
        items.reverse()
        base = self.list_widget.count()
        for i, item in enumerate(items):
            self.list_widget.insertItem(base + i, item)
        self._reselect(items)
        self._renumber()

    def _renumber(self) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setText(f"{i + 1}. {item.data(ROLE_PATH)}")

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        first_match = None
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            matches = bool(text) and text in item.data(ROLE_PATH).lower()
            item.setBackground(MATCH_COLOR if matches else QColor(Qt.GlobalColor.transparent))
            if matches and first_match is None:
                first_match = item
        if first_match is not None:
            self.list_widget.scrollToItem(first_match)

    def ordered_result(self) -> list[tuple[str, bool]]:
        """(path, recursive) tuples, highest priority (rank 0) first."""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = item.data(ROLE_PATH)
            recursive = item.checkState() == Qt.CheckState.Checked
            result.append((path, recursive))
        return result
