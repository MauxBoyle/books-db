# API Reference

## Public package

::: books_db
    options:
      show_source: true

## Import adapters

::: books_db.adapters
    options:
      members:
        - ImportValidationError
        - parse_import_file
      show_source: true

## Database

::: books_db.database
    options:
      members:
        - ReadStatus
        - BookInput
        - BookRecord
        - connect_database
        - create_schema
        - insert_book
        - replace_book
        - find_duplicates
      show_source: true

## Import service

::: books_db.service
    options:
      members:
        - ImportCancelled
        - ImportSummary
        - import_books
      show_source: true
