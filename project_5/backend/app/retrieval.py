"""Load local documents, split them into chunks and rank them with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".md", ".txt")
MAX_CHUNK_CHARS = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Runs of letters and digits: \w without the underscore.
TOKEN = re.compile(r"[^\W_]+")
BLANK_LINES = re.compile(r"\n\s*\n")


class DocumentError(Exception):
    """The documents folder or one of its files cannot be read."""


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
    """Chunk every .md and .txt file directly inside the folder."""
    folder = Path(folder)
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")
    try:
        paths = sorted(
            (
                path
                for path in folder.iterdir()
                if path.name.endswith(SUFFIXES) and path.is_file()
            ),
            key=lambda path: path.name,
        )
    except OSError as error:
        raise DocumentError(f"Cannot read documents folder: {error}") from error
    return [
        Chunk(path.name, position, text)
        for path in paths
        for position, text in enumerate(chunk_text(_read(path)))
    ]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentError(f"Document {path} is not valid UTF-8") from error
    except OSError as error:
        raise DocumentError(f"Cannot read document: {error}") from error


def chunk_text(text: str) -> list[str]:
    """Split on blank lines, keeping headings with the block that follows."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = []
    headings = []
    for block in BLANK_LINES.split(text):
        block = block.strip()
        if not block:
            continue
        if all(line.startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending have no following block and are dropped.
    return chunks


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        cut = MAX_CHUNK_CHARS
        # Whitespace at index MAX_CHUNK_CHARS still leaves a piece that fits.
        for index in range(MAX_CHUNK_CHARS, 0, -1):
            if text[index].isspace():
                cut = index
                break
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """An in-memory BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]):
        self._entries = []
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            self._entries.append((chunk, Counter(tokens), len(tokens)))
        total = sum(length for _, _, length in self._entries)
        self._average_length = total / len(chunks) if chunks else 0.0
        self._containing = Counter(
            token for _, counts, _ in self._entries for token in counts
        )

    def search(self, question: str) -> list[Result]:
        """Return the best matching chunks, at most MAX_RESULTS of them."""
        # dict.fromkeys keeps question order, so float sums are repeatable.
        terms = dict.fromkeys(tokenize(question))
        results = []
        for chunk, counts, length in self._entries:
            score = sum(
                self._term_score(term, counts[term], length)
                for term in terms
                if term in counts
            )
            if score > 0:
                results.append(Result(chunk, score))
        results.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return results[:MAX_RESULTS]

    def _term_score(self, term: str, frequency: int, length: int) -> float:
        total = len(self._entries)
        containing = self._containing[term]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * frequency * (K1 + 1) / (frequency + K1 * norm)
