"""SQLite persistence for the books database."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ReadStatus(StrEnum):
    """The reading states accepted by the application."""

    UNREAD = "unread"
    CURRENTLY_READING = "currently reading"
    READ = "read"
    DID_NOT_FINISH = "did not finish"
    ON_HOLD = "on hold"


@dataclass(frozen=True, slots=True)
class BookInput:
    """A validated book ready to be stored."""

    title: str
    author: str | None = None
    series: str | None = None
    publication_year: int | None = None
    source: str | None = None
    status: ReadStatus = ReadStatus.UNREAD
    notes: str | None = None
    isbn: str | None = None


@dataclass(frozen=True, slots=True)
class BookRecord:
    """A stored book used when displaying duplicate matches."""

    id: int
    title: str
    author: str | None
    series: str | None
    publication_year: int | None
    source: str | None
    status: ReadStatus
    notes: str | None
    isbn: str | None = None


_LOOKUP_TABLES = frozenset({"authors", "series", "sources"})

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS authors (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS series (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        normalized_title TEXT NOT NULL,
        isbn TEXT,
        author_id INTEGER,
        series_id INTEGER,
        publication_year INTEGER,
        source_id INTEGER,
        status TEXT NOT NULL DEFAULT 'unread'
            CHECK (
                status IN (
                    'unread',
                    'currently reading',
                    'read',
                    'did not finish',
                    'on hold'
                )
            ),
        notes TEXT,
        FOREIGN KEY (author_id) REFERENCES authors(id),
        FOREIGN KEY (series_id) REFERENCES series(id),
        FOREIGN KEY (source_id) REFERENCES sources(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS books_duplicate_key
    ON books (normalized_title, author_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS open_library_cache (
        request_url TEXT PRIMARY KEY,
        response_json TEXT NOT NULL,
        fetched_at REAL NOT NULL
    )
    """,
)


def normalize_value(value: str) -> str:
    """Trim, collapse whitespace, and case-fold a value."""

    return " ".join(value.split()).casefold()


