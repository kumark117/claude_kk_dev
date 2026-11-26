"""Document loading, chunking and BM25 ranking. Everything is held in memory."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)

_SUFFIXES = {".md", ".txt"}
_BLANK_LINES = re.compile(r"\n\s*\n")
_TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder or one of its files cannot be read."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Match:
    file: str
    position: int
    text: str
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside the folder."""
    try:
        paths = sorted(
            (p for p in folder.iterdir() if p.suffix in _SUFFIXES and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentError(
            f"Cannot read documents folder {folder}: {error.strerror}"
        ) from error

    chunks: list[Chunk] = []
    for path in paths:
        try:
            # utf-8-sig also accepts a byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        except OSError as error:
            raise DocumentError(
                f"Cannot read document {path}: {error.strerror}"
            ) from error
        chunks.extend(chunk_text(path.name, text))
    return chunks


def chunk_text(file: str, text: str) -> list[Chunk]:
    """Split on blank lines, attach headings to what follows, cap the length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[str] = []
    headings: list[str] = []
    for raw in _BLANK_LINES.split(text):
        block = raw.strip()
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        blocks.append("\n".join([*headings, block]))
        headings = []
    # Headings still pending here have no following block and are dropped.

    pieces = [piece for block in blocks for piece in _split_long(block)]
    return [Chunk(file, position, piece) for position, piece in enumerate(pieces)]


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still leaves a full-size piece.
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Index:
    """BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self._lengths), 1)
        # Number of chunks containing each token.
        self._frequencies = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[Match]:
        """Return the best matching chunks, at most MAX_RESULTS, all above 0."""
        # dict.fromkeys keeps the order, so float sums are reproducible.
        tokens = dict.fromkeys(tokenize(question))
        total = len(self.chunks)
        matches = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for token in tokens:
                f = counts[token]
                if not f:
                    continue
                n = self._frequencies[token]
                idf = math.log(1 + (total - n + 0.5) / (n + 0.5))
                norm = 1 - B + B * length / self._average_length
                score += idf * f * (K1 + 1) / (f + K1 * norm)
            if score > 0:
                matches.append(Match(chunk.file, chunk.position, chunk.text, score))
        matches.sort(key=lambda m: (-m.score, m.file, m.position))
        return matches[:MAX_RESULTS]
