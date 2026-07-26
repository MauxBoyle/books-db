"""Tests for personal CSV and Goodreads TSV adapters."""

import csv

import pytest

from books_db.adapters import ImportValidationError, parse_import_file
from books_db.database import ReadStatus


def write_rows(path, rows, *, delimiter=",", encoding="utf-8"):
    with path.open("w", encoding=encoding, newline="") as import_file:
        writer = csv.writer(import_file, delimiter=delimiter)
        writer.writerows(rows)


def test_personal_csv_normalizes_headers_and_optional_values(tmp_path):
    import_path = tmp_path / "personal.csv"
    write_rows(
        import_path,
        [
            [
                " BOOK   TITLE ",
                "Author",
                "Series",
                "Published Year",
                "Read?",
                "Source",
                "Notes",
            ],
            [
                " Kindred ",
                " Octavia   E. Butler ",
                "",
                "1979",
                "yes",
                "Friend",
                "Excellent",
            ],
            ["Piranesi", "", "", "", "", "", ""],
        ],
    )

    books = parse_import_file(import_path)

    assert books[0].title == "Kindred"
    assert books[0].author == "Octavia E. Butler"
    assert books[0].publication_year == 1979
    assert books[0].status is ReadStatus.READ
    assert books[1].author is None
    assert books[1].status is ReadStatus.UNREAD


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("unread", ReadStatus.UNREAD),
        ("no", ReadStatus.UNREAD),
        ("N", ReadStatus.UNREAD),
        ("false", ReadStatus.UNREAD),
        ("0", ReadStatus.UNREAD),
        ("read", ReadStatus.READ),
        ("Yes", ReadStatus.READ),
        ("y", ReadStatus.READ),
        ("TRUE", ReadStatus.READ),
        ("1", ReadStatus.READ),
        ("currently reading", ReadStatus.CURRENTLY_READING),
        ("reading", ReadStatus.CURRENTLY_READING),
        ("currently-reading", ReadStatus.CURRENTLY_READING),
        ("did not finish", ReadStatus.DID_NOT_FINISH),
        ("dnf", ReadStatus.DID_NOT_FINISH),
        ("did-not-finish", ReadStatus.DID_NOT_FINISH),
        ("on hold", ReadStatus.ON_HOLD),
        ("hold", ReadStatus.ON_HOLD),
        ("on-hold", ReadStatus.ON_HOLD),
    ],
)
def test_personal_status_aliases(tmp_path, raw_status, expected):
    import_path = tmp_path / "personal.csv"
    write_rows(import_path, [["Title", "Status"], ["Book", raw_status]])

    assert parse_import_file(import_path)[0].status is expected


def test_goodreads_tsv_maps_shelves_and_combines_notes(tmp_path):
    import_path = tmp_path / "goodreads.tsv"
    write_rows(
        import_path,
        [
            [
                "Title",
                "Author",
                "Original Publication Year",
                "Exclusive Shelf",
                "Date Read",
                "My Review",
                "Private Notes",
                "Additional Authors",
                "Book Id",
            ],
            [
                "The Left Hand of Darkness",
                "Ursula K. Le Guin",
                "1969",
                "read",
                "",
                "A classic.",
                "Reread this.",
                "Someone Else",
                "123",
            ],
            [
                "Ancillary Justice",
                "Ann Leckie",
                "2013",
                "",
                "2024/01/02",
                "",
                "",
                "",
                "456",
            ],
            [
                "A Memory Called Empire",
                "Arkady Martine",
                "2019",
                "to-read",
                "",
                "",
                "",
                "",
                "789",
            ],
        ],
        delimiter="\t",
    )

    books = parse_import_file(import_path)

    assert books[0].status is ReadStatus.READ
    assert books[0].notes == ("My Review:\nA classic.\n\nPrivate Notes:\nReread this.")
    assert books[1].status is ReadStatus.READ
    assert books[1].notes is None
    assert books[2].status is ReadStatus.UNREAD


