"""Small, cache-aware client for the official Open Library JSON APIs."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from typing import Any

from books_db.adapters import normalize_isbn
from books_db.database import BookRecord

Transport = Callable[[urllib.request.Request, float], Any]

_BASE_URL = "https://openlibrary.org"
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_MINIMUM_REQUEST_INTERVAL = 1 / 3
_SEARCH_FIELDS = (
    "key,title,author_name,first_publish_year,"
    "editions,editions.key,editions.title,editions.isbn,"
    "editions.publish_year,editions.series"
)


class OpenLibraryLookupError(RuntimeError):
    """A recoverable failure while looking up one book."""


@dataclass(frozen=True, slots=True)
class OpenLibraryCandidate:
    """Metadata proposed by one relevance-ranked Open Library result."""

    key: str
    title: str | None = None
    author: str | None = None
    series: str | None = None
    publication_year: int | None = None
    isbn: str | None = None
    work_key: str | None = None
    edition_key: str | None = None
    edition_incomplete: bool = False
    work_incomplete: bool = False


def _first_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            text = _first_text(item)
            if text is not None:
                return text
    return None


def _year(value: object) -> int | None:
    if isinstance(value, int):
        return value if 0 < value < 10000 else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            parsed = _year(item)
            if parsed is not None:
                return parsed
        return None
    text = _first_text(value)
    if text is None:
        return None
    for token in text.replace("/", " ").replace("-", " ").split():
        if len(token) == 4 and token.isdigit():
            parsed = int(token)
            if 0 < parsed < 10000:
                return parsed
    return int(text) if text.isdigit() and 0 < int(text) < 10000 else None


def _preferred_isbn(value: object) -> str | None:
    values: Sequence[object]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = value
    else:
        values = (value,)
    valid: list[str] = []
    for raw_value in values:
        if not isinstance(raw_value, str):
            continue
        try:
            isbn = normalize_isbn(raw_value)
        except ValueError:
            continue
        if isbn:
            valid.append(isbn)
    return next((isbn for isbn in valid if len(isbn) == 13), None) or (
        valid[0] if valid else None
    )


def _edition_isbn(edition: Mapping[str, object]) -> str | None:
    values: list[object] = []
    for field in ("isbn_13", "isbn_10", "isbn"):
        value = edition.get(field)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return _preferred_isbn(values)


def _work_author(work: Mapping[str, object]) -> str | None:
    authors = work.get("authors")
    if not isinstance(authors, Sequence) or isinstance(authors, (str, bytes)):
        return None
    for entry in authors:
        if not isinstance(entry, Mapping):
            continue
        author = entry.get("author", entry)
        if isinstance(author, Mapping):
            name = _first_text(author.get("name"))
            if name:
                return name
    return None


def _key(value: object) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("key")
    return _first_text(value)


def _edition_from_document(document: Mapping[str, object]) -> Mapping[str, object]:
    editions = document.get("editions")
    if isinstance(editions, Mapping):
        docs = editions.get("docs")
        if isinstance(docs, Sequence) and docs and isinstance(docs[0], Mapping):
            return docs[0]
    return {}


def _candidate_from_document(document: Mapping[str, object]) -> OpenLibraryCandidate:
    edition = _edition_from_document(document)
    work_key = _key(document.get("key"))
    edition_key = _key(edition.get("key"))
    edition_title = _first_text(edition.get("title"))
    edition_year = _year(edition.get("publish_year"))
    edition_series = _first_text(edition.get("series"))
    edition_isbn = _edition_isbn(edition)
    work_title = _first_text(document.get("title"))
    work_author = _first_text(document.get("author_name"))
    work_year = _year(document.get("first_publish_year"))
    return OpenLibraryCandidate(
        key=edition_key or work_key or "(unknown)",
        title=edition_title or work_title,
        author=work_author,
        series=edition_series or _first_text(document.get("series")),
        publication_year=edition_year or work_year,
        isbn=edition_isbn or _preferred_isbn(document.get("isbn")),
        work_key=work_key,
        edition_key=edition_key,
        edition_incomplete=not all(
            (edition_title, edition_year, edition_series, edition_isbn)
        ),
        work_incomplete=not all(
            (
                edition_title or work_title,
                work_author,
                edition_series or _first_text(document.get("series")),
                edition_year or work_year,
            )
        ),
    )


class OpenLibraryClient:
    """Perform identified, throttled Open Library requests with SQLite caching."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        contact_email: str,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], object] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not contact_email.strip():
            raise ValueError("Open Library contact email cannot be blank")
        self.connection = connection
        self.contact_email = contact_email.strip()
        self.transport = transport or (
            lambda request, timeout: urllib.request.urlopen(
                request,
                timeout=timeout,
            )
        )
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.timeout = timeout
        self._last_request_at: float | None = None

    def search(self, book: BookRecord) -> list[OpenLibraryCandidate]:
        """Find up to five candidates using ISBN or title and author metadata."""

        parameters: dict[str, str | int] = {
            "fields": _SEARCH_FIELDS,
            "limit": 5,
        }
        if book.isbn:
            parameters["isbn"] = book.isbn
        else:
            parameters["title"] = book.title
            if book.author:
                parameters["author"] = book.author
        payload = self._request_json("/search.json", parameters)
        documents = payload.get("docs")
        if not isinstance(documents, list):
            raise OpenLibraryLookupError(
                "Open Library search response has no docs list"
            )
        return [
            _candidate_from_document(document)
            for document in documents[:5]
            if isinstance(document, Mapping)
        ]

    def complete_candidate(
        self, candidate: OpenLibraryCandidate
    ) -> OpenLibraryCandidate:
        """Fetch selected JSON records only when search metadata is incomplete."""

        completed = candidate
        if candidate.edition_key and candidate.edition_incomplete:
            edition = self._request_key(candidate.edition_key)
            completed = replace(
                completed,
                title=_first_text(edition.get("title")) or completed.title,
                isbn=_edition_isbn(edition) or completed.isbn,
                series=_first_text(edition.get("series")) or completed.series,
                publication_year=_year(edition.get("publish_date"))
                or _year(edition.get("publish_year"))
                or completed.publication_year,
                edition_incomplete=False,
            )
        missing_work_field = any(
            value is None
            for value in (
                completed.title,
                completed.author,
                completed.series,
                completed.publication_year,
            )
        )
        if candidate.work_key and candidate.work_incomplete and missing_work_field:
            work = self._request_key(candidate.work_key)
            completed = replace(
                completed,
                title=completed.title or _first_text(work.get("title")),
                author=completed.author or _work_author(work),
                series=completed.series or _first_text(work.get("series")),
                publication_year=completed.publication_year
                or _year(work.get("first_publish_date"))
                or _year(work.get("first_publish_year")),
                work_incomplete=False,
            )
        return completed

    def _request_key(self, key: str) -> dict[str, object]:
        path = key if key.startswith("/") else f"/{key}"
        if not path.endswith(".json"):
            path = f"{path}.json"
        return self._request_json(path)

    def _request_json(
        self,
        path: str,
        parameters: Mapping[str, str | int] | None = None,
    ) -> dict[str, object]:
        query = urllib.parse.urlencode(sorted((parameters or {}).items()))
        url = f"{_BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"

        cached = self.connection.execute(
            """
            SELECT response_json, fetched_at
            FROM open_library_cache
            WHERE request_url = ?
            """,
            (url,),
        ).fetchone()
        now = self.clock()
        if (
            cached is not None
            and now - float(cached["fetched_at"]) < _CACHE_TTL_SECONDS
        ):
            return self._decode(cached["response_json"])

        headers = {
            "Accept": "application/json",
            "User-Agent": f"books-db/0.0.1 (contact: {self.contact_email})",
        }
        request = urllib.request.Request(url, headers=headers)
        response_body: bytes | str | None = None
        for attempt in range(3):
            self._throttle()
            try:
                response = self.transport(request, self.timeout)
                response_body = response.read()
                status = getattr(response, "status", 200)
                if status == 429 or status >= 500:
                    if attempt == 2:
                        raise OpenLibraryLookupError(
                            f"Open Library returned HTTP {status} after retries"
                        )
                    self.sleep(self._retry_after(getattr(response, "headers", {})))
                    continue
                if status >= 400:
                    raise OpenLibraryLookupError(f"Open Library returned HTTP {status}")
                break
            except urllib.error.HTTPError as error:
                if error.code != 429 and error.code < 500:
                    raise OpenLibraryLookupError(
                        f"Open Library returned HTTP {error.code}"
                    ) from error
                if attempt == 2:
                    raise OpenLibraryLookupError(
                        f"Open Library returned HTTP {error.code} after retries"
                    ) from error
                self.sleep(self._retry_after(error.headers))
            except (TimeoutError, urllib.error.URLError, OSError) as error:
                raise OpenLibraryLookupError(
                    f"Open Library request failed: {error}"
                ) from error

        if response_body is None:
            raise OpenLibraryLookupError("Open Library request did not return data")
        payload = self._decode(response_body)
        self.connection.execute(
            """
            INSERT INTO open_library_cache (request_url, response_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(request_url) DO UPDATE SET
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at
            """,
            (url, json.dumps(payload, separators=(",", ":")), self.clock()),
        )
        return payload

    def _decode(self, value: bytes | str) -> dict[str, object]:
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise OpenLibraryLookupError(
                "Open Library returned malformed JSON"
            ) from error
        if not isinstance(payload, dict):
            raise OpenLibraryLookupError(
                "Open Library returned an unexpected JSON value"
            )
        return payload

    def _throttle(self) -> None:
        now = self.clock()
        if self._last_request_at is not None:
            remaining = _MINIMUM_REQUEST_INTERVAL - (now - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
                now = self.clock()
        self._last_request_at = now

    def _retry_after(self, headers: Mapping[str, str] | None) -> float:
        value = headers.get("Retry-After") if headers else None
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    target = parsedate_to_datetime(value).timestamp()
                    return max(0.0, target - self.clock())
                except (TypeError, ValueError, OverflowError):
                    pass
        return 1.0
