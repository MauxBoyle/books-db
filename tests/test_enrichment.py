"""Tests for interactive, durable per-book enrichment."""

import pytest

from books_db.database import (
    BookInput,
    ReadStatus,
    connect_database,
    create_schema,
    get_book,
    insert_book,
)
from books_db.enrichment import (
    EnrichmentCancelled,
    EnrichmentConfigurationError,
    enrich_books,
)
from books_db.open_library import OpenLibraryCandidate, OpenLibraryLookupError


class Client:
    def __init__(self, results):
        self.results = iter(results)
        self.seen = []

    def search(self, book):
        self.seen.append(book.id)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result

    def complete_candidate(self, candidate):
        return candidate


def answers(*values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def seed(database_path, *books):
    connection = connect_database(database_path)
    create_schema(connection)
    ids = [insert_book(connection, book) for book in books]
    connection.close()
    return ids


def test_each_different_field_requires_explicit_review_and_preserves_other_data(
    tmp_path,
):
    database_path = tmp_path / "library.db"
    [book_id] = seed(
        database_path,
        BookInput(
            title="Dune",
            author="F. Herbert",
            source="Friend",
            status=ReadStatus.READ,
            notes="Keep this",
        ),
    )
    candidate = OpenLibraryCandidate(
        key="/books/OL1M",
        isbn="9780441172719",
        title="Dune Deluxe",
        author="Frank Herbert",
        series="Dune",
        publication_year=1965,
    )

    summary = enrich_books(
        database_path,
        contact_email="reader@example.com",
        input_func=answers("accept", "keep", "accept", "keep", "accept"),
        output_func=lambda _message: None,
        client=Client([[candidate]]),
    )

    connection = connect_database(database_path)
    stored = get_book(connection, book_id)
    connection.close()
    assert (summary.reviewed, summary.updated) == (1, 1)
    assert stored.isbn == "9780441172719"
    assert stored.title == "Dune"
    assert stored.author == "Frank Herbert"
    assert stored.series is None
    assert stored.publication_year == 1965
    assert stored.source == "Friend"
    assert stored.status is ReadStatus.READ
    assert stored.notes == "Keep this"


def test_misses_failures_and_ambiguous_skips_do_not_change_books(tmp_path):
    database_path = tmp_path / "library.db"
    ids = seed(
        database_path,
        BookInput(title="First"),
        BookInput(title="Second"),
        BookInput(title="Third"),
    )
    candidates = [
        OpenLibraryCandidate(key="/works/1", title="Candidate one"),
        OpenLibraryCandidate(key="/works/2", title="Candidate two"),
    ]
    client = Client(
        [
            [],
            OpenLibraryLookupError("timeout"),
            candidates,
        ]
    )

    summary = enrich_books(
        database_path,
        contact_email="reader@example.com",
        input_func=answers("skip"),
        output_func=lambda _message: None,
        client=client,
    )

    assert client.seen == ids
    assert (summary.not_found, summary.failed, summary.skipped) == (1, 1, 1)


def test_cancellation_keeps_completed_books_and_leaves_current_unchanged(tmp_path):
    database_path = tmp_path / "library.db"
    first_id, second_id = seed(
        database_path,
        BookInput(title="First"),
        BookInput(title="Second"),
    )
    client = Client(
        [
            [OpenLibraryCandidate(key="/works/1", title="First improved")],
            [OpenLibraryCandidate(key="/works/2", title="Second improved")],
        ]
    )

    with pytest.raises(EnrichmentCancelled):
        enrich_books(
            database_path,
            contact_email="reader@example.com",
            input_func=answers("accept", "cancel"),
            output_func=lambda _message: None,
            client=client,
        )

    connection = connect_database(database_path)
    assert get_book(connection, first_id).title == "First improved"
    assert get_book(connection, second_id).title == "Second"
    connection.close()


def test_book_ids_limit_post_import_enrichment_scope(tmp_path):
    database_path = tmp_path / "library.db"
    ids = seed(
        database_path,
        BookInput(title="First"),
        BookInput(title="Second"),
    )
    client = Client([[]])

    enrich_books(
        database_path,
        contact_email="reader@example.com",
        book_ids=[ids[1]],
        output_func=lambda _message: None,
        client=client,
    )

    assert client.seen == [ids[1]]


def test_contact_email_has_environment_fallback_and_actionable_error(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("BOOKS_DB_OPEN_LIBRARY_EMAIL", raising=False)
    with pytest.raises(EnrichmentConfigurationError, match="--contact-email"):
        enrich_books(tmp_path / "library.db")
