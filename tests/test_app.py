"""Tests for the command-line interface."""

import csv

import pytest

import books_db.app
from books_db.app import main
from books_db.database import connect_database
from books_db.enrichment import EnrichmentSummary


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as import_file:
        csv.writer(import_file).writerows(rows)


def count_books(database_path):
    connection = connect_database(database_path)
    count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    connection.close()
    return count


def test_cli_imports_to_default_database_and_prints_summary(
    tmp_path, monkeypatch, capsys
):
    import_path = tmp_path / "books.csv"
    write_csv(import_path, [["Title"], ["Kindred"]])
    monkeypatch.chdir(tmp_path)

    exit_code = main(["import", str(import_path)])

    assert exit_code == 0
    assert count_books(tmp_path / "books.db") == 1
    assert (
        "Import complete: 1 imported, 0 replaced, 0 skipped." in capsys.readouterr().out
    )


def test_cli_uses_custom_database_path(tmp_path):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "data" / "custom.db"
    database_path.parent.mkdir()
    write_csv(import_path, [["Title"], ["Piranesi"]])

    exit_code = main(["import", str(import_path), "--database", str(database_path)])

    assert exit_code == 0
    assert count_books(database_path) == 1


def test_cli_prompts_for_duplicates(tmp_path, monkeypatch, capsys):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_csv(import_path, [["Title"], ["Dune"], ["Dune"]])
    monkeypatch.setattr("builtins.input", lambda _prompt: "k")

    exit_code = main(["import", str(import_path), "--database", str(database_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Possible duplicate" in output
    assert "1 imported, 0 replaced, 1 skipped" in output


def test_cli_returns_one_for_validation_failure_without_creating_database(
    tmp_path, capsys
):
    import_path = tmp_path / "bad.csv"
    database_path = tmp_path / "books.db"
    write_csv(import_path, [["Title", "Year"], ["Book", "not a year"]])

    exit_code = main(["import", str(import_path), "--database", str(database_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "row 2" in captured.err
    assert not database_path.exists()


def test_cli_returns_one_and_rolls_back_on_input_eof(tmp_path, monkeypatch, capsys):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_csv(import_path, [["Title"], ["Dune"], ["Dune"]])

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    exit_code = main(["import", str(import_path), "--database", str(database_path)])

    assert exit_code == 1
    assert "no changes were saved" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [[], ["unknown"], ["import"]])
def test_invalid_cli_usage_uses_argparse_exit_code(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2


def test_cli_enrich_passes_database_and_contact_and_prints_summary(
    tmp_path, monkeypatch, capsys
):
    database_path = tmp_path / "books.db"
    calls = []

    def fake_enrich(path, **options):
        calls.append((path, options))
        return EnrichmentSummary(
            reviewed=3,
            updated=1,
            unchanged=2,
            skipped=1,
            not_found=1,
            failed=1,
        )

    monkeypatch.setattr(books_db.app, "enrich_books", fake_enrich)

    exit_code = main(
        [
            "enrich",
            "--database",
            str(database_path),
            "--contact-email",
            "reader@example.com",
        ]
    )

    assert exit_code == 0
    assert calls[0][0] == database_path
    assert calls[0][1]["contact_email"] == "reader@example.com"
    assert "3 reviewed, 1 updated, 2 unchanged" in capsys.readouterr().out


def test_import_enrichment_runs_after_commit_for_only_affected_ids(
    tmp_path, monkeypatch
):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_csv(import_path, [["Title"], ["Dune"]])
    seen = []

    def fake_enrich(path, **options):
        assert count_books(path) == 1
        seen.extend(options["book_ids"])
        return EnrichmentSummary()

    monkeypatch.setattr(books_db.app, "enrich_books", fake_enrich)

    exit_code = main(
        [
            "import",
            str(import_path),
            "--database",
            str(database_path),
            "--enrich",
            "--contact-email",
            "reader@example.com",
        ]
    )

    assert exit_code == 0
    assert seen == [1]


def test_import_remains_committed_when_enrichment_configuration_is_missing(
    tmp_path, monkeypatch, capsys
):
    import_path = tmp_path / "books.csv"
    database_path = tmp_path / "books.db"
    write_csv(import_path, [["Title"], ["Dune"]])
    monkeypatch.delenv("BOOKS_DB_OPEN_LIBRARY_EMAIL", raising=False)

    exit_code = main(
        [
            "import",
            str(import_path),
            "--database",
            str(database_path),
            "--enrich",
        ]
    )

    assert exit_code == 1
    assert count_books(database_path) == 1
    assert "BOOKS_DB_OPEN_LIBRARY_EMAIL" in capsys.readouterr().err
