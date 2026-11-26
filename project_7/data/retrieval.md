# How retrieval works

Each document is split into chunks on blank lines. A heading is kept together
with the text that follows it.

## Ranking

Chunks are ranked with BM25, a keyword scoring method. The three best matching
chunks are returned as sources. Synonyms are not matched.
