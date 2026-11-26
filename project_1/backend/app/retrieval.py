"""Load documents, split them into chunks and rank the chunks with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".md", ".txt")
MAX_CHUNK_CHARS = 800
TOP_K = 3
K1 = 1.5
B = 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or "
    "that the this to was what when where which who why with you".split()
)

_BLANK_LINES = re.compile(r"\n\s*\n")
# Runs of letters and digits: "\w" without the underscore.
_TOKEN = re.compile(r"[^\W_]+")


class DocumentError(Exception):
    """The documents folder or one of its files cannot be read."""


@dataclass(frozen=True)
class Chunk:
    file: str
    position: int
    text: str


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def load_chunks(folder: Path) -> list[Chunk]:
    """Chunk every .md and .txt file directly inside ``folder``."""
    if not folder.is_dir():
        raise DocumentError(f"Documents folder not found: {folder}")

    try:
        paths = sorted(
            (p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file()),
            key=lambda p: p.name,
        )
    except OSError as error:
        raise DocumentError(f"Cannot read documents folder: {error}") from error

    chunks = []
    for path in paths:
        try:
            # "utf-8-sig" also accepts a leading byte order mark and removes it.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise DocumentError(f"Document is not valid UTF-8: {path}") from error
        except OSError as error:
            raise DocumentError(f"Cannot read document: {error}") from error
        chunks.extend(
            Chunk(path.name, position, piece)
            for position, piece in enumerate(chunk_text(text))
        )
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split on blank lines, keep headings with their text, limit the length."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in _BLANK_LINES.split(text)]

    pieces = []
    headings = []
    for block in blocks:
        if not block:
            continue
        if all(line.lstrip().startswith("#") for line in block.split("\n")):
            headings.append(block)
            continue
        pieces.extend(_split_long("\n".join([*headings, block])))
        headings = []
    # Headings still waiting here have no following block, so they are dropped.
    return pieces


def _split_long(text: str) -> list[str]:
    pieces = []
    while len(text) > MAX_CHUNK_CHARS:
        # The last whitespace that leaves a piece of at most MAX_CHUNK_CHARS.
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


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self._counts = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self._lengths = [counts.total() for counts in self._counts]
        self._average_length = sum(self._lengths) / max(len(self.chunks), 1)
        # In how many chunks each token occurs.
        self._chunk_frequency = Counter(
            token for counts in self._counts for token in counts
        )

    def search(self, question: str) -> list[ScoredChunk]:
        """Return the best matching chunks, at most TOP_K, all scoring above 0."""
        # A dict keeps the order stable, so equal chunks get identical sums.
        tokens = list(dict.fromkeys(tokenize(question)))
        results = []
        for chunk, counts, length in zip(self.chunks, self._counts, self._lengths):
            score = sum(
                self._term_score(token, counts[token], length)
                for token in tokens
                if token in counts
            )
            if score > 0:
                results.append(ScoredChunk(chunk, score))
        results.sort(key=lambda r: (-r.score, r.chunk.file, r.chunk.position))
        return results[:TOP_K]

    def _term_score(self, token: str, frequency: int, length: int) -> float:
        containing = self._chunk_frequency[token]
        idf = math.log(1 + (len(self.chunks) - containing + 0.5) / (containing + 0.5))
        norm = 1 - B + B * length / self._average_length
        return idf * frequency * (K1 + 1) / (frequency + K1 * norm)
