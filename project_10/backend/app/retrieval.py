"""Document loading, chunking and BM25 ranking. Plain Python, in memory."""

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
SUFFIXES = (".md", ".txt")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)


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


def load_chunks(folder: Path) -> list[Chunk]:
    """Read the *.md and *.txt files directly inside folder and chunk them."""
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found or not a folder: {folder}")
    try:
        paths = sorted(
            (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentError(f"Cannot list documents folder {folder}: {error}") from error

    chunks = []
    for path in paths:
        try:
            # utf-8-sig is UTF-8 that also drops a leading byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        except OSError as error:
            raise DocumentError(f"Cannot read document {path}: {error}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
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
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending here have no following block and are dropped.
    return chunks


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # A break at index MAX_CHUNK_CHARS still leaves a piece that fits.
        breaks = re.finditer(r"\s", text[: MAX_CHUNK_CHARS + 1])
        cut = max((match.start() for match in breaks), default=MAX_CHUNK_CHARS)
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    """Lowercase runs of Unicode letters and digits, without stopwords."""
    return [t for t in re.findall(r"[^\W_]+", text.lower()) if t not in STOPWORDS]


class Index:
    """BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: Iterable[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self.chunks), 1)
        self._containing = Counter(t for counts in self._counts for t in counts)

    def search(self, question: str) -> list[Hit]:
        """Return the best chunks with a score above 0, best first."""
        tokens = list(dict.fromkeys(tokenize(question)))
        scored = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].file, item[1].position))
        return [
            Hit(chunk.file, chunk.text, score)
            for score, chunk in scored[:MAX_RESULTS]
        ]

    def _term_score(self, token: str, frequency: int, length: int) -> float:
        total = len(self.chunks)
        containing = self._containing[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * frequency * (K1 + 1) / (frequency + K1 * norm)
