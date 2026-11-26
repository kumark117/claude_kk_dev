"""Load local documents, split them into chunks and rank them with BM25."""

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
SUFFIXES = {".md", ".txt"}
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Word characters without the underscore: Unicode letters and digits.
TOKEN = re.compile(r"[^\W_]+")


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


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside the folder, by file name."""
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file()),
        key=lambda p: p.name,
    )
    chunks: list[Chunk] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(split_chunks(text))
        )
    return chunks


def split_chunks(text: str) -> list[str]:
    """Split on blank lines, attach headings to what follows, cap the length."""
    chunks: list[str] = []
    headings: list[str] = []
    for block in _blocks(text):
        if all(line.startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending here have no following block and are dropped.
    return chunks


def _blocks(text: str) -> Iterable[str]:
    lines: list[str] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in [*normalized.split("\n"), ""]:
        if line.strip():
            lines.append(line)
        elif lines:
            yield "\n".join(lines).strip()
            lines = []


def _split_long(text: str) -> list[str]:
    pieces: list[str] = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still leaves a piece that fits.
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


class Index:
    """An in-memory BM25 index over a fixed set of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self._chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._containing = Counter(token for counts in self._counts for token in counts)
        self._average_length = sum(self._lengths) / max(len(self._chunks), 1)

    @property
    def size(self) -> int:
        return len(self._chunks)

    def search(self, question: str) -> list[Hit]:
        tokens = dict.fromkeys(tokenize(question))  # unique, in a stable order
        idf = {
            token: math.log(1 + (self.size - n + 0.5) / (n + 0.5))
            for token in tokens
            if (n := self._containing[token])
        }
        hits: list[Hit] = []
        for chunk, counts, length in zip(self._chunks, self._counts, self._lengths):
            score = sum(
                weight
                * counts[token]
                * (K1 + 1)
                / (counts[token] + K1 * (1 - B + B * length / self._average_length))
                for token, weight in idf.items()
                if counts[token]
            )
            if score > 0:
                hits.append(Hit(chunk.file, chunk.position, chunk.text, score))
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.position))
        return hits[:MAX_RESULTS]
