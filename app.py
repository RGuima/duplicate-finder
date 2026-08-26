"""Duplicate Finder - entry point.

Scans a folder (recursively) for duplicate and near-duplicate media/document
files. See README.md for details.
"""
import multiprocessing
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
