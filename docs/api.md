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
        - get_book
        - list_books
        - update_book_metadata
      show_source: true

## Import service

::: books_db.service
    options:
      members:
        - ImportCancelled
        - ImportSummary
        - import_books
      show_source: true

## Enrichment service

::: books_db.enrichment
    options:
      members:
        - EnrichmentCancelled
        - EnrichmentConfigurationError
        - EnrichmentSummary
        - resolve_contact_email
        - enrich_books
      show_source: true

## Open Library client

::: books_db.open_library
    options:
      members:
        - OpenLibraryLookupError
        - OpenLibraryCandidate
        - OpenLibraryClient
      show_source: true
