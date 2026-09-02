from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QAbstractItemView,
)

ROLE_PATH = Qt.ItemDataRole.UserRole


class FolderPriorityDialog(QDialog):
    """Lets the user rank the folders involved in this scan's duplicate
    groups from most to least important, and mark any of them as covering
    their subfolders too. Suggest Delete keeps files in the
    highest-ranked folder for each group and suggests the rest for removal.
    """

    def __init__(self, folders: list[tuple[str, bool]], parent=None):
        """folders: ordered list of (path, recursive) to pre-populate with,
        highest priority first."""
        super().__init__(parent)
        self.setWindowTitle("Set Folder Priority")
        self.resize(700, 450)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Drag to reorder: folders nearer the top are kept; folders "
            "nearer the bottom are suggested for removal when they contain "
            "a duplicate. Check 'Include subfolders' to also cover any "
            "subfolder of that folder, including ones found in future scans."
        ))

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for path, recursive in folders:
            item = QListWidgetItem(path)
            item.setData(ROLE_PATH, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if recursive else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(QLabel("(the checkbox on each row toggles 'Include subfolders')"))

        move_row = QHBoxLayout()
        up_btn = QPushButton("Move Up")
        up_btn.clicked.connect(self._move_up)
        down_btn = QPushButton("Move Down")
        down_btn.clicked.connect(self._move_down)
        move_row.addWidget(up_btn)
        move_row.addWidget(down_btn)
        move_row.addStretch(1)
        layout.addLayout(move_row)

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

    def _move_up(self):
        row = self.list_widget.currentRow()
        if row > 0:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row - 1, item)
            self.list_widget.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list_widget.currentRow()
        if 0 <= row < self.list_widget.count() - 1:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + 1, item)
            self.list_widget.setCurrentRow(row + 1)

    def ordered_result(self) -> list[tuple[str, bool]]:
        """(path, recursive) tuples, highest priority (rank 0) first."""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = item.data(ROLE_PATH)
            recursive = item.checkState() == Qt.CheckState.Checked
            result.append((path, recursive))
        return result
