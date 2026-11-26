"""Document loading, chunking and BM25 ranking."""

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_LENGTH = 800
MAX_RESULTS = 3
K1 = 1.5
B = 0.75
SUFFIXES = (".md", ".txt")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that "
    "the this to was what when where which who why with you".split()
)
# Word characters without the underscore: Unicode letters and digits.
TOKEN = re.compile(r"[^\W_]+")


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
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        chunks.extend(
            Chunk(file=path.name, position=position, text=piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split text on blank lines, attach headings and limit the chunk length."""
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
        chunks.extend(_split("\n".join([*headings, block])))
        headings = []
    # Headings still pending here have no following block and are dropped.
    return chunks


def _split(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_LENGTH:
        cut = next(
            (i for i in range(MAX_CHUNK_LENGTH, 0, -1) if text[i].isspace()), None
        )
        if cut is None:
            pieces.append(text[:MAX_CHUNK_LENGTH])
            text = text[MAX_CHUNK_LENGTH:]
        else:
            pieces.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """An in-memory BM25 index over chunks."""

    def __init__(self, chunks: Iterable[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self.chunks), 1)
        self._frequencies = Counter(token for counts in self._counts for token in counts)

    def search(self, question: str) -> list[Result]:
        """Return the best matching chunks, highest score first."""
        tokens = list(dict.fromkeys(tokenize(question)))
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                results.append(Result(chunk, score))
        results.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return results[:MAX_RESULTS]

    def _term_score(self, token: str, count: int, length: int) -> float:
        total = len(self.chunks)
        containing = self._frequencies[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * count * (K1 + 1) / (count + K1 * norm)
