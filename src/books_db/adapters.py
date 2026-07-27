"""Adapters that validate personal CSV and Goodreads TSV imports."""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from pathlib import Path

from books_db.database import (
    BookInput,
    ReadStatus,
    clean_display_value,
    normalize_value,
)


class ImportValidationError(ValueError):
    """An actionable file, header, or row validation error."""

    def __init__(
        self,
        path: str | Path,
        row_number: int,
        field: str,
        value: object,
        guidance: str,
    ) -> None:
        self.path = Path(path)
        self.row_number = row_number
        self.field = field
        self.value = value
        self.guidance = guidance
        super().__init__(
            f"{self.path}: row {row_number}, field {field!r}, invalid value "
            f"{value!r}; {guidance}"
        )


_PERSONAL_HEADERS = {
    "title": {"title", "book title"},
    "author": {"author", "author name"},
    "series": {"series", "series name"},
    "year": {
        "year",
        "publication year",
        "published year",
        "original publication year",
        "year published",
    },
    "status": {"status", "read status", "read?", "read"},
    "source": {"source", "recommendation source"},
    "notes": {"notes", "note"},
    "isbn": {"isbn"},
    "isbn10": {"isbn-10", "isbn 10"},
    "isbn13": {"isbn-13", "isbn 13"},
}

_GOODREADS_HEADERS = {
    "title": {"title"},
    "author": {"author"},
    "series": {"series"},
    "year": {"original publication year", "year published", "publication year"},
    "shelf": {"exclusive shelf"},
    "date_read": {"date read"},
    "review": {"my review"},
    "private_notes": {"private notes"},
    "source": {"source"},
    "isbn": {"isbn"},
    "isbn13": {"isbn13", "isbn-13", "isbn 13"},
}

_GOODREADS_MARKERS = {
    "exclusive shelf",
    "date read",
    "my review",
    "private notes",
    "book id",
}

_PERSONAL_STATUSES = {
    "": ReadStatus.UNREAD,
    "unread": ReadStatus.UNREAD,
    "no": ReadStatus.UNREAD,
    "n": ReadStatus.UNREAD,
    "false": ReadStatus.UNREAD,
    "0": ReadStatus.UNREAD,
    "read": ReadStatus.READ,
    "yes": ReadStatus.READ,
    "y": ReadStatus.READ,
    "true": ReadStatus.READ,
    "1": ReadStatus.READ,
    "currently reading": ReadStatus.CURRENTLY_READING,
    "reading": ReadStatus.CURRENTLY_READING,
    "currently-reading": ReadStatus.CURRENTLY_READING,
    "did not finish": ReadStatus.DID_NOT_FINISH,
    "dnf": ReadStatus.DID_NOT_FINISH,
    "did-not-finish": ReadStatus.DID_NOT_FINISH,
    "on hold": ReadStatus.ON_HOLD,
    "hold": ReadStatus.ON_HOLD,
    "on-hold": ReadStatus.ON_HOLD,
}

_GOODREADS_STATUSES = {
    "to-read": ReadStatus.UNREAD,
    "currently-reading": ReadStatus.CURRENTLY_READING,
    "read": ReadStatus.READ,
}


def _read_header(path: Path, delimiter: str) -> list[str]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as import_file:
            return next(csv.reader(import_file, delimiter=delimiter, strict=True), [])
    except (OSError, UnicodeError, csv.Error) as error:
        raise ImportValidationError(
            path,
            1,
            "file",
            str(error),
            "provide a readable UTF-8 CSV or TSV file",
        ) from error


def _normalized_headers(header: Sequence[str]) -> list[str]:
    return [normalize_value(value) for value in header]


def _detect_format(path: Path) -> tuple[str, dict[str, set[str]], str]:
    comma_header = _normalized_headers(_read_header(path, ","))
    tab_header = _normalized_headers(_read_header(path, "\t"))

    if "title" in tab_header and _GOODREADS_MARKERS.intersection(tab_header):
        return "\t", _GOODREADS_HEADERS, "Goodreads"
    personal_titles = _PERSONAL_HEADERS["title"]
    if personal_titles.intersection(comma_header):
        return ",", _PERSONAL_HEADERS, "personal"
    raise ImportValidationError(
        path,
        1,
        "header",
        comma_header[0] if len(comma_header) == 1 else comma_header,
        "use the documented personal CSV or Goodreads TSV headers",
    )


