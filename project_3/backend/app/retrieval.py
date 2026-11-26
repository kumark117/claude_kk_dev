"""Load text files, split them into chunks and rank them with BM25."""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
SUFFIXES = (".md", ".txt")
MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents could not be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class ScoredChunk(Chunk):
    score: float


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


def _is_heading_only(block: str) -> bool:
    return all(line.startswith("#") for line in block.split("\n"))


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
    pieces.append(text)
    return pieces


def chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    headings: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if _is_heading_only(block):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    return chunks


def load_chunks(folder: Path) -> list[Chunk]:
    """Read the .md and .txt files directly inside folder, sorted by name."""
    try:
        paths = sorted(
            (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentError(
            f"Cannot read documents folder {folder}: {error.strerror}"
        ) from error

    chunks = []
    for path in paths:
        try:
            # utf-8-sig also accepts files that start with a byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        except OSError as error:
            raise DocumentError(f"Cannot read document {path}: {error.strerror}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


class Index:
    """An in-memory BM25 index over chunks."""

    def __init__(self, chunks: Sequence[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(c.text)) for c in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / len(self.chunks) if self.chunks else 0.0
        # Number of chunks containing each token.
        self._containing = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[ScoredChunk]:
        """Return the best matching chunks, highest score first."""
        tokens = [t for t in dict.fromkeys(tokenize(question)) if t in self._containing]
        total = len(self.chunks)
        idf = {
            t: math.log(1 + (total - self._containing[t] + 0.5) / (self._containing[t] + 0.5))
            for t in tokens
        }
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for t in tokens:
                f = counts[t]
                if f:
                    norm = K1 * (1 - B + B * length / self._average_length)
                    score += idf[t] * f * (K1 + 1) / (f + norm)
            if score > 0:
                results.append(ScoredChunk(chunk.file, chunk.position, chunk.text, score))
        results.sort(key=lambda r: (-r.score, r.file, r.position))
        return results[:MAX_RESULTS]
