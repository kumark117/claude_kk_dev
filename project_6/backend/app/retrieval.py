"""Document loading, chunking and BM25 ranking."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".md", ".txt")
MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of Unicode letters and digits: "\w" without the underscore.
TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder cannot be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def load_chunks(docs_dir: Path) -> list[Chunk]:
    """Read the *.md and *.txt files directly inside docs_dir, sorted by name."""
    if not docs_dir.is_dir():
        raise DocumentError(f"Documents folder not found: {docs_dir}")
    paths = sorted(
        (p for p in docs_dir.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    chunks = []
    headings: list[str] = []
    for block in _blocks(text):
        if all(line.startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending have no following block, so they are dropped.
    return chunks


def _blocks(text: str) -> list[str]:
    """Split on blank lines; return trimmed, non-empty blocks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = (block.strip() for block in re.split(r"\n\s*\n", normalized))
    return [block for block in blocks if block]


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # The last whitespace that leaves a piece of at most MAX_CHUNK_CHARS.
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class BM25Index:
    """An in-memory BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self._chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self._chunks), 1)
        self._containing = Counter(token for counts in self._counts for token in counts)

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, question: str) -> list[ScoredChunk]:
        tokens = list(dict.fromkeys(tokenize(question)))
        results = []
        for chunk, counts, length in zip(self._chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                results.append(ScoredChunk(chunk, score))
        results.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return results[:MAX_RESULTS]

    def _term_score(self, token: str, frequency: int, length: int) -> float:
        total = len(self._chunks)
        containing = self._containing[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * frequency * (K1 + 1) / (frequency + K1 * norm)
