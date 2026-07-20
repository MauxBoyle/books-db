"""Command-line interface for books-db."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from books_db.adapters import ImportValidationError
from books_db.service import ImportCancelled, import_books


def build_parser() -> argparse.ArgumentParser:
    """Build the public command-line parser."""

    parser = argparse.ArgumentParser(
        prog="books_db",
        description="Store a normalized to-be-read library in SQLite.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser(
        "import",
        help="import a personal CSV or Goodreads TSV file",
    )
    import_parser.add_argument("path", type=Path, help="path to the import file")
    import_parser.add_argument(
        "--database",
        type=Path,
        default=Path("books.db"),
        help="SQLite database path (default: ./books.db)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""

    arguments = build_parser().parse_args(argv)

    try:
        summary = import_books(
            arguments.path,
            arguments.database,
            input_func=input,
            output_func=print,
        )
    except (ImportValidationError, ImportCancelled, OSError, sqlite3.Error) as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Import failed: cancelled; no changes were saved.", file=sys.stderr)
        return 1

    print(
        "Import complete: "
        f"{summary.imported} imported, "
        f"{summary.replaced} replaced, "
        f"{summary.skipped} skipped."
    )
    return 0
