import math

import pytest

from app import retrieval
from app.retrieval import (
    Chunk,
    DocumentError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def make_index(*texts):
    return Index([Chunk(file=f"{i}.md", position=0, text=t) for i, t in enumerate(texts)])


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    (tmp_path / "b.txt").write_text("bravo", encoding="utf-8")
    (tmp_path / "a.md").write_text("alpha one\n\nalpha two", encoding="utf-8")
    (tmp_path / "c.rst").write_text("ignored", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.md").write_text("nested", encoding="utf-8")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert chunks == [
        Chunk("a.md", 0, "alpha one"),
        Chunk("a.md", 1, "alpha two"),
        Chunk("b.txt", 0, "bravo"),
    ]


def test_reads_utf8_text(tmp_path):
    (tmp_path / "a.md").write_bytes("café ☕".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café ☕"


def test_splits_on_blank_lines_and_normalizes_line_endings():
    text = "  first line\r\nsecond line  \r\n\r\n   \r\nthird\r\rfourth\n"

    assert chunk_text(text) == ["first line\nsecond line", "third", "fourth"]


def test_heading_blocks_join_the_next_block():
    text = "# Title\n\n## Section\n### Sub\n\nBody text.\n\nMore."

    assert chunk_text(text) == ["# Title\n## Section\n### Sub\nBody text.", "More."]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body.\n\n# Dangling") == ["Body."]
    assert chunk_text("# Only heading") == []


def test_block_with_heading_and_text_is_an_ordinary_block():
    assert chunk_text("# Title\ntext\n\nnext") == ["# Title\ntext", "next"]


def test_long_chunk_splits_at_whitespace():
    text = " ".join(["abcdefghi"] * 200)  # 1999 characters

    pieces = chunk_text(text)

    assert [len(p) for p in pieces] == [799, 799, 399]
    assert " ".join(pieces) == text


def test_chunk_of_exactly_800_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_word_ending_exactly_at_the_limit_stays_in_the_first_piece():
    first = "a" * 399 + " " + "b" * 400

    assert chunk_text(first + " tail") == [first, "tail"]


def test_single_long_word_is_cut_at_800():
    assert [len(p) for p in chunk_text("x" * 1700)] == [800, 800, 100]


def test_split_pieces_get_consecutive_positions(tmp_path):
    (tmp_path / "a.md").write_text("x" * 900 + "\n\nend", encoding="utf-8")

    assert [c.position for c in load_chunks(tmp_path)] == [0, 1, 2]


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.5 Über-café") == [
        "hello", "world", "snake", "case", "v2", "5", "über", "café",
    ]


def test_tokenize_removes_exactly_the_listed_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("The cat is not here") == ["cat", "not", "here"]


def test_relevant_chunk_ranks_first():
    index = make_index(
        "Bananas are yellow fruit.",
        "The solar panel converts sunlight into electricity.",
        "Sailing boats use wind.",
    )

    results = index.search("How does a solar panel make electricity?")

    assert results[0].file == "1.md"
    assert results[0].text == "The solar panel converts sunlight into electricity."
    assert results[0].score > 0


def test_score_follows_the_bm25_formula():
    index = make_index("apple apple banana", "cherry")
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))

    results = index.search("apple apple")  # repeated question tokens count once

    assert [r.file for r in results] == ["0.md"]
    assert results[0].score == pytest.approx(expected)


def test_returns_at_most_three_results_ordered_by_score():
    index = make_index(
        "kiwi pear plum fig", "kiwi kiwi kiwi", "kiwi pear", "kiwi", "melon"
    )

    results = index.search("kiwi")

    assert len(results) == 3
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].file == "1.md"


def test_ties_are_ordered_by_file_then_position():
    chunks = [
        Chunk("b.md", 0, "kiwi"),
        Chunk("a.md", 1, "kiwi"),
        Chunk("c.md", 0, "kiwi"),
        Chunk("a.md", 0, "kiwi"),
    ]

    results = Index(chunks).search("kiwi")

    assert [(r.file, r.position) for r in results] == [
        ("a.md", 0), ("a.md", 1), ("b.md", 0),
    ]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!_", ""])
def test_no_match_or_no_usable_tokens_returns_empty(question):
    assert make_index("the apple is red").search(question) == []


def test_empty_folder_is_allowed(tmp_path):
    index = Index(load_chunks(tmp_path))

    assert index.chunks == []
    assert index.search("anything") == []


def test_chunks_made_only_of_stopwords_do_not_break_search():
    assert make_index("the and of").search("apple") == []


def test_missing_folder_is_a_startup_error(tmp_path):
    with pytest.raises(DocumentError, match="nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_invalid_utf8_is_a_startup_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


def test_default_docs_dir_is_data_at_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    root = retrieval.Path(__file__).resolve().parents[2]
    assert retrieval.DEFAULT_DOCS_DIR == root / "data"
