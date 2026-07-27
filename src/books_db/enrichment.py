"""Interactive, per-field Open Library enrichment workflow."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from books_db.database import (
    BookRecord,
    connect_database,
    create_schema,
    list_books,
    normalize_value,
    update_book_metadata,
)
from books_db.open_library import (
    OpenLibraryCandidate,
    OpenLibraryClient,
    OpenLibraryLookupError,
)

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], object]


class EnrichmentCancelled(RuntimeError):
    """The user cancelled enrichment; completed books remain committed."""


class EnrichmentConfigurationError(ValueError):
    """Required Open Library configuration was not supplied."""


@dataclass(frozen=True, slots=True)
class EnrichmentSummary:
    """Counts from a completed enrichment run."""

    reviewed: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    not_found: int = 0
    failed: int = 0

    @property
    def skipped_ambiguous(self) -> int:
        """Alias describing candidates skipped during ambiguous selection."""

        return self.skipped

    @property
    def failed_lookups(self) -> int:
        """Alias describing lookup failures."""

        return self.failed


def resolve_contact_email(contact_email: str | None = None) -> str:
    """Resolve explicit, environment, then local ``.env`` contact configuration."""

    variable_name = "BOOKS_DB_OPEN_LIBRARY_EMAIL"
    env_file_value = dotenv_values(Path.cwd() / ".env").get(variable_name) or ""
    resolved = (
        (contact_email or "").strip()
        or os.environ.get(variable_name, "").strip()
        or env_file_value.strip()
    )
    if not resolved:
        raise EnrichmentConfigurationError(
            "Open Library enrichment requires --contact-email EMAIL or the "
            "BOOKS_DB_OPEN_LIBRARY_EMAIL setting in the environment or a local "
            ".env file."
        )
    return resolved


def _ask(input_func: InputFunction, prompt: str) -> str:
    try:
        return input_func(prompt)
    except (EOFError, KeyboardInterrupt) as error:
        raise EnrichmentCancelled(
            "Enrichment cancelled; completed books remain saved and the current "
            "book was not changed."
        ) from error


def _candidate_description(position: int, candidate: OpenLibraryCandidate) -> str:
    return " | ".join(
        (
            f"{position}. {candidate.title or '(no title)'}",
            f"author: {candidate.author or '(none)'}",
            "edition year: "
            f"{candidate.publication_year if candidate.publication_year is not None else '(none)'}",
            f"ISBN: {candidate.isbn or '(none)'}",
            f"key: {candidate.key}",
        )
    )


def _choose_candidate(
    book: BookRecord,
    candidates: Sequence[OpenLibraryCandidate],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> OpenLibraryCandidate | None:
    output_func(f"Multiple Open Library matches for {book.title!r}:")
    for position, candidate in enumerate(candidates[:5], start=1):
        output_func(_candidate_description(position, candidate))
    while True:
        choice = normalize_value(
            _ask(
                input_func,
                f"Choose a match (1-{min(5, len(candidates))}), skip, or cancel: ",
            )
        )
        if choice in {"skip", "s"}:
            return None
        if choice in {"cancel", "c", "quit", "q", "abort"}:
            raise EnrichmentCancelled(
                "Enrichment cancelled; completed books remain saved and the current "
                "book was not changed."
            )
        try:
            position = int(choice)
        except ValueError:
            output_func("Enter a candidate number, skip, or cancel.")
            continue
        if 1 <= position <= min(5, len(candidates)):
            return candidates[position - 1]
        output_func("Enter a candidate number, skip, or cancel.")


def _equivalent(stored: object, proposed: object) -> bool:
    if isinstance(stored, str) and isinstance(proposed, str):
        return normalize_value(stored) == normalize_value(proposed)
    return stored == proposed


def _review_field(
    field_name: str,
    stored: object,
    proposed: object,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    output_func(
        f"{field_name}: stored={stored if stored is not None else '(none)'} | "
        f"proposed={proposed if proposed is not None else '(none)'}"
    )
    while True:
        choice = normalize_value(
            _ask(input_func, f"{field_name}: press Enter to accept, or type keep: ")
        )
        if choice in {"", "accept"}:
            return True
        if choice == "keep":
            return False
        if choice in {"cancel", "quit", "q", "abort"}:
            raise EnrichmentCancelled(
                "Enrichment cancelled; completed books remain saved and the current "
                "book was not changed."
            )
        output_func("Press Enter to accept, or type keep.")


def _review_book(
    connection: sqlite3.Connection,
    book: BookRecord,
    candidate: OpenLibraryCandidate,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> bool:
    values: dict[str, object] = {
        "isbn": book.isbn,
        "title": book.title,
        "author": book.author,
        "series": book.series,
        "publication_year": book.publication_year,
    }
    proposals = (
        ("ISBN", "isbn", candidate.isbn),
        ("Title", "title", candidate.title),
        ("Author", "author", candidate.author),
        ("Series", "series", candidate.series),
        ("Year", "publication_year", candidate.publication_year),
    )
    changed = False
    for label, field, proposed in proposals:
        stored = values[field]
        if proposed is None or _equivalent(stored, proposed):
            continue
        if stored is None:
            output_func(f"{label}: stored=(none) | proposed={proposed} | accepted")
            values[field] = proposed
            changed = True
            continue
        if _review_field(label, stored, proposed, input_func, output_func):
            values[field] = proposed
            changed = True

    if not changed:
        return False
    connection.execute("BEGIN")
    try:
        update_book_metadata(
            connection,
            book.id,
            isbn=values["isbn"],  # type: ignore[arg-type]
            title=values["title"],  # type: ignore[arg-type]
            author=values["author"],  # type: ignore[arg-type]
            series=values["series"],  # type: ignore[arg-type]
            publication_year=values["publication_year"],  # type: ignore[arg-type]
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return True


def enrich_books(
    database_path: str | Path = "books.db",
    *,
    contact_email: str | None = None,
    book_ids: Sequence[int] | None = None,
    input_func: InputFunction | None = None,
    output_func: OutputFunction | None = None,
    client: OpenLibraryClient | None = None,
) -> EnrichmentSummary:
    """Interactively enrich all or selected books, committing after each book."""

    email = resolve_contact_email(contact_email)
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    connection = connect_database(database_path)
    reviewed = updated = unchanged = skipped = not_found = failed = 0
    try:
        create_schema(connection)
        books = list_books(
            connection,
            None if book_ids is None else list(book_ids),
        )
        lookup_client = client or OpenLibraryClient(connection, email)
        for book in books:
            output_func(f"Looking up [id {book.id}] {book.title}")
            try:
                candidates = lookup_client.search(book)
            except OpenLibraryLookupError as error:
                output_func(f"Lookup failed for {book.title!r}: {error}")
                failed += 1
                continue
            if not candidates:
                output_func(f"No match found for {book.title!r}; unchanged.")
                not_found += 1
                continue
            if len(candidates) == 1:
                selected = candidates[0]
            else:
                selected = _choose_candidate(book, candidates, input_func, output_func)
                if selected is None:
                    output_func(f"Skipped ambiguous match for {book.title!r}.")
                    skipped += 1
                    continue
            try:
                selected = lookup_client.complete_candidate(selected)
            except OpenLibraryLookupError as error:
                output_func(f"Lookup failed for {book.title!r}: {error}")
                failed += 1
                continue
            reviewed += 1
            if _review_book(
                connection,
                book,
                selected,
                input_func,
                output_func,
            ):
                updated += 1
                output_func(f"Updated {book.title!r}.")
            else:
                unchanged += 1
                output_func(f"Kept {book.title!r} unchanged.")
    finally:
        connection.close()

    return EnrichmentSummary(
        reviewed=reviewed,
        updated=updated,
        unchanged=unchanged,
        skipped=skipped,
        not_found=not_found,
        failed=failed,
    )
