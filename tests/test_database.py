"""Persistence tests for the normalized books database."""

import sqlite3

import pytest

from books_db.database import (
    BookInput,
    ReadStatus,
    connect_database,
    create_schema,
    find_duplicates,
    insert_book,
    replace_book,
)


def test_schema_is_file_backed_and_persists_books(tmp_path):
    database_path = tmp_path / "library.db"

    connection = connect_database(database_path)
    with connection:
        create_schema(connection)
        book_id = insert_book(
            connection,
            BookInput(title="A Wizard of Earthsea", author="Ursula K. Le Guin"),
        )
    connection.close()

    reopened = connect_database(database_path)
    row = reopened.execute(
        "SELECT title, normalized_title FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    reopened.close()

    assert row["title"] == "A Wizard of Earthsea"
    assert row["normalized_title"] == "a wizard of earthsea"


def test_schema_enforces_foreign_keys(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    with connection:
        create_schema(connection)

    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO books (title, normalized_title, author_id, status)
            VALUES (?, ?, ?, ?)
            """,
            ("Book", "book", 999, ReadStatus.UNREAD.value),
        )
    connection.close()


def test_lookup_values_are_normalized_and_keep_first_display_spelling(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    with connection:
        create_schema(connection)
        insert_book(
            connection,
            BookInput(
                title="First",
                author="  Octavia   E. Butler ",
                series=" Patternist ",
                source=" Friend ",
            ),
        )
        insert_book(
            connection,
            BookInput(
                title="Second",
                author="octavia e. BUTLER",
                series="patternist",
                source="friend",
            ),
        )

    assert [row["name"] for row in connection.execute("SELECT name FROM authors")] == [
        "Octavia E. Butler"
    ]
    assert [row["name"] for row in connection.execute("SELECT name FROM series")] == [
        "Patternist"
    ]
    assert [row["name"] for row in connection.execute("SELECT name FROM sources")] == [
        "Friend"
    ]
    connection.close()


@pytest.mark.parametrize("status", list(ReadStatus))
def test_all_read_statuses_are_stored(tmp_path, status):
    connection = connect_database(tmp_path / "library.db")
    with connection:
        create_schema(connection)
        insert_book(connection, BookInput(title=status.name, status=status))

    assert connection.execute("SELECT status FROM books").fetchone()[0] == status.value
    connection.close()


def test_optional_values_are_null_and_blank_status_defaults_to_unread(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    with connection:
        create_schema(connection)
        insert_book(connection, BookInput(title="Piranesi"))

    row = connection.execute(
        """
        SELECT author_id, series_id, publication_year, source_id, status, notes
        FROM books
        """
    ).fetchone()
    connection.close()

    assert tuple(row) == (None, None, None, None, ReadStatus.UNREAD.value, None)


def test_existing_database_is_migrated_idempotently_without_data_loss(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    connection.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            author_id INTEGER,
            series_id INTEGER,
            publication_year INTEGER,
            source_id INTEGER,
            status TEXT NOT NULL,
            notes TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO books (title, normalized_title, status)
        VALUES ('Kindred', 'kindred', 'read')
        """
    )

    create_schema(connection)
    create_schema(connection)

    columns = [row["name"] for row in connection.execute("PRAGMA table_info(books)")]
    row = connection.execute("SELECT title, isbn FROM books").fetchone()
    assert columns.count("isbn") == 1
    assert tuple(row) == ("Kindred", None)
    connection.close()


def test_isbn_is_inserted_retrieved_and_replaced_without_uniqueness(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    first_id = insert_book(
        connection,
        BookInput(
            title="Dune",
            author="Frank Herbert",
            isbn="9780441172719",
        ),
    )
    insert_book(
        connection,
        BookInput(
            title="Dune",
            author="Frank Herbert",
            isbn="9780441172719",
        ),
    )

    matches = find_duplicates(connection, "dune", "frank herbert")
    assert [match.isbn for match in matches] == [
        "9780441172719",
        "9780441172719",
    ]

    replace_book(
        connection,
        first_id,
        BookInput(title="Dune", author="Frank Herbert", isbn="0441172717"),
    )
    assert (
        connection.execute(
            "SELECT isbn FROM books WHERE id = ?", (first_id,)
        ).fetchone()[0]
        == "0441172717"
    )
    connection.close()
