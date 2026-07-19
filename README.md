# books-db

## Installation

Clone the repository, then install its dependencies:

```bash
uv sync
```

## Usage

Run via the CLI entrypoint:

```bash
uv run books_db
```

Run with the development environment:

```bash
uv run --env-file .env books_db
```

Or run as a Python module:

```bash
uv run python -m books_db
```

## Environment Variables

`.env.example` is the configuration template. Copy it to `.env` for development.

- `LOG_LEVEL` defaults to `INFO`; `.env` sets it to `DEBUG` for verbose console output.
- `LOG_FILE` defaults to `app.log` and sets the path to the log file.

`uv run --env-file .env` loads the development environment explicitly; it is not loaded automatically.

## Testing

Run tests:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov
```

## Documentation

Preview documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build static documentation:

```bash
uv run mkdocs build
```