def _map_headers(
    path: Path, header: Sequence[str], aliases: dict[str, set[str]]
) -> dict[str, int]:
    normalized = _normalized_headers(header)
    duplicates = {item for item in normalized if item and normalized.count(item) > 1}
    if duplicates:
        raise ImportValidationError(
            path,
            1,
            "header",
            ", ".join(sorted(duplicates)),
            "use each column heading only once",
        )

    mapping: dict[str, int] = {}
    for field, accepted_headers in aliases.items():
        matches = [
            index
            for index, header_name in enumerate(normalized)
            if header_name in accepted_headers
        ]
        if len(matches) > 1:
            raise ImportValidationError(
                path,
                1,
                "header",
                [header[index] for index in matches],
                f"use only one column for {field!r}",
            )
        if matches:
            mapping[field] = matches[0]

    if "title" not in mapping:
        raise ImportValidationError(
            path,
            1,
            "title",
            "",
            "include a Title or Book Title column",
        )
    return mapping


def _cell(row: Sequence[str], mapping: dict[str, int], field: str) -> str:
    index = mapping.get(field)
    if index is None or index >= len(row):
        return ""
    return row[index]


def _optional_text(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def _year(path: Path, row_number: int, raw_value: str) -> int | None:
    cleaned = clean_display_value(raw_value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError as error:
        raise ImportValidationError(
            path,
            row_number,
            "year",
            raw_value,
            "enter a whole number or leave the year blank",
        ) from error


def normalize_isbn(value: str | None) -> str | None:
    """Remove common spreadsheet formatting and validate an ISBN checksum."""

    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.startswith("="):
        cleaned = cleaned[1:].strip()
    cleaned = cleaned.strip("\"'")
    normalized = "".join(character for character in cleaned if character not in " -")
    if not normalized:
        return None
    normalized = normalized.upper()
    if len(normalized) == 10:
        if not normalized[:9].isdigit() or not (
            normalized[9].isdigit() or normalized[9] == "X"
        ):
            raise ValueError("ISBN-10 must contain nine digits and a digit or X")
        values = [int(character) for character in normalized[:9]]
        values.append(10 if normalized[9] == "X" else int(normalized[9]))
        if (
            sum(
                weight * digit
                for weight, digit in zip(range(10, 0, -1), values, strict=True)
            )
            % 11
        ):
            raise ValueError("ISBN-10 checksum is invalid")
        return normalized
    if len(normalized) == 13:
        if not normalized.isdigit():
            raise ValueError("ISBN-13 must contain only digits")
        checksum = sum(
            int(character) * (1 if index % 2 == 0 else 3)
            for index, character in enumerate(normalized)
        )
        if checksum % 10:
            raise ValueError("ISBN-13 checksum is invalid")
        return normalized
    raise ValueError("ISBN must contain 10 or 13 characters")


def _isbn(
    path: Path, row_number: int, row: Sequence[str], mapping: dict[str, int]
) -> str | None:
    parsed: dict[str, str] = {}
    for field in ("isbn", "isbn10", "isbn13"):
        raw_value = _cell(row, mapping, field)
        if not raw_value.strip():
            continue
        try:
            normalized = normalize_isbn(raw_value)
        except ValueError as error:
            raise ImportValidationError(
                path,
                row_number,
                field.replace("isbn", "ISBN-").rstrip("-"),
                raw_value,
                f"enter a valid ISBN-10 or ISBN-13 ({error})",
            ) from error
        if normalized is not None:
            expected_length = (
                10 if field == "isbn10" else 13 if field == "isbn13" else None
            )
            if expected_length is not None and len(normalized) != expected_length:
                raise ImportValidationError(
                    path,
                    row_number,
                    field.upper(),
                    raw_value,
                    f"enter a valid ISBN-{expected_length}",
                )
            parsed[field] = normalized

    isbn13 = next((value for value in parsed.values() if len(value) == 13), None)
    return isbn13 or next(iter(parsed.values()), None)


def _title(path: Path, row_number: int, raw_value: str) -> str:
    cleaned = clean_display_value(raw_value)
    if cleaned is None:
        raise ImportValidationError(
            path,
            row_number,
            "title",
            raw_value,
            "enter a nonblank title",
        )
    return cleaned


def _personal_status(path: Path, row_number: int, raw_value: str) -> ReadStatus:
    normalized = normalize_value(raw_value)
    try:
        return _PERSONAL_STATUSES[normalized]
    except KeyError as error:
        raise ImportValidationError(
            path,
            row_number,
            "status",
            raw_value,
            "use an accepted status or alias, or leave the status blank",
        ) from error


def _goodreads_status(
    path: Path, row_number: int, raw_shelf: str, raw_date_read: str
) -> ReadStatus:
    normalized = normalize_value(raw_shelf)
    if not normalized:
        return (
            ReadStatus.READ if clean_display_value(raw_date_read) else ReadStatus.UNREAD
        )
    try:
        return _GOODREADS_STATUSES[normalized]
    except KeyError as error:
        raise ImportValidationError(
            path,
            row_number,
            "status",
            raw_shelf,
            "use to-read, currently-reading, or read for Exclusive Shelf",
        ) from error


def _personal_book(
    path: Path, row_number: int, row: Sequence[str], mapping: dict[str, int]
) -> BookInput:
    return BookInput(
        title=_title(path, row_number, _cell(row, mapping, "title")),
        author=clean_display_value(_cell(row, mapping, "author")),
        series=clean_display_value(_cell(row, mapping, "series")),
        publication_year=_year(path, row_number, _cell(row, mapping, "year")),
        source=clean_display_value(_cell(row, mapping, "source")),
        status=_personal_status(path, row_number, _cell(row, mapping, "status")),
        notes=_optional_text(_cell(row, mapping, "notes")),
        isbn=_isbn(path, row_number, row, mapping),
    )


def _goodreads_notes(row: Sequence[str], mapping: dict[str, int]) -> str | None:
    sections = []
    review = _optional_text(_cell(row, mapping, "review"))
    private_notes = _optional_text(_cell(row, mapping, "private_notes"))
    if review:
        sections.append(f"My Review:\n{review}")
    if private_notes:
        sections.append(f"Private Notes:\n{private_notes}")
    return "\n\n".join(sections) or None


def _goodreads_book(
    path: Path, row_number: int, row: Sequence[str], mapping: dict[str, int]
) -> BookInput:
    return BookInput(
        title=_title(path, row_number, _cell(row, mapping, "title")),
        author=clean_display_value(_cell(row, mapping, "author")),
        series=clean_display_value(_cell(row, mapping, "series")),
        publication_year=_year(path, row_number, _cell(row, mapping, "year")),
        source=clean_display_value(_cell(row, mapping, "source")),
        status=_goodreads_status(
            path,
            row_number,
            _cell(row, mapping, "shelf"),
            _cell(row, mapping, "date_read"),
        ),
        notes=_goodreads_notes(row, mapping),
        isbn=_isbn(path, row_number, row, mapping),
    )


def parse_import_file(path: str | Path) -> list[BookInput]:
    """Detect, parse, and completely validate a supported import file."""

    import_path = Path(path)
    delimiter, aliases, source_format = _detect_format(import_path)
    adapter: Callable[[Path, int, Sequence[str], dict[str, int]], BookInput]
    adapter = _goodreads_book if source_format == "Goodreads" else _personal_book

    try:
        with import_path.open(encoding="utf-8-sig", newline="") as import_file:
            reader = csv.reader(import_file, delimiter=delimiter, strict=True)
            header = next(reader, [])
            mapping = _map_headers(import_path, header, aliases)
            books = [
                adapter(import_path, row_number, row, mapping)
                for row_number, row in enumerate(reader, start=2)
            ]
    except ImportValidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ImportValidationError(
            import_path,
            1,
            "file",
            str(error),
            "provide a readable, well-formed UTF-8 import file",
        ) from error
    return books