def test_goodreads_currently_reading_and_single_note_sections(tmp_path):
    import_path = tmp_path / "goodreads.tsv"
    write_rows(
        import_path,
        [
            ["Title", "Exclusive Shelf", "My Review", "Private Notes"],
            ["First", "currently-reading", "Review only", ""],
            ["Second", "read", "", "Private only"],
        ],
        delimiter="\t",
    )

    books = parse_import_file(import_path)

    assert books[0].status is ReadStatus.CURRENTLY_READING
    assert books[0].notes == "My Review:\nReview only"
    assert books[1].notes == "Private Notes:\nPrivate only"


def test_notes_keep_internal_line_breaks(tmp_path):
    import_path = tmp_path / "personal.csv"
    write_rows(
        import_path,
        [["Title", "Notes"], ["Book", "  First line\nSecond line  "]],
    )

    assert parse_import_file(import_path)[0].notes == "First line\nSecond line"


def test_utf8_bom_is_accepted(tmp_path):
    import_path = tmp_path / "bom.csv"
    write_rows(
        import_path,
        [["Title", "Author"], ["Circe", "Madeline Miller"]],
        encoding="utf-8-sig",
    )

    assert parse_import_file(import_path)[0].title == "Circe"


@pytest.mark.parametrize(
    ("rows", "expected_field", "expected_value", "guidance"),
    [
        ([["Title", "Year"], ["Book", "twenty"]], "year", "twenty", "whole number"),
        ([["Title"], ["   "]], "title", "   ", "nonblank title"),
        (
            [["Title", "Status"], ["Book", "maybe"]],
            "status",
            "maybe",
            "accepted status",
        ),
    ],
)
def test_personal_row_errors_are_actionable(
    tmp_path, rows, expected_field, expected_value, guidance
):
    import_path = tmp_path / "bad.csv"
    write_rows(import_path, rows)

    with pytest.raises(ImportValidationError) as error:
        parse_import_file(import_path)

    message = str(error.value)
    assert str(import_path) in message
    assert "row 2" in message
    assert expected_field in message
    assert repr(expected_value) in message
    assert guidance in message


def test_goodreads_rejects_unknown_nonblank_shelf(tmp_path):
    import_path = tmp_path / "bad.tsv"
    write_rows(
        import_path,
        [["Title", "Exclusive Shelf"], ["Book", "want-to-buy"]],
        delimiter="\t",
    )

    with pytest.raises(ImportValidationError, match="want-to-buy"):
        parse_import_file(import_path)


def test_unknown_header_format_is_rejected(tmp_path):
    import_path = tmp_path / "unknown.csv"
    write_rows(import_path, [["Name", "Writer"], ["Book", "Author"]])

    with pytest.raises(ImportValidationError, match="header"):
        parse_import_file(import_path)


def test_personal_isbn_headers_are_normalized_and_isbn13_is_preferred(tmp_path):
    import_path = tmp_path / "isbn.csv"
    write_rows(
        import_path,
        [
            ["Title", "ISBN-10", "ISBN-13"],
            ["Dune", " 0-441-17271-7 ", '="978-0-441-17271-9"'],
        ],
    )

    assert parse_import_file(import_path)[0].isbn == "9780441172719"


def test_goodreads_isbn_columns_are_supported(tmp_path):
    import_path = tmp_path / "goodreads.tsv"
    write_rows(
        import_path,
        [
            ["Title", "Book Id", "Exclusive Shelf", "ISBN", "ISBN13"],
            ["Dune", "1", "read", '="0441172717"', '="9780441172719"'],
        ],
        delimiter="\t",
    )

    assert parse_import_file(import_path)[0].isbn == "9780441172719"


@pytest.mark.parametrize(
    "isbn",
    ["123", "0441172718", "9780441172718", "978044117271X"],
)
def test_invalid_isbn_is_actionable(tmp_path, isbn):
    import_path = tmp_path / "bad-isbn.csv"
    write_rows(import_path, [["Title", "ISBN"], ["Dune", isbn]])

    with pytest.raises(ImportValidationError, match="valid ISBN"):
        parse_import_file(import_path)
