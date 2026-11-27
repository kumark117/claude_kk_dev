"""Load local documents, split them into chunks and rank them with BM25."""

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
TOKEN = re.compile(r"[^\W_]+")
BLANK_LINES = re.compile(r"\n\s*\n")


class DocsError(Exception):
    """The documents folder or one of its files cannot be used."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class Match:
    file: str
    text: str
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside the folder."""
    if not folder.is_dir():
        raise DocsError(f"Documents folder not found: {folder}")
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
            raise DocsError(f"Document {path} is not valid UTF-8") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split on blank lines, attach headings to what follows, limit the length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pieces = []
    headings: list[str] = []
    for block in BLANK_LINES.split(text):
        block = block.strip()
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        pieces.extend(_split_long("\n".join([*headings, block])))
        headings = []
    return pieces


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # Whitespace at index MAX_CHUNK_CHARS still leaves a piece of that length.
        cut = next(
            (i for i in range(MAX_CHUNK_CHARS, 0, -1) if text[i].isspace()),
            MAX_CHUNK_CHARS,
        )
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    pieces.append(text)
    return pieces


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS]


class Index:
    """An in-memory BM25 index over a fixed set of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        tokens = [tokenize(chunk.text) for chunk in self.chunks]
        self._counts = [Counter(words) for words in tokens]
        self._lengths = [len(words) for words in tokens]
        self._average_length = sum(self._lengths) / len(tokens) if tokens else 0.0
        # In how many chunks each token occurs.
        self._document_frequency = Counter(
            token for counts in self._counts for token in counts
        )

    def search(self, question: str) -> list[Match]:
        """Return the best matching chunks, at most MAX_RESULTS of them."""
        total = len(self.chunks)
        idf = {}
        for token in dict.fromkeys(tokenize(question)):
            n = self._document_frequency[token]
            if n:
                idf[token] = math.log(1 + (total - n + 0.5) / (n + 0.5))

        scored = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = 0.0
            for token, weight in idf.items():
                f = counts[token]
                if f:
                    norm = 1 - B + B * length / self._average_length
                    score += weight * f * (K1 + 1) / (f + K1 * norm)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda item: (-item[0], item[1].file, item[1].position))
        return [
            Match(chunk.file, chunk.text, score)
            for score, chunk in scored[:MAX_RESULTS]
        ]
