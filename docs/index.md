# books-db

`books-db` turns personal CSV and Goodreads TSV reading lists into a
normalized, file-backed SQLite database and interactively enriches book
metadata from Open Library.

## Highlights

- Standard-library CSV parsing, CLI handling, and SQLite persistence
- Normalized author, series, and source lookup tables
- Five constrained reading statuses
- Full-file validation before database changes
- Interactive duplicate resolution
- One transaction per import, with rollback on errors or cancellation
- Checksum-validated ISBN-10 and ISBN-13 storage
- Identified, throttled, cached Open Library lookups
- Explicit per-field metadata review with one commit per book

Start with the [usage guide](usage.md) for accepted headers, exact status
mappings, duplicate choices, and examples. The [API reference](api.md)
documents the reusable Python interfaces.
