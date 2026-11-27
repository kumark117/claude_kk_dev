# Retrieval

Retrieval uses BM25, a keyword ranking method. Each document is split into
chunks on blank lines, and the three best matching chunks are returned as
sources.

## Limitations

Keyword search does not understand synonyms: a question about "cars" will not
find a chunk that only mentions "automobiles".
