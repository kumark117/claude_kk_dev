import math
from pathlib import Path

import pytest

from app.retrieval import (
    Chunk,
    DocumentError,
    Index,
    load_chunks,
    split_chunks,
    tokenize,
)


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


# Loading


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "ignored")
    write(tmp_path, "notes.md.bak", "ignored")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "d.md", "nested")
    (tmp_path / "folder.md").mkdir()

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "alpha"),
        Chunk("b.txt", 0, "bravo"),
    ]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("b.md", 0, "three"),
    ]


def test_reads_files_as_utf8(tmp_path):
    (tmp_path / "a.md").write_bytes("café naïve".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café naïve"


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_invalid_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\x00 not utf-8")

    with pytest.raises(DocumentError, match="bad.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n   \n\t\n\nnext\n\n\n"

    assert split_chunks(text) == ["first line\nsecond line", "next"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "still one", "", "two"])

    assert split_chunks(text) == ["one\nstill one", "two"]


def test_heading_block_is_joined_to_the_next_block():
    assert split_chunks("# Title\n\nBody\n\nMore") == ["# Title\nBody", "More"]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# Title\n## Sub\n\n### Deeper\n\nBody"

    assert split_chunks(text) == ["# Title\n## Sub\n### Deeper\nBody"]


def test_block_with_heading_and_text_stays_on_its_own():
    text = "# Title\ntext\n\nnext"

    assert split_chunks(text) == ["# Title\ntext", "next"]


def test_heading_with_no_following_block_is_dropped():
    assert split_chunks("Body\n\n# Trailing\n\n## Also trailing\n") == ["Body"]


def test_long_chunk_is_split_at_whitespace():
    words = ["abcdefghi"] * 200  # 1999 characters when joined
    pieces = split_chunks(" ".join(words))

    assert [len(piece) for piece in pieces] == [799, 799, 399]
    assert " ".join(pieces).split() == words


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert split_chunks(text) == [text]


def test_split_uses_whitespace_right_after_the_limit():
    text = "a" * 800 + " tail"

    assert split_chunks(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert split_chunks("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_a_short_one():
    assert split_chunks("hi " + "y" * 900) == ["hi", "y" * 800, "y" * 100]


def test_joined_heading_counts_towards_the_length_limit():
    pieces = split_chunks("# Head\n\n" + "word " * 200)

    assert pieces[0].startswith("# Head\nword")
    assert len(pieces) == 2
    assert all(len(piece) <= 800 for piece in pieces)


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nlast")

    assert [(c.position, len(c.text)) for c in load_chunks(tmp_path)] == [
        (0, 800),
        (1, 100),
        (2, 4),
    ]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, World! snake_case v2.5 e-mail") == [
        "hello",
        "world",
        "snake",
        "case",
        "v2",
        "5",
        "e",
        "mail",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Éclair ÜBER 東京 ٤٢") == ["éclair", "über", "東京", "٤٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("What is the Moon?") == ["moon"]


def test_tokenize_does_not_stem():
    assert tokenize("cats running") == ["cats", "running"]


# Ranking


def index_of(*texts: str) -> Index:
    return Index([Chunk("doc.md", i, text) for i, text in enumerate(texts)])


def test_most_relevant_chunk_ranks_first():
    index = index_of(
        "Bananas are yellow fruit.",
        "Penguins live in Antarctica. Penguins swim well.",
        "One penguins mention among many many other unrelated words here.",
    )

    hits = index.search("Where do penguins live?")

    assert [hit.text for hit in hits] == [
        "Penguins live in Antarctica. Penguins swim well.",
        "One penguins mention among many many other unrelated words here.",
    ]
    assert hits[0].score > hits[1].score > 0
    assert hits[0].file == "doc.md"


def test_score_follows_the_bm25_formula():
    index = index_of("apple apple banana", "cherry")
    # N=2, n=1, f=2, len=3, avglen=2
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))

    [hit] = index.search("apple")

    assert hit.score == pytest.approx(expected)


def test_length_excludes_stopwords():
    index = index_of("the the the apple", "apple")

    first, second = index.search("apple")

    assert first.score == pytest.approx(second.score)


def test_repeated_question_tokens_count_once():
    index = index_of("apple banana", "cherry")

    assert index.search("apple apple apple") == index.search("apple")


def test_returns_at_most_three_results():
    index = index_of(*["apple"] * 5)

    assert len(index.search("apple")) == 3


def test_ties_are_ordered_by_file_name_then_position():
    index = Index(
        [
            Chunk("b.md", 0, "apple"),
            Chunk("a.md", 1, "apple"),
            Chunk("c.md", 0, "apple"),
            Chunk("a.md", 0, "apple"),
        ]
    )

    hits = index.search("apple")

    assert [(hit.file, hit.position) for hit in hits] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!...", ""])
def test_no_usable_match_returns_empty_list(question):
    assert index_of("apple banana", "the cherry").search(question) == []


def test_no_chunks_returns_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_without_usable_tokens_are_harmless():
    assert index_of("the", "---").search("apple") == []


def test_index_reports_chunk_count():
    assert index_of("a", "b", "c").size == 3
