"""Loading, chunking and BM25 ranking of the local documents."""

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
# Runs of Unicode letters and digits: \w without the underscore.
TOKEN = re.compile(r"[^\W_]+")
BLANK_LINES = re.compile(r"\n\s*\n")


class DocumentError(Exception):
    """The documents folder or one of its files cannot be used."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Match:
    file: str
    position: int
    text: str
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside the folder."""
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")

    paths = sorted(
        (p for p in folder.iterdir() if p.name.endswith(SUFFIXES) and p.is_file()),
        key=lambda p: p.name,
    )
    chunks: list[Chunk] = []
    for path in paths:
        try:
            # utf-8-sig also drops a byte order mark, which would hide a heading.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document {path} is not valid UTF-8") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split text on blank lines, joining headings and splitting long blocks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    headings: list[str] = []
    for block in BLANK_LINES.split(text):
        block = block.strip()
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        chunks.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still pending here have no following block, so they are dropped.
    return chunks


def _split_long(text: str) -> list[str]:
    pieces: list[str] = []
    while len(text) > MAX_CHUNK_CHARS:
        # The last whitespace that leaves a piece of at most the limit.
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

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self.chunks), 1)
        self._document_frequency = Counter(
            token for counts in self._counts for token in counts
        )

    def search(self, question: str) -> list[Match]:
        """Return the best matching chunks, highest score first."""
        tokens = list(dict.fromkeys(tokenize(question)))
        matches: list[Match] = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                matches.append(Match(chunk.file, chunk.position, chunk.text, score))
        matches.sort(key=lambda m: (-m.score, m.file, m.position))
        return matches[:MAX_RESULTS]

    def _term_score(self, token: str, frequency: int, length: int) -> float:
        total = len(self.chunks)
        containing = self._document_frequency[token]
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        norm = K1 * (1 - B + B * length / self._average_length)
        return idf * frequency * (K1 + 1) / (frequency + norm)
