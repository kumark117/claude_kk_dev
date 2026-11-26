# How retrieval works

Documents are split into chunks on blank lines. Retrieval ranks the chunks with BM25, a classic keyword scoring formula, and returns at most three of them.

## Limits

Keyword search does not match synonyms. Use words that appear in the documents.
