import math
from pathlib import Path

import pytest

from app.retrieval import (
    BM25Index,
    Chunk,
    DocumentError,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def index_of(*texts: str) -> BM25Index:
    return BM25Index([Chunk("doc.md", i, text) for i, text in enumerate(texts)])


# Loading


def test_loads_only_md_and_txt_sorted_by_file_name(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "charlie")
    write(tmp_path, "d.json", "delta")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "e.md", "echo")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "alpha"),
        ("b.txt", 0, "bravo"),
    ]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo\n\nthree")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("a.md", 2, "three"),
    ]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.txt", "café naïve 東京")

    assert load_chunks(tmp_path)[0].text == "café naïve 東京"


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_file_instead_of_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_bad_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"fine\xff\xfenot")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n\n   \n\t\n\nnext\n\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "next"]


def test_normalizes_line_endings():
    assert chunk_text("a\r\nb\r\n\r\nc\r\rd") == ["a\nb", "c", "d"]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text.") == ["# Title\nBody text."]


def test_consecutive_heading_blocks_all_join_the_next_block():
    text = "# One\n## Two\n\n### Three\n\nBody.\n\nMore."

    assert chunk_text(text) == ["# One\n## Two\n### Three\nBody.", "More."]


def test_heading_with_no_following_block_is_dropped():
    assert chunk_text("Body.\n\n## Trailing") == ["Body."]
    assert chunk_text("# Only") == []


def test_block_with_heading_and_text_is_an_ordinary_block():
    assert chunk_text("# Title\nBody.\n\nMore.") == ["# Title\nBody.", "More."]


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    text = " ".join(["word"] * 500)  # 2499 characters

    pieces = chunk_text(text)

    assert len(pieces) == 4
    assert all(len(piece) <= 800 for piece in pieces)
    assert all(set(piece.split()) == {"word"} for piece in pieces)
    assert " ".join(pieces) == text


def test_split_uses_whitespace_right_after_800_characters():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_word():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_heading_counts_toward_the_chunk_length():
    pieces = chunk_text("# H\n\n" + "y" * 798)

    assert pieces == ["# H", "y" * 798]


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nafter")

    assert [c.position for c in load_chunks(tmp_path)] == [0, 1, 2]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, World! snake_case x-ray v2.5") == [
        "hello",
        "world",
        "snake",
        "case",
        "x",
        "ray",
        "v2",
        "5",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Éclair über 東京 ١٢") == ["éclair", "über", "東京", "١٢"]


def test_tokenize_removes_all_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("What is THE kettle?") == ["kettle"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Bananas are yellow fruit.",
        "The telescope observes distant galaxies.",
        "Bread needs flour and water.",
    )

    results = index.search("Which telescope observes galaxies?")

    assert [r.chunk.position for r in results] == [1]
    assert results[0].score > 0


def test_score_follows_the_bm25_formula():
    index = index_of("apple apple banana", "cherry")

    (result,) = index.search("apple apple")  # question tokens are unique

    n_chunks, containing, f, length, average = 2, 1, 2, 3, 2.0
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = idf * f * 2.5 / (f + 1.5 * (1 - 0.75 + 0.75 * length / average))
    assert result.score == pytest.approx(expected)


def test_score_sums_over_question_tokens():
    index = index_of("apple banana", "cherry")

    both = index.search("apple banana")[0].score

    assert both == pytest.approx(
        index.search("apple")[0].score + index.search("banana")[0].score
    )


def test_returns_at_most_three_results_best_first():
    index = index_of(
        "kiwi one two three four",
        "kiwi kiwi kiwi",
        "kiwi one",
        "kiwi one two",
        "kiwi one two three",
    )

    results = index.search("kiwi")

    assert len(results) == 3
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].chunk.position == 1


def test_ties_are_ordered_by_file_name_then_position():
    chunks = [
        Chunk("b.md", 0, "mango"),
        Chunk("a.md", 1, "mango"),
        Chunk("c.md", 0, "mango"),
        Chunk("a.md", 0, "mango"),
    ]

    results = BM25Index(chunks).search("mango")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


def test_no_match_gives_empty_list():
    assert index_of("apple", "banana").search("zebra") == []


def test_stopword_only_or_blank_question_gives_empty_list():
    index = index_of("what is the answer")

    assert index.search("what is the") == []
    assert index.search("?!") == []


def test_index_with_no_chunks_gives_empty_list(tmp_path):
    assert BM25Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_with_only_stopwords_do_not_break_ranking():
    assert index_of("the of and").search("apple") == []


def test_index_reports_chunk_count():
    assert len(index_of("a", "b", "c")) == 3
