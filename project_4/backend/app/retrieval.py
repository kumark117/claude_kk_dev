"""Load documents, split them into chunks and rank the chunks with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = {".md", ".txt"}
MAX_CHUNK_CHARS = 800
K1 = 1.5
B = 0.75
TOP_K = 3
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
_TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder or one of its files cannot be read."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Hit:
    file: str
    text: str
    score: float


def load_chunks(docs_dir: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside docs_dir."""
    if not docs_dir.is_dir():
        raise DocumentError(f"Documents folder not found: {docs_dir}")
    try:
        paths = sorted(
            (p for p in docs_dir.iterdir() if p.suffix in SUFFIXES and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentError(f"Cannot read documents folder {docs_dir}: {error}") from error

    chunks: list[Chunk] = []
    for path in paths:
        try:
            # utf-8-sig is UTF-8 that also drops a leading byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        except OSError as error:
            raise DocumentError(f"Cannot read document {path}: {error}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split on blank lines, attach headings to what follows, cap the length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    headings: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if all(line.startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings left over here have no following block, so they are dropped.
    return chunks


def _split_long(text: str) -> list[str]:
    pieces: list[str] = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still gives a piece that fits.
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """An in-memory BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        tokens = [tokenize(chunk.text) for chunk in self.chunks]
        self._counts = [Counter(t) for t in tokens]
        self._lengths = [len(t) for t in tokens]
        self._average_length = sum(self._lengths) / len(tokens) if tokens else 0.0
        # Number of chunks containing each token.
        self._document_frequency = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[Hit]:
        """Return up to TOP_K chunks with a positive score, best first."""
        terms = list(dict.fromkeys(tokenize(question)))
        total = len(self.chunks)
        scored: list[tuple[float, Chunk]] = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for term in terms:
                frequency = counts[term]
                if not frequency:
                    continue
                containing = self._document_frequency[term]
                idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
                norm = 1 - B + B * length / self._average_length
                score += idf * frequency * (K1 + 1) / (frequency + K1 * norm)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].file, item[1].position))
        return [Hit(chunk.file, chunk.text, score) for score, chunk in scored[:TOP_K]]
