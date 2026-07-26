# books-db

`books-db` imports a personal reading list into a normalized, file-backed
SQLite database and can interactively enrich it from Open Library. It supports
a personal comma-separated format and a Goodreads tab-separated export without
requiring a database server.

## Installation

Clone the repository and restore the environment:

```bash
uv sync
```

If the generated `books_db` command reports `ModuleNotFoundError: No module
named 'books_db'`, run it with the source directory explicitly on Python's
import path:

```bash
PYTHONPATH=src uv run --no-sync books_db import path/to/books.csv
```

## Importing books

Import into `books.db` in the current directory:

```bash
uv run books_db import path/to/books.csv
```

Choose another database file:

```bash
uv run books_db import path/to/goodreads.tsv --database data/library.db
```

The same CLI is available as a module:

```bash
uv run python -m books_db import path/to/books.csv
```

The command prints imported, replaced, and skipped totals. A successful import
returns exit code `0`; validation, storage, or cancellation failures return
`1`. Invalid command-line usage uses argparse's standard exit code `2`.

To enrich only the rows inserted or replaced by an import:

```bash
uv run books_db import path/to/books.csv --enrich \
  --contact-email reader@example.com
```

## Personal CSV format

The personal format is comma-separated. Header matching ignores case, leading
or trailing whitespace, and repeated internal whitespace.

| Value | Accepted headers | Required |
|---|---|---|
| Title | `Title`, `Book Title` | Yes |
| Author | `Author`, `Author Name` | No |
| Series | `Series`, `Series Name` | No |
| Year | `Year`, `Publication Year`, `Published Year`, `Original Publication Year`, `Year Published` | No |
| Status | `Status`, `Read Status`, `Read?`, `Read` | No |
| Source | `Source`, `Recommendation Source` | No |
| Notes | `Notes`, `Note` | No |
| ISBN | `ISBN`, `ISBN-10`, `ISBN-13` | No |

Example:

```csv
Title,Author,Series,Year,ISBN-13,Status,Source,Notes
Kindred,Octavia E. Butler,,1979,9780807083697,read,Friend,Excellent
Piranesi,Susanna Clarke,,2020,9781635575637,unread,Book club,
```

Blank statuses default to `unread`. The five stored statuses and accepted
personal aliases are:

| Stored status | Accepted personal values |
|---|---|
| `unread` | blank, `unread`, `no`, `n`, `false`, `0` |
| `currently reading` | `currently reading`, `reading`, `currently-reading` |
| `read` | `read`, `yes`, `y`, `true`, `1` |
| `did not finish` | `did not finish`, `dnf`, `did-not-finish` |
| `on hold` | `on hold`, `hold`, `on-hold` |

Status matching is case-insensitive and ignores surrounding or repeated
whitespace.

## Goodreads TSV format

The Goodreads format must be tab-separated. `Title` is required. Recognized
optional columns are `Author`, `Series`, `Original Publication Year`,
`Year Published`, `Publication Year`, `Exclusive Shelf`, `Date Read`,
`My Review`, `Private Notes`, `Source`, `ISBN`, and `ISBN13`. Columns such as
`Additional Authors` and `Book Id` are safely ignored.

`Exclusive Shelf` maps as follows:

| Goodreads shelf | Stored status |
|---|---|
| `to-read` | `unread` |
| `currently-reading` | `currently reading` |
| `read` | `read` |

When the shelf is blank, a nonblank `Date Read` produces `read`; otherwise the
status is `unread`. Any other nonblank shelf is rejected.

When present, review text and private notes are stored as labeled sections:

```text
My Review:
Review text

Private Notes:
Private note text
```

Both import formats are read as UTF-8 with optional BOM support.

## ISBN validation

ISBNs are stored without spaces or hyphens. Both ISBN-10 (including a final
`X`) and ISBN-13 are checksum-validated. Quoted values and common spreadsheet
wrappers such as `="978-0-8070-8369-7"` are accepted. If valid ISBN-10 and
ISBN-13 columns are both populated, ISBN-13 is preferred. ISBN is nullable and
not unique, so multiple physical copies of the same edition remain valid.

## Open Library enrichment

Enrich every stored book in ID order:

```bash
uv run books_db enrich --database data/library.db \
  --contact-email reader@example.com
```

The email can instead be set once:

```bash
export BOOKS_DB_OPEN_LIBRARY_EMAIL=reader@example.com
uv run books_db enrich --database data/library.db
```

The application identifies itself to Open Library using this address. It
searches by ISBN when available, otherwise by title and optional author. When
several results are returned, it displays at most five for selection. It then
reviews changed ISBN, title, author, series, and year values in that order.
Only the full word `accept` changes a field; `keep` retains the stored value.
Source, reading status, and notes are never changed.

The integration follows Open Library's
[API usage guidelines](https://openlibrary.org/developers/api) and uses its
[Search API](https://openlibrary.org/dev/docs/api/search).

Each completed book is committed independently. Cancelling leaves the current
and remaining books unchanged while preserving earlier work (and a preceding
import). Individual no-match and lookup failures are reported and processing
continues; configuration, database, and cancellation failures return exit code
`1`.

Successful JSON responses are cached in SQLite for seven days. Uncached
requests use a ten-second timeout, run at no more than three per second, and
retry HTTP 429 or server errors at most twice while honoring `Retry-After`.

## Validation and duplicates

Every data row needs a nonblank title. Author, series, year, source, and notes
may be blank; a nonblank year must be an integer. Errors identify the filename,
row, field, rejected value, and how to correct it.

The whole file is validated before the database is opened or changed. A valid
import then runs in one explicit transaction. Storage errors, aborting,
Ctrl-C, or input EOF roll back every change from that import.

A possible duplicate is an exact title-and-author match after trimming,
collapsing whitespace, and case-folding. Two books with missing authors can
therefore match. Each row is checked against existing records and rows inserted
earlier in the same import. The prompt displays every match and offers:

- keep the existing record and skip the incoming row;
- import another copy;
- replace one selected match; or
- abort and roll back the import.

## Database schema

SQLite stores books in `books` with nullable foreign keys to the normalized
`authors`, `series`, and `sources` lookup tables. Repeated lookup values share
one row while retaining the first display spelling. The five reading statuses
are enforced by a database `CHECK` constraint, and foreign-key enforcement is
enabled on every application connection.

`create_schema` safely adds the nullable, non-unique `books.isbn` column to
databases created by earlier releases without removing existing records. The
migration is safe to run repeatedly. The `open_library_cache` table stores
successful API responses by canonical request URL.

## Development

Run the verification suite:

```bash
uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
uv run mkdocs build --strict
```

Preview the documentation with:

```bash
uv run python scripts/serve_docs.py
```
