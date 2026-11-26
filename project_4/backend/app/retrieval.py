"""Load documents, split them into chunks and rank the chunks with BM25."""

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
    "a an and are as at be by can do does for from how i in is it of on or "
    "that the this to was what when where which who why with you".split()
)
# Word characters without the underscore: runs of letters and digits.
TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents cannot be loaded."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Result(Chunk):
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    folder = Path(folder)
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")
    paths = sorted(
        (p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file()),
        key=lambda p: p.name,
    )
    chunks = []
    for path in paths:
        try:
            # "utf-8-sig" is UTF-8 that also drops a leading byte order mark.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = (block.strip() for block in re.split(r"\n\s*\n", text))

    chunks = []
    headings: list[str] = []
    for block in filter(None, blocks):
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still waiting here have no following block and are dropped.
    return chunks


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        cut = MAX_CHUNK_CHARS
        # Whitespace at index MAX_CHUNK_CHARS still leaves a full-size piece.
        for index in range(MAX_CHUNK_CHARS, 0, -1):
            if text[index].isspace():
                cut = index
                break
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Index:
    """An in-memory BM25 index over chunks."""

    def __init__(self, chunks: list[Chunk]):
        self._chunks = list(chunks)
        self._counts = [Counter(tokenize(c.text)) for c in self._chunks]
        self._lengths = [sum(counts.values()) for counts in self._counts]
        self._average = sum(self._lengths) / len(self._chunks) if self._chunks else 0
        # Number of chunks containing each token.
        self._containing = Counter(t for counts in self._counts for t in counts)

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, question: str) -> list[Result]:
        tokens = list(dict.fromkeys(tokenize(question)))
        results = []
        for chunk, counts, length in zip(self._chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(t, counts[t], length) for t in tokens if t in counts
            )
            if score > 0:
                results.append(Result(chunk.file, chunk.position, chunk.text, score))
        results.sort(key=lambda r: (-r.score, r.file, r.position))
        return results[:MAX_RESULTS]

    def _term_score(self, token: str, frequency: int, length: int) -> float:
        total = len(self._chunks)
        containing = self._containing[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = K1 * (1 - B + B * length / self._average)
        return idf * frequency * (K1 + 1) / (frequency + norm)
