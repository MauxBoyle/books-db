"""Tests for the application entrypoint."""

from books_db.app import main


def test_main_logs_greeting(capfd):
    main()
    captured = capfd.readouterr()
    assert "Hello from books_db!" in captured.err
