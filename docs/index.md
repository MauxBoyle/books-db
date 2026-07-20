# books-db

`books-db` turns personal CSV and Goodreads TSV reading lists into a
normalized, file-backed SQLite database.

## Highlights

- Standard-library CSV parsing, CLI handling, and SQLite persistence
- Normalized author, series, and source lookup tables
- Five constrained reading statuses
- Full-file validation before database changes
- Interactive duplicate resolution
- One transaction per import, with rollback on errors or cancellation

Start with the [usage guide](usage.md) for accepted headers, exact status
mappings, duplicate choices, and examples. The [API reference](api.md)
documents the reusable Python interfaces.
