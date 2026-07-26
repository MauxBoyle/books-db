"""Tests for the identified, cached Open Library client."""

import io
import json
import urllib.error

import pytest

from books_db.database import (
    BookInput,
    connect_database,
    create_schema,
    get_book,
    insert_book,
)
from books_db.open_library import OpenLibraryClient, OpenLibraryLookupError


class Response:
    def __init__(self, payload, *, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()


def stored_book(connection, *, isbn=None):
    book_id = insert_book(
        connection,
        BookInput(title="Dune", author="Frank Herbert", isbn=isbn),
    )
    return get_book(connection, book_id)


def test_search_by_isbn_requests_only_needed_fields_and_parses_edition(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    requests = []

    def transport(request, timeout):
        requests.append((request, timeout))
        return Response(
            {
                "docs": [
                    {
                        "key": "/works/OL1W",
                        "title": "Dune",
                        "author_name": ["Frank Herbert"],
                        "first_publish_year": 1965,
                        "editions": {
                            "docs": [
                                {
                                    "key": "/books/OL1M",
                                    "title": "Dune: Deluxe Edition",
                                    "isbn": ["0441172717", "9780441172719"],
                                    "publish_year": [1977],
                                    "series": ["Dune"],
                                }
                            ]
                        },
                    }
                ]
            }
        )

    client = OpenLibraryClient(
        connection,
        "reader@example.com",
        transport=transport,
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )
    candidates = client.search(stored_book(connection, isbn="9780441172719"))

    assert len(candidates) == 1
    assert candidates[0].isbn == "9780441172719"
    assert candidates[0].title == "Dune: Deluxe Edition"
    assert candidates[0].publication_year == 1977
    request, timeout = requests[0]
    assert "isbn=9780441172719" in request.full_url
    assert "limit=5" in request.full_url
    assert "fields=" in request.full_url
    assert request.get_header("User-agent") == (
        "books-db/0.0.1 (contact: reader@example.com)"
    )
    assert timeout == 10.0
    connection.close()


def test_title_author_search_is_cached_for_seven_days(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    calls = []
    now = [100.0]

    def transport(request, _timeout):
        calls.append(request.full_url)
        return Response({"docs": []})

    client = OpenLibraryClient(
        connection,
        "reader@example.com",
        transport=transport,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
    )
    book = stored_book(connection)
    client.search(book)
    client.search(book)
    now[0] += 7 * 24 * 60 * 60 + 1
    client.search(book)

    assert len(calls) == 2
    assert "title=Dune" in calls[0]
    assert "author=Frank+Herbert" in calls[0]
    connection.close()


def test_retry_after_and_server_errors_are_retried_at_most_twice(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    calls = 0
    sleeps = []

    def transport(request, _timeout):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise urllib.error.HTTPError(
                request.full_url,
                429 if calls == 1 else 503,
                "retry",
                {"Retry-After": "2"},
                io.BytesIO(),
            )
        return Response({"docs": []})

    client = OpenLibraryClient(
        connection,
        "reader@example.com",
        transport=transport,
        clock=lambda: 100.0,
        sleep=sleeps.append,
    )
    client.search(stored_book(connection))

    assert calls == 3
    assert sleeps.count(2.0) == 2
    connection.close()


def test_malformed_json_is_a_lookup_failure_and_is_not_cached(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    client = OpenLibraryClient(
        connection,
        "reader@example.com",
        transport=lambda _request, _timeout: Response(b"not json"),
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(OpenLibraryLookupError, match="malformed"):
        client.search(stored_book(connection))

    assert (
        connection.execute("SELECT COUNT(*) FROM open_library_cache").fetchone()[0] == 0
    )
    connection.close()


def test_selected_incomplete_edition_and_work_are_fetched_with_fallbacks(tmp_path):
    connection = connect_database(tmp_path / "library.db")
    create_schema(connection)
    urls = []
    responses = iter(
        [
            Response(
                {
                    "docs": [
                        {
                            "key": "/works/OL1W",
                            "editions": {"docs": [{"key": "/books/OL1M"}]},
                        }
                    ]
                }
            ),
            Response(
                {
                    "title": "Edition title",
                    "isbn_13": ["invalid"],
                    "isbn_10": ["0441172717"],
                    "publish_date": "not a date",
                    "publish_year": 1977,
                }
            ),
            Response(
                {
                    "title": "Work title",
                    "authors": [{"author": {"name": "Frank Herbert"}}],
                    "series": ["Dune"],
                    "first_publish_date": "June 1965",
                }
            ),
        ]
    )

    def transport(request, _timeout):
        urls.append(request.full_url)
        return next(responses)

    client = OpenLibraryClient(
        connection,
        "reader@example.com",
        transport=transport,
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )
    [candidate] = client.search(stored_book(connection))
    completed = client.complete_candidate(candidate)

    assert urls[1:] == [
        "https://openlibrary.org/books/OL1M.json",
        "https://openlibrary.org/works/OL1W.json",
    ]
    assert completed.title == "Edition title"
    assert completed.author == "Frank Herbert"
    assert completed.series == "Dune"
    assert completed.publication_year == 1977
    assert completed.isbn == "0441172717"
    connection.close()
