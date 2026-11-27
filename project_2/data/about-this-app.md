# About this app

This app answers questions about the text files in its documents folder. It
reads the files once, when the backend starts.

## Retrieval

Documents are split into chunks on blank lines. The chunks are ranked against
your question with BM25, a keyword scoring method, and the best three are
returned as sources. It matches words, not synonyms.
