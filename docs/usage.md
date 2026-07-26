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

```csv
Title,Author,Series,Year,Status,Source,Notes
The Fifth Season,N. K. Jemisin,The Broken Earth,2015,currently reading,Friend,
Kindred,Octavia E. Butler,,1979,yes,Library,Excellent
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

## Storage model

The `books` table references normalized `authors`, `series`, and `sources`
tables with nullable foreign keys. Equivalent lookup values share a row and
retain the spelling from their first import. `books.status` has a `CHECK`
constraint for exactly `unread`, `currently reading`, `read`,
`did not finish`, and `on hold`. Foreign keys are enabled on each connection.
