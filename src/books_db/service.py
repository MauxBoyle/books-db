"""Shared transactional import workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from books_db.adapters import parse_import_file
from books_db.database import (
    BookInput,
    BookRecord,
    connect_database,
    create_schema,
    find_duplicates,
    insert_book,
    normalize_value,
    replace_book,
)

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], object]


class ImportCancelled(RuntimeError):
    """The user cancelled an import before its transaction committed."""


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Counts for a successfully committed import."""

    imported: int = 0
    replaced: int = 0
    skipped: int = 0
    affected_ids: tuple[int, ...] = ()

    @property
    def affected_record_ids(self) -> tuple[int, ...]:
        """Return the records eligible for optional post-import enrichment."""

        return self.affected_ids


def _ask(input_func: InputFunction, prompt: str) -> str:
    try:
        return input_func(prompt)
    except (EOFError, KeyboardInterrupt) as error:
        raise ImportCancelled("Import cancelled; no changes were saved.") from error


def _describe_match(position: int, match: BookRecord) -> str:
    details = [
        f"{position}. [id {match.id}] {match.title}",
        f"author: {match.author or '(none)'}",
        f"ISBN: {match.isbn or '(none)'}",
        f"year: {match.publication_year if match.publication_year is not None else '(none)'}",
        f"status: {match.status.value}",
    ]
    return " | ".join(details)


def _choose_duplicate_action(
    book: BookInput,
    matches: list[BookRecord],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[str, int | None]:
    output_func(
        f"Possible duplicate for {book.title!r} by {book.author or '(no author)'}:"
    )
    for position, match in enumerate(matches, start=1):
        output_func(_describe_match(position, match))

    while True:
        choice = normalize_value(
            _ask(
                input_func,
                "[k]eep existing, [i]mport another copy, [r]eplace, or [a]bort: ",
            )
        )
        if choice in {"k", "keep", "keep existing", "s", "skip", "existing"}:
            return "skip", None
        if choice in {
            "i",
            "import",
            "import another",
            "import another copy",
            "b",
            "both",
        }:
            return "insert", None
        if choice in {"a", "abort", "q", "quit"}:
            raise ImportCancelled("Import aborted; no changes were saved.")
        if choice in {"r", "replace"}:
            if len(matches) == 1:
                return "replace", matches[0].id
            return "replace", _select_match(matches, input_func)
        output_func("Enter k, i, r, or a.")


def _select_match(matches: list[BookRecord], input_func: InputFunction) -> int:
    while True:
        raw_choice = normalize_value(
            _ask(
                input_func,
                f"Select the record to replace (1-{len(matches)}) or a to abort: ",
            )
        )
        if raw_choice in {"a", "abort", "q", "quit"}:
            raise ImportCancelled("Import aborted; no changes were saved.")
        try:
            position = int(raw_choice)
        except ValueError:
            continue
        if 1 <= position <= len(matches):
            return matches[position - 1].id


def import_books(
    import_path: str | Path,
    database_path: str | Path = "books.db",
    *,
    input_func: InputFunction | None = None,
    output_func: OutputFunction | None = None,
) -> ImportSummary:
    """Validate and atomically import one personal CSV or Goodreads TSV file."""

    books = parse_import_file(import_path)
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    connection = connect_database(database_path)
    imported = 0
    replaced = 0
    skipped = 0
    affected_ids: list[int] = []

    try:
        connection.execute("BEGIN")
        create_schema(connection)
        for book in books:
            matches = find_duplicates(connection, book.title, book.author)
            if not matches:
                affected_ids.append(insert_book(connection, book))
                imported += 1
                continue

            action, selected_id = _choose_duplicate_action(
                book, matches, input_func, output_func
            )
            if action == "skip":
                skipped += 1
            elif action == "insert":
                affected_ids.append(insert_book(connection, book))
                imported += 1
            else:
                if selected_id is None:
                    raise AssertionError("Replacement requires a selected record")
                replace_book(connection, selected_id, book)
                affected_ids.append(selected_id)
                replaced += 1
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    return ImportSummary(
        imported=imported,
        replaced=replaced,
        skipped=skipped,
        affected_ids=tuple(dict.fromkeys(affected_ids)),
    )
