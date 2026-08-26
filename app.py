"""Duplicate Finder - entry point.

Scans a folder (recursively) for duplicate and near-duplicate media/document
files. See README.md for details.
"""
import argparse
import multiprocessing
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Duplicate Finder")
    parser.add_argument(
        "--exact-only",
        action="store_true",
        help=(
            "Show exact duplicates only (no similar/near-duplicate matches), "
            "and enable 'Suggest Delete' to bulk-select one file to remove "
            "from every exact-duplicate group that lives entirely in a "
            "single folder."
        ),
    )
    args, _unknown = parser.parse_known_args(argv)
    return args


def main():
    args = parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    window = MainWindow(exact_only=args.exact_only)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
