# Usage

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

## CLI

Import a personal CSV into the default `books.db` in the current directory:

```bash
uv run books_db import reading-list.csv
```

Import a Goodreads TSV into another database:

```bash
uv run books_db import goodreads.tsv --database data/library.db
```

Import first, then enrich only inserted or replaced records:

```bash
uv run books_db import reading-list.csv --database data/library.db \
  --enrich --contact-email reader@example.com
```

Module execution is equivalent:

```bash
uv run python -m books_db import reading-list.csv
```

On success the command prints:

```text
Import complete: 12 imported, 1 replaced, 2 skipped.
```

Success returns exit code `0`, import failures return `1`, and argparse returns
its standard code for invalid CLI usage.

## Personal CSV

The personal format uses commas. Header matching trims, collapses whitespace,
and case-folds values.

| Field | Accepted headers | Required |
|---|---|---|
| Title | `Title`, `Book Title` | Yes |
| Author | `Author`, `Author Name` | No |
| Series | `Series`, `Series Name` | No |
| Year | `Year`, `Publication Year`, `Published Year`, `Original Publication Year`, `Year Published` | No |
| Status | `Status`, `Read Status`, `Read?`, `Read` | No |
| Source | `Source`, `Recommendation Source` | No |
| Notes | `Notes`, `Note` | No |
| ISBN | `ISBN`, `ISBN-10`, `ISBN-13` | No |

```csv
Title,Author,Series,Year,ISBN,Status,Source,Notes
The Fifth Season,N. K. Jemisin,The Broken Earth,2015,9780316229296,currently reading,Friend,
Kindred,Octavia E. Butler,,1979,9780807083697,yes,Library,Excellent
```

The stored status values and personal inputs are:

| Stored status | Accepted input |
|---|---|
| `unread` | blank, `unread`, `no`, `n`, `false`, `0` |
| `currently reading` | `currently reading`, `reading`, `currently-reading` |
| `read` | `read`, `yes`, `y`, `true`, `1` |
| `did not finish` | `did not finish`, `dnf`, `did-not-finish` |
| `on hold` | `on hold`, `hold`, `on-hold` |

## Goodreads TSV

Goodreads input uses tabs. `Title` is required. Recognized optional columns are:

- `Author`
- `Series`
- `Original Publication Year`, `Year Published`, or `Publication Year`
- `Exclusive Shelf`
- `Date Read`
- `My Review`
- `Private Notes`
- `Source`
- `ISBN`
- `ISBN13`

Other columns, including `Additional Authors`, are ignored.

Shelf mappings are `to-read` → `unread`, `currently-reading` →
`currently reading`, and `read` → `read`. If `Exclusive Shelf` is blank, a
nonblank `Date Read` maps to `read`; both blank maps to `unread`. Unknown
nonblank shelves fail validation.

`My Review` and `Private Notes` are combined with labels and a blank line:

```text
My Review:
Review text

Private Notes:
Private note text
```

Both formats are opened as UTF-8 with optional BOM support and CSV-safe newline
handling.

## ISBNs

ISBN-10 and ISBN-13 values are stored without separators and validated using
their standard checksum. Spaces, hyphens, surrounding quotes, and spreadsheet
wrappers such as `="978-0-316-22929-6"` are removed during import. A final `X`
is valid for ISBN-10. Invalid lengths, characters, or checksums fail full-file
validation. When both valid forms are supplied, ISBN-13 wins.

ISBN is optional and deliberately non-unique: separate copies of one edition
can coexist.

## Validation

The title must be nonblank. Author, series, year, source, and notes are
optional. A supplied year must be an integer, and a supplied status must use a
documented value. Errors include the filename, row, field, bad value, and
corrective guidance.

All rows are validated before the database is opened or modified. Once
validation succeeds, the schema setup and complete import run in one explicit
transaction.

## Duplicate choices

Duplicate detection uses the normalized title plus normalized author.
Normalization trims, collapses whitespace, and case-folds. Missing authors
match other missing authors. Records inserted earlier in the same import are
included in subsequent checks.

All matching records are displayed. Enter:

- `k` to keep existing records and skip the incoming row;
- `i` to import another copy;
- `r` to replace a match, selecting one when several exist; or
- `a` to abort.

Aborting, Ctrl-C, input EOF, or a storage error rolls back the entire import.

## Open Library enrichment

Open Library asks API clients to identify themselves. Supply a contact address
on the command line or through the environment:

```bash
uv run books_db enrich --database data/library.db \
  --contact-email reader@example.com

export BOOKS_DB_OPEN_LIBRARY_EMAIL=reader@example.com
uv run books_db enrich --database data/library.db
```

See Open Library's [API usage guidelines](https://openlibrary.org/developers/api)
and [Search API documentation](https://openlibrary.org/dev/docs/api/search).

An explicit `--contact-email` takes precedence over
`BOOKS_DB_OPEN_LIBRARY_EMAIL`. If neither is present, enrichment exits with
actionable configuration guidance.

Standalone enrichment processes every book in ID order. `import --enrich`
first commits the complete atomic import, then processes only inserted or
replaced record IDs; duplicate rows that were skipped are excluded.

The lookup uses ISBN when stored, otherwise title and optional author. No
results leave the book unchanged. With several results, up to five numbered
candidates show title, primary author, edition year, ISBN, and Open Library
key. Choose a number or enter `skip`.

For a selected result, differing fields are shown in this order:

1. ISBN
2. Title
3. Author
4. Series
5. Year

Enter exactly `accept` to use the proposed value or `keep` to retain the stored
one. Differences that normalize to the same text are ignored. Blank fields
still require approval before being filled. Source, status, and notes are
never reviewed or changed.

Accepted changes are committed after each book. `cancel`, Ctrl-C, or input EOF
returns failure, preserves completed books and any preceding import, and
leaves the current and remaining books unchanged. No-match and per-book lookup
failures do not make the overall completed run fail. The final summary reports
reviewed, updated, unchanged, skipped/ambiguous, not found, and failed lookup
counts.

Successful JSON responses are cached by canonical request URL for seven days;
errors are not cached. Uncached traffic is limited to three requests per
second with a ten-second timeout. HTTP 429 and server errors are retried at
most twice, and `Retry-After` is honored.

## Storage model

The `books` table references normalized `authors`, `series`, and `sources`
tables with nullable foreign keys. Equivalent lookup values share a row and
retain the spelling from their first import. `books.status` has a `CHECK`
constraint for exactly `unread`, `currently reading`, `read`,
`did not finish`, and `on hold`. Foreign keys are enabled on each connection.

Opening an older database through `create_schema` adds nullable `books.isbn`
and the Open Library cache without losing data. Repeating the migration is
safe.
