"""Load text files, split them into chunks and rank the chunks with BM25."""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".md", ".txt")
MAX_CHUNK = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of word characters, with the underscore treated as a separator.
_TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder cannot be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Hit:
    file: str
    position: int
    text: str
    score: float


def load_chunks(docs_dir: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside docs_dir."""
    if not docs_dir.is_dir():
        raise DocumentError(f"Documents folder not found: {docs_dir}")
    paths = sorted(
        (p for p in docs_dir.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            # "utf-8-sig" also drops a byte order mark, which would hide a heading.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split on blank lines, attach headings, and cap the chunk length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    headings: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    return chunks  # headings still pending have no following block


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK:
        # Last whitespace that leaves a piece of at most MAX_CHUNK characters.
        cut = next(
            (i for i in range(MAX_CHUNK, 0, -1) if text[i].isspace()),
            MAX_CHUNK,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Index:
    """An in-memory BM25 index over chunks."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average = sum(self._lengths) / len(self.chunks) if self.chunks else 0.0
        # For each token, the number of chunks that contain it.
        self._containing = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[Hit]:
        """Return the best matching chunks, best first."""
        total = len(self.chunks)
        idf = {
            # dict keys keep question order, so float sums are reproducible.
            t: math.log(1 + (total - n + 0.5) / (n + 0.5))
            for t in dict.fromkeys(tokenize(question))
            if (n := self._containing[t])
        }
        hits = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            matched = [t for t in idf if t in counts]
            if not matched:
                continue
            norm = K1 * (1 - B + B * length / self._average)
            score = sum(
                idf[t] * counts[t] * (K1 + 1) / (counts[t] + norm) for t in matched
            )
            hits.append(Hit(chunk.file, chunk.position, chunk.text, score))
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.position))
        return hits[:MAX_RESULTS]
