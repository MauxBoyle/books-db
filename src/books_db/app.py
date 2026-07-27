"""Command-line interface for books-db."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from books_db.adapters import ImportValidationError
from books_db.enrichment import (
    EnrichmentCancelled,
    EnrichmentConfigurationError,
    EnrichmentSummary,
    enrich_books,
)
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
    import_parser.add_argument(
        "--enrich",
        action="store_true",
        help="interactively enrich imported or replaced books after commit",
    )
    import_parser.add_argument(
        "--contact-email",
        help="contact email included in Open Library requests",
    )
    enrich_parser = subparsers.add_parser(
        "enrich",
        help="interactively enrich stored books with Open Library metadata",
    )
    enrich_parser.add_argument(
        "--database",
        type=Path,
        default=Path("books.db"),
        help="SQLite database path (default: ./books.db)",
    )
    enrich_parser.add_argument(
        "--contact-email",
        help="contact email included in Open Library requests",
    )
    return parser


def _print_enrichment_summary(summary: EnrichmentSummary) -> None:
    print(
        "Enrichment complete: "
        f"{summary.reviewed} reviewed, "
        f"{summary.updated} updated, "
        f"{summary.unchanged} unchanged, "
        f"{summary.skipped} skipped/ambiguous, "
        f"{summary.not_found} not found, "
        f"{summary.failed} failed lookups."
    )


def _run_enrichment(
    database: Path,
    contact_email: str | None,
    *,
    book_ids: Sequence[int] | None = None,
) -> int:
    try:
        summary = enrich_books(
            database,
            contact_email=contact_email,
            book_ids=book_ids,
            input_func=input,
            output_func=print,
        )
    except (
        EnrichmentCancelled,
        EnrichmentConfigurationError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(f"Enrichment failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Enrichment failed: cancelled; completed books remain saved.",
            file=sys.stderr,
        )
        return 1
    _print_enrichment_summary(summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""

    arguments = build_parser().parse_args(argv)

    if arguments.command == "enrich":
        return _run_enrichment(arguments.database, arguments.contact_email)

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
    if arguments.enrich:
        return _run_enrichment(
            arguments.database,
            arguments.contact_email,
            book_ids=summary.affected_ids,
        )
    return 0
