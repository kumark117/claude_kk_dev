"""Load local documents, split them into chunks and rank them with BM25."""

import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

K1 = 1.5
B = 0.75
MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
SUFFIXES = (".md", ".txt")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of letters and digits: \w without the underscore.
TOKEN_PATTERN = re.compile(r"[^\W_]+")


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
    """Read the .md and .txt files directly inside docs_dir and chunk them."""
    if not docs_dir.is_dir():
        raise DocumentError(f"Documents folder not found: {docs_dir}")
    paths = sorted(
        (p for p in docs_dir.iterdir() if p.suffix in SUFFIXES and p.is_file()),
        key=lambda p: p.name,
    )
    return [
        Chunk(path.name, position, text)
        for path in paths
        for position, text in enumerate(_split(_read(path)))
    ]


def _read(path: Path) -> str:
    try:
        # utf-8-sig also accepts a byte order mark and removes it.
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentError(f"Document {path} is not valid UTF-8") from error
    except OSError as error:
        raise DocumentError(f"Cannot read document {path}: {error}") from error


def _split(text: str) -> Iterator[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    headings: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        yield from _limit("\n".join([*headings, block]))
        headings = []


def _limit(text: str) -> Iterator[str]:
    """Cut text into pieces of at most MAX_CHUNK_CHARS, preferably at whitespace."""
    while len(text) > MAX_CHUNK_CHARS:
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        yield text[:cut].rstrip()
        text = text[cut:].lstrip()
    if text:
        yield text


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_PATTERN.findall(text.lower()) if t not in STOPWORDS]


class Index:
    """BM25 over chunks held in memory."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        tokens = [tokenize(chunk.text) for chunk in chunks]
        self._counts = [Counter(t) for t in tokens]
        self._lengths = [len(t) for t in tokens]
        self._average = sum(self._lengths) / len(chunks) if chunks else 0.0
        self._containing = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[ScoredChunk]:
        terms = dict.fromkeys(tokenize(question))  # unique, in a stable order
        scored = [
            ScoredChunk(chunk, score)
            for i, chunk in enumerate(self.chunks)
            if (score := self._score(terms, i)) > 0
        ]
        scored.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return scored[:MAX_RESULTS]

    def _score(self, terms: dict[str, None], i: int) -> float:
        total = 0.0
        for term in terms:
            frequency = self._counts[i][term]
            if not frequency:
                continue
            containing = self._containing[term]
            idf = math.log(
                1 + (len(self.chunks) - containing + 0.5) / (containing + 0.5)
            )
            norm = 1 - B + B * self._lengths[i] / self._average
            total += idf * frequency * (K1 + 1) / (frequency + K1 * norm)
        return total
