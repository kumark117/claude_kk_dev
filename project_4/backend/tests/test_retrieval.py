import math
from pathlib import Path

import pytest

from app.retrieval import (
    Chunk,
    DocumentError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def index_of(*texts: str) -> Index:
    return Index([Chunk("doc.md", i, text) for i, text in enumerate(texts)])


# --- loading ---


def test_loads_only_md_and_txt_sorted_by_file_name(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "ignored")
    write(tmp_path, "d.markdown", "ignored")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [("a.md", "alpha"), ("b.txt", "bravo")]


def test_does_not_recurse_into_subfolders(tmp_path):
    write(tmp_path, "top.md", "top")
    nested = tmp_path / "nested.md"
    nested.mkdir()
    write(nested, "deep.md", "deep")

    assert [c.file for c in load_chunks(tmp_path)] == ["top.md"]


def test_each_chunk_keeps_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("b.md", 0, "three"),
    ]


def test_reads_utf8_and_ignores_a_byte_order_mark(tmp_path):
    (tmp_path / "a.md").write_bytes("﻿# Café\n\nCrème".encode("utf-8"))

    assert [c.text for c in load_chunks(tmp_path)] == ["# Café\nCrème"]


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_invalid_utf8_is_a_clear_error_naming_the_file(tmp_path):
    (tmp_path / "broken.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="broken.txt.*UTF-8"):
        load_chunks(tmp_path)


# --- chunking ---


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "  first line\nsecond line  \n\n   \n\n\nthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


def test_normalizes_line_endings():
    assert chunk_text("a\r\nb\r\n\r\nc\r\rd") == ["a\nb", "c", "d"]


def test_heading_block_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text.\n\nMore.") == ["# Title\nBody text.", "More."]


def test_consecutive_heading_blocks_all_join_the_next_block():
    text = "# Title\n## Sub\n\n### Deeper\n\nBody."

    assert chunk_text(text) == ["# Title\n## Sub\n### Deeper\nBody."]


def test_block_with_heading_and_body_is_not_joined_forward():
    assert chunk_text("# Title\nBody.\n\nNext.") == ["# Title\nBody.", "Next."]


def test_trailing_heading_with_no_following_block_is_dropped():
    assert chunk_text("Body.\n\n## Orphan") == ["Body."]


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_splits_at_whitespace():
    text = " ".join(["abcdefghi"] * 200)  # 1999 characters

    pieces = chunk_text(text)

    assert len(pieces) == 3
    assert all(len(piece) <= 800 for piece in pieces)
    assert " ".join(pieces) == text


def test_piece_may_end_exactly_at_800_characters():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_a_short_word():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_heading_join_happens_before_length_split():
    pieces = chunk_text("# Title\n\n" + "x" * 800)

    assert pieces == ["# Title", "x" * 800]


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nend")

    assert [c.position for c in load_chunks(tmp_path)] == [0, 1, 2]


# --- tokenizing ---


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case x-ray 42nd") == [
        "hello", "world", "snake", "case", "x", "ray", "42nd",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Éclair naïve Привет ١٢") == ["éclair", "naïve", "привет", "١٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []


def test_tokenize_does_not_stem():
    assert tokenize("cats running") == ["cats", "running"]


# --- ranking ---


def test_most_relevant_chunk_ranks_first():
    index = index_of(
        "Bananas are yellow fruit.",
        "Volcanoes erupt molten lava. Lava is hot.",
        "A volcano is a mountain.",
    )

    results = index.search("How hot is lava?")

    assert results[0].text.startswith("Volcanoes erupt")


def test_score_matches_the_bm25_formula():
    # N = 2, n = 1, f = 2, len = 3, avglen = 2
    index = index_of("lava lava rock", "water")

    [hit] = index.search("lava")

    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    assert hit.score == pytest.approx(expected)
    assert (hit.file, hit.text) == ("doc.md", "lava lava rock")


def test_stopwords_do_not_count_toward_chunk_length():
    index = index_of("the lava is on the rock", "water")

    [hit] = index.search("lava")

    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 1 * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 2 / 1.5))
    assert hit.score == pytest.approx(expected)


def test_repeated_question_tokens_count_once():
    index = index_of("lava rock", "water")

    assert index.search("lava lava lava") == index.search("lava")


def test_returns_at_most_three_results_ordered_by_score():
    index = index_of("lava", "lava lava", "lava lava lava", "lava lava lava lava", "water")

    results = index.search("lava")

    assert len(results) == 3
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_ties_are_ordered_by_file_name_then_position():
    index = Index(
        [
            Chunk("b.md", 0, "lava one"),
            Chunk("a.md", 1, "lava two"),
            Chunk("a.md", 0, "lava three"),
            Chunk("c.md", 0, "lava four"),
        ]
    )

    results = index.search("lava")

    assert [(r.file, r.text) for r in results] == [
        ("a.md", "lava three"),
        ("a.md", "lava two"),
        ("b.md", "lava one"),
    ]


def test_chunks_without_any_question_token_are_excluded():
    index = index_of("lava", "water")

    assert [r.text for r in index.search("lava")] == ["lava"]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!_", ""])
def test_no_usable_match_returns_empty_list(question):
    assert index_of("lava rock", "the water is here").search(question) == []


def test_empty_index_returns_empty_list():
    assert Index([]).search("lava") == []


def test_chunks_made_only_of_stopwords_do_not_break_search():
    assert index_of("the and of").search("lava") == []


def test_empty_folder_searches_to_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("lava") == []
