"""Load text files, split them into chunks and rank the chunks with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

MAX_CHUNK_CHARS = 800
SUFFIXES = (".md", ".txt")
K1 = 1.5
B = 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of letters and digits: word characters without the underscore.
TOKEN = re.compile(r"[^\W_]+")


class DocumentsError(Exception):
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
    """Chunk every .md and .txt file directly inside `folder`, in name order."""
    if not folder.is_dir():
        raise DocumentsError(f"Documents folder not found: {folder}")
    try:
        paths = sorted(
            (p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentsError(f"Cannot read documents folder: {folder}") from error

    chunks = []
    for path in paths:
        try:
            # utf-8-sig drops a byte order mark, which would hide a first heading.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentsError(f"Document is not valid UTF-8: {path}") from error
        except OSError as error:
            raise DocumentsError(f"Cannot read document: {path}") from error
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
    # Headings still pending here have no following block and are dropped.
    return chunks


def _blocks(text: str) -> list[str]:
    """Split on blank lines; each block is trimmed and non-empty."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [
        "\n".join(group).strip()
        for has_text, group in groupby(lines, key=lambda line: bool(line.strip()))
        if has_text
    ]


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # The whitespace may sit just past the limit, hence the + 1.
        spaces = list(re.finditer(r"\s+", text[: MAX_CHUNK_CHARS + 1]))
        cut = spaces[-1].start() if spaces else MAX_CHUNK_CHARS
        pieces.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """BM25 index over chunks held in memory."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self.chunks), 1)
        # Number of chunks containing each token.
        self._frequencies = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str, limit: int = 3) -> list[Hit]:
        """Return the best chunks with a score above 0, best first."""
        tokens = list(dict.fromkeys(tokenize(question)))
        hits = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                hits.append(Hit(chunk.file, chunk.position, chunk.text, score))
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.position))
        return hits[:limit]

    def _term_score(self, token: str, count: int, length: int) -> float:
        total = len(self.chunks)
        containing = self._frequencies[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * count * (K1 + 1) / (count + K1 * norm)
