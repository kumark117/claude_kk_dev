"""Loading, chunking and BM25 ranking of local documents."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
SUFFIXES = (".md", ".txt")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of Unicode letters and digits: "\w" without the underscore.
TOKEN_PATTERN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder cannot be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Result:
    chunk: Chunk
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside the folder, by file name."""
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found or not a directory: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            # "utf-8-sig" is UTF-8 that also drops a leading byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path} ({error})") from error
        chunks.extend(
            Chunk(file=path.name, position=position, text=piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split text on blank lines, attach headings, and cap the chunk length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = []
    headings: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if _is_heading_block(block):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending here have no following block and are dropped.
    return chunks


def _is_heading_block(block: str) -> bool:
    return all(line.lstrip().startswith("#") for line in block.split("\n"))


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still leaves a full-length piece.
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
    return [t for t in TOKEN_PATTERN.findall(text.lower()) if t not in STOPWORDS]


class Index:
    """An in-memory BM25 index over chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(c.text)) for c in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        self._containing = Counter(token for counts in self._counts for token in counts)

    def search(self, question: str) -> list[Result]:
        """Return the best matching chunks, at most MAX_RESULTS, all scoring above 0."""
        # dict.fromkeys keeps the order, so float sums are the same on every run.
        tokens = list(dict.fromkeys(tokenize(question)))
        total = len(self.chunks)
        idf = {
            t: math.log(1 + (total - self._containing[t] + 0.5) / (self._containing[t] + 0.5))
            for t in tokens
        }
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for token in tokens:
                frequency = counts[token]
                if frequency:
                    norm = K1 * (1 - B + B * length / self._average)
                    score += idf[token] * frequency * (K1 + 1) / (frequency + norm)
            if score > 0:
                results.append(Result(chunk=chunk, score=score))
        results.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return results[:MAX_RESULTS]
