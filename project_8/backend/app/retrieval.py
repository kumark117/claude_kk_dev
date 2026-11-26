"""Document loading, chunking and BM25 ranking."""

import math
import re
from collections import Counter
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
TOKEN = re.compile(r"[^\W_]+")


class StartupError(Exception):
    """The documents could not be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Result:
    file: str
    position: int
    text: str
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    folder = Path(folder)
    if not folder.is_dir():
        raise StartupError(f"Documents folder not found: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise StartupError(f"Document is not valid UTF-8: {path}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(split_chunks(text))
        )
    return chunks


def split_chunks(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = []
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
    return chunks


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still leaves a piece that fits.
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """In-memory BM25 index over a fixed set of chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / len(self.chunks) if self.chunks else 0
        self._document_frequency = Counter(
            token for counts in self._counts for token in counts
        )

    def search(self, question: str) -> list[Result]:
        # Sorted so floating point sums do not depend on set iteration order.
        terms = sorted(set(tokenize(question)))
        total = len(self.chunks)
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for term in terms:
                frequency = counts[term]
                if not frequency:
                    continue
                containing = self._document_frequency[term]
                idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
                norm = K1 * (1 - B + B * length / self._average_length)
                score += idf * frequency * (K1 + 1) / (frequency + norm)
            if score > 0:
                results.append(Result(chunk.file, chunk.position, chunk.text, score))
        results.sort(key=lambda r: (-r.score, r.file, r.position))
        return results[:MAX_RESULTS]
