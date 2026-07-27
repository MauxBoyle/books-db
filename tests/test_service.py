"""Tests for the atomic, interactive import service."""

import csv
import sqlite3

import pytest

import books_db.service
from books_db.adapters import ImportValidationError
from books_db.database import connect_database
from books_db.service import ImportCancelled, import_books


def write_personal_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as import_file:
        writer = csv.writer(import_file)
        writer.writerows(rows)


def answers(*values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def stored_books(database_path):
    connection = connect_database(database_path)
    rows = connection.execute(
        "SELECT id, title, publication_year, notes FROM books ORDER BY id"
    ).fetchall()
    connection.close()
    return [tuple(row) for row in rows]


def test_import_is_atomic_and_reports_counts(tmp_path):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(
        import_path,
        [
            ["Title", "Author", "Year"],
            ["Kindred", "Octavia E. Butler", "1979"],
            ["Piranesi", "Susanna Clarke", "2020"],
        ],
    )

    summary = import_books(import_path, database_path)

    assert (summary.imported, summary.replaced, summary.skipped) == (2, 0, 0)
    assert summary.affected_ids == (1, 2)
    assert [row[1] for row in stored_books(database_path)] == ["Kindred", "Piranesi"]


def test_invalid_file_is_validated_before_database_is_opened(tmp_path):
    import_path = tmp_path / "bad.csv"
    database_path = tmp_path / "should-not-exist.db"
    write_personal_csv(import_path, [["Title", "Year"], ["Book", "unknown"]])

    with pytest.raises(ImportValidationError):
        import_books(import_path, database_path)

    assert not database_path.exists()


def test_duplicate_in_same_import_can_keep_existing(tmp_path):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(
        import_path,
        [
            ["Title", "Author"],
            ["  KINDRED", "Octavia   E. Butler"],
            ["kindred ", "octavia e. BUTLER"],
        ],
    )

    summary = import_books(import_path, database_path, input_func=answers("k"))

    assert (summary.imported, summary.replaced, summary.skipped) == (1, 0, 1)
    assert len(stored_books(database_path)) == 1


def test_duplicate_with_two_missing_authors_is_detected_across_runs(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(first_path, [["Title"], ["Piranesi"]])
    write_personal_csv(second_path, [["Title"], [" piranesi "]])
    import_books(first_path, database_path)

    summary = import_books(second_path, database_path, input_func=answers("k"))

    assert summary.skipped == 1
    assert len(stored_books(database_path)) == 1


def test_duplicate_can_be_imported_as_another_copy(tmp_path):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(
        import_path,
        [["Title", "Author"], ["Dune", "Frank Herbert"], ["Dune", "Frank Herbert"]],
    )

    summary = import_books(import_path, database_path, input_func=answers("i"))

    assert (summary.imported, summary.replaced, summary.skipped) == (2, 0, 0)
    assert len(stored_books(database_path)) == 2


def test_duplicate_can_replace_the_only_match(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(
        first_path,
        [["Title", "Author", "Year"], ["Dune", "Frank Herbert", "1964"]],
    )
    write_personal_csv(
        second_path,
        [["Title", "Author", "Year"], ["Dune", "Frank Herbert", "1965"]],
    )
    import_books(first_path, database_path)

    summary = import_books(second_path, database_path, input_func=answers("r"))

    assert (summary.imported, summary.replaced, summary.skipped) == (0, 1, 0)
    assert summary.affected_ids == (1,)
    assert stored_books(database_path)[0][2] == 1965


def test_replacing_one_of_multiple_matches_prompts_for_selection(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    database_path = tmp_path / "books.db"
    output = []
    write_personal_csv(
        first_path,
        [
            ["Title", "Author", "Year"],
            ["Dune", "Frank Herbert", "1964"],
            ["Dune", "Frank Herbert", "1965"],
        ],
    )
    write_personal_csv(
        second_path,
        [["Title", "Author", "Year"], ["Dune", "Frank Herbert", "2024"]],
    )
    import_books(first_path, database_path, input_func=answers("i"))

    summary = import_books(
        second_path,
        database_path,
        input_func=answers("r", "2"),
        output_func=output.append,
    )

    assert summary.replaced == 1
    assert [row[2] for row in stored_books(database_path)] == [1964, 2024]
    rendered = "\n".join(output)
    assert "1964" in rendered
    assert "1965" in rendered


def test_abort_rolls_back_rows_inserted_earlier_in_import(tmp_path):
    seed_path = tmp_path / "seed.csv"
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(seed_path, [["Title"], ["Existing"]])
    write_personal_csv(
        import_path,
        [["Title"], ["New in transaction"], ["Existing"]],
    )
    import_books(seed_path, database_path)

    with pytest.raises(ImportCancelled):
        import_books(import_path, database_path, input_func=answers("a"))

    assert [row[1] for row in stored_books(database_path)] == ["Existing"]


@pytest.mark.parametrize(
    "input_func",
    [
        pytest.param(lambda _prompt: (_ for _ in ()).throw(EOFError), id="input-eof"),
        pytest.param(
            lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt),
            id="keyboard-interrupt",
        ),
    ],
)
def test_input_cancellation_rolls_back(tmp_path, input_func):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(
        import_path,
        [["Title"], ["Piranesi"], ["Piranesi"]],
    )

    with pytest.raises(ImportCancelled):
        import_books(import_path, database_path, input_func=input_func)

    connection = connect_database(database_path)
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'books'"
    ).fetchall()
    connection.close()
    assert tables == []


def test_storage_error_rolls_back_the_complete_import(tmp_path, monkeypatch):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_personal_csv(import_path, [["Title"], ["First"], ["Second"]])
    real_insert = books_db.service.insert_book
    insert_count = 0

    def fail_on_second_insert(connection, book):
        nonlocal insert_count
        insert_count += 1
        if insert_count == 2:
            raise sqlite3.OperationalError("simulated storage failure")
        return real_insert(connection, book)

    monkeypatch.setattr(books_db.service, "insert_book", fail_on_second_insert)

    with pytest.raises(sqlite3.OperationalError, match="simulated storage failure"):
        import_books(import_path, database_path)

    connection = connect_database(database_path)
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'books'"
    ).fetchall()
    connection.close()
    assert tables == []


def test_values_that_look_like_sql_are_stored_as_plain_data(tmp_path):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    title = "Robert'); DROP TABLE books;--"
    write_personal_csv(import_path, [["Title", "Author"], [title, "A'uthor"]])

    import_books(import_path, database_path)

    assert stored_books(database_path)[0][1] == title
