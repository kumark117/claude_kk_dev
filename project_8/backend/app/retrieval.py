"""Load local documents, split them into chunks and rank them with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".md", ".txt")
MAX_CHUNK = 800
K1 = 1.5
B = 0.75
TOP_K = 3
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of letters and digits: \w without the underscore.
TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder cannot be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Result(Chunk):
    score: float


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK:
        # Whitespace at index MAX_CHUNK still leaves a piece of MAX_CHUNK.
        cut = next(
            (i for i in range(MAX_CHUNK, 0, -1) if text[i].isspace()), MAX_CHUNK
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def _is_heading_only(block: str) -> bool:
    return all(line.lstrip().startswith("#") for line in block.split("\n"))


def chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = []
    headings = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if _is_heading_only(block):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending have no following block and are dropped.
    return chunks


def load_chunks(folder: Path) -> list[Chunk]:
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """In-memory BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._counts = [Counter(tokenize(chunk.text)) for chunk in chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average = sum(self._lengths) / len(chunks) if chunks else 0.0
        self._containing = Counter(token for counts in self._counts for token in counts)

    def _idf(self, token: str) -> float:
        n = self._containing[token]
        return math.log(1 + (len(self.chunks) - n + 0.5) / (n + 0.5))

    def search(self, question: str) -> list[Result]:
        tokens = list(dict.fromkeys(tokenize(question)))
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for token in tokens:
                f = counts[token]
                if f:
                    norm = 1 - B + B * length / self._average
                    score += self._idf(token) * f * (K1 + 1) / (f + K1 * norm)
            if score > 0:
                results.append(Result(chunk.file, chunk.position, chunk.text, score))
        results.sort(key=lambda r: (-r.score, r.file, r.position))
        return results[:TOP_K]