def clean_display_value(value: str | None) -> str | None:
    """Return a whitespace-normalized display value, or ``None`` when blank."""

    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def connect_database(path: str | Path) -> sqlite3.Connection:
    """Open a file-backed SQLite connection with foreign keys enabled."""

    connection = sqlite3.connect(Path(path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create all tables and idempotently migrate older databases."""

    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
    book_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(books)")
    }
    if "isbn" not in book_columns:
        connection.execute("ALTER TABLE books ADD COLUMN isbn TEXT")


def _lookup_id(
    connection: sqlite3.Connection, table: str, display_value: str | None
) -> int | None:
    if table not in _LOOKUP_TABLES:
        raise ValueError(f"Unsupported lookup table: {table}")
    cleaned = clean_display_value(display_value)
    if cleaned is None:
        return None

    normalized = normalize_value(cleaned)
    row = connection.execute(
        f"SELECT id FROM {table} WHERE normalized_name = ?", (normalized,)
    ).fetchone()
    if row is not None:
        return int(row["id"])

    cursor = connection.execute(
        f"INSERT INTO {table} (name, normalized_name) VALUES (?, ?)",
        (cleaned, normalized),
    )
    return int(cursor.lastrowid)


def _book_values(
    connection: sqlite3.Connection, book: BookInput
) -> tuple[
    str,
    str,
    str | None,
    int | None,
    int | None,
    int | None,
    int | None,
    str,
    str | None,
]:
    title = clean_display_value(book.title)
    if title is None:
        raise ValueError("Book title cannot be blank")
    notes = book.notes.strip() if book.notes and book.notes.strip() else None
    return (
        title,
        normalize_value(title),
        book.isbn,
        _lookup_id(connection, "authors", book.author),
        _lookup_id(connection, "series", book.series),
        book.publication_year,
        _lookup_id(connection, "sources", book.source),
        book.status.value,
        notes,
    )


def insert_book(connection: sqlite3.Connection, book: BookInput) -> int:
    """Insert a book and return its database identifier."""

    cursor = connection.execute(
        """
        INSERT INTO books (
            title,
            normalized_title,
            isbn,
            author_id,
            series_id,
            publication_year,
            source_id,
            status,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _book_values(connection, book),
    )
    return int(cursor.lastrowid)


def replace_book(connection: sqlite3.Connection, book_id: int, book: BookInput) -> None:
    """Replace the editable values for one stored book."""

    cursor = connection.execute(
        """
        UPDATE books
        SET
            title = ?,
            normalized_title = ?,
            isbn = ?,
            author_id = ?,
            series_id = ?,
            publication_year = ?,
            source_id = ?,
            status = ?,
            notes = ?
        WHERE id = ?
        """,
        (*_book_values(connection, book), book_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"Book {book_id} does not exist")


def find_duplicates(
    connection: sqlite3.Connection, title: str, author: str | None
) -> list[BookRecord]:
    """Find exact normalized title-and-author duplicate candidates."""

    normalized_title = normalize_value(title)
    normalized_author = (
        normalize_value(author) if clean_display_value(author) is not None else None
    )
    rows = connection.execute(
        """
        SELECT
            books.id,
            books.title,
            books.isbn,
            authors.name AS author,
            series.name AS series,
            books.publication_year,
            sources.name AS source,
            books.status,
            books.notes
        FROM books
        LEFT JOIN authors ON authors.id = books.author_id
        LEFT JOIN series ON series.id = books.series_id
        LEFT JOIN sources ON sources.id = books.source_id
        WHERE
            books.normalized_title = ?
            AND (
                (? IS NULL AND books.author_id IS NULL)
                OR authors.normalized_name = ?
            )
        ORDER BY books.id
        """,
        (normalized_title, normalized_author, normalized_author),
    ).fetchall()
    return [
        BookRecord(
            id=int(row["id"]),
            title=str(row["title"]),
            author=row["author"],
            series=row["series"],
            publication_year=row["publication_year"],
            source=row["source"],
            status=ReadStatus(row["status"]),
            notes=row["notes"],
            isbn=row["isbn"],
        )
        for row in rows
    ]


def get_book(connection: sqlite3.Connection, book_id: int) -> BookRecord:
    """Return one stored book."""

    rows = _select_books(connection, "WHERE books.id = ?", (book_id,))
    if not rows:
        raise LookupError(f"Book {book_id} does not exist")
    return rows[0]


def list_books(
    connection: sqlite3.Connection, book_ids: Sequence[int] | None = None
) -> list[BookRecord]:
    """Return books in ID order, optionally limited to specific identifiers."""

    if book_ids is None:
        return _select_books(connection)
    unique_ids = tuple(dict.fromkeys(book_ids))
    if not unique_ids:
        return []
    placeholders = ", ".join("?" for _ in unique_ids)
    return _select_books(
        connection,
        f"WHERE books.id IN ({placeholders})",
        unique_ids,
    )


def _select_books(
    connection: sqlite3.Connection,
    where_clause: str = "",
    parameters: tuple[object, ...] = (),
) -> list[BookRecord]:
    rows = connection.execute(
        f"""
        SELECT
            books.id,
            books.title,
            books.isbn,
            authors.name AS author,
            series.name AS series,
            books.publication_year,
            sources.name AS source,
            books.status,
            books.notes
        FROM books
        LEFT JOIN authors ON authors.id = books.author_id
        LEFT JOIN series ON series.id = books.series_id
        LEFT JOIN sources ON sources.id = books.source_id
        {where_clause}
        ORDER BY books.id
        """,
        parameters,
    ).fetchall()
    return [
        BookRecord(
            id=int(row["id"]),
            title=str(row["title"]),
            author=row["author"],
            series=row["series"],
            publication_year=row["publication_year"],
            source=row["source"],
            status=ReadStatus(row["status"]),
            notes=row["notes"],
            isbn=row["isbn"],
        )
        for row in rows
    ]


def update_book_metadata(
    connection: sqlite3.Connection,
    book_id: int,
    *,
    isbn: str | None,
    title: str,
    author: str | None,
    series: str | None,
    publication_year: int | None,
) -> None:
    """Update only metadata fields controlled by enrichment."""

    cleaned_title = clean_display_value(title)
    if cleaned_title is None:
        raise ValueError("Book title cannot be blank")
    cursor = connection.execute(
        """
        UPDATE books
        SET
            isbn = ?,
            title = ?,
            normalized_title = ?,
            author_id = ?,
            series_id = ?,
            publication_year = ?
        WHERE id = ?
        """,
        (
            isbn,
            cleaned_title,
            normalize_value(cleaned_title),
            _lookup_id(connection, "authors", author),
            _lookup_id(connection, "series", series),
            publication_year,
            book_id,
        ),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"Book {book_id} does not exist")
