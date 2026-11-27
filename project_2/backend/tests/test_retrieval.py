import math

import pytest

from app.retrieval import (
    DocumentError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def index_of(folder):
    return Index(load_chunks(folder))


# --- loading ---------------------------------------------------------------


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "charlie")
    write(tmp_path, "notes.md.bak", "delta")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "d.md", "echo")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "alpha"),
        ("b.txt", 0, "bravo"),
    ]


def test_each_chunk_keeps_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo\n\nthree")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("a.md", 2, "three"),
    ]


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_invalid_utf8_is_a_clear_error_naming_the_file(tmp_path):
    (tmp_path / "broken.txt").write_bytes(b"ok \xff\xfe not utf-8")

    with pytest.raises(DocumentError, match="broken.txt"):
        load_chunks(tmp_path)


def test_empty_folder_is_allowed_and_search_returns_nothing(tmp_path):
    assert load_chunks(tmp_path) == []
    assert index_of(tmp_path).search("anything") == []


# --- chunking --------------------------------------------------------------


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "  first  \n\n\n   \n\nsecond line\nstill second\n \t \nthird\n"

    assert chunk_text(text) == ["first", "second line\nstill second", "third"]


def test_normalizes_line_endings():
    assert chunk_text("one\r\ntwo\r\n\r\nthree\r\rfour") == [
        "one\ntwo",
        "three",
        "four",
    ]


def test_heading_block_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text.") == ["# Title\nBody text."]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# Title\n\n## Sub\n### Subsub\n\nBody."

    assert chunk_text(text) == ["# Title\n## Sub\n### Subsub\nBody."]


def test_block_with_heading_and_body_is_not_joined_forward():
    assert chunk_text("# Title\nBody.\n\nMore.") == ["# Title\nBody.", "More."]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body.\n\n# Dangling") == ["Body."]
    assert chunk_text("# Only a heading") == []


def test_long_chunk_is_split_at_whitespace():
    text = " ".join(["abcdefghi"] * 200)  # 1999 characters

    pieces = chunk_text(text)

    assert all(len(piece) <= 800 for piece in pieces)
    assert [len(piece) for piece in pieces] == [799, 799, 399]
    assert " ".join(pieces) == text


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_piece_may_end_exactly_at_800_characters():
    first = "a" * 399 + " " + "b" * 400  # whitespace sits at index 800

    assert chunk_text(first + " tail") == [first, "tail"]


def test_single_long_word_is_cut_at_800():
    assert [len(p) for p in chunk_text("x" * 1700)] == [800, 800, 100]


def test_long_word_after_short_word():
    pieces = chunk_text("hi " + "y" * 900)

    assert pieces == ["hi", "y" * 800, "y" * 100]


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.txt", "x" * 900 + "\n\nafter")

    chunks = load_chunks(tmp_path)

    assert [(c.position, len(c.text)) for c in chunks] == [(0, 800), (1, 100), (2, 5)]


# --- tokenizing ------------------------------------------------------------


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, World! snake_case-and.dots 42x") == [
        "hello",
        "world",
        "snake",
        "case",
        "dots",
        "42x",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Café ÜBER naïve ٤٢") == ["café", "über", "naïve", "٤٢"]


def test_tokenize_removes_exactly_the_listed_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("not we") == ["not", "we"]


# --- ranking ---------------------------------------------------------------


def test_relevant_chunk_ranks_first(tmp_path):
    write(tmp_path, "cats.md", "Cats purr and sleep all day.")
    write(tmp_path, "rockets.md", "Rockets burn fuel to reach orbit.")
    write(tmp_path, "bread.md", "Bread dough needs yeast to rise.")

    results = index_of(tmp_path).search("How do rockets reach orbit?")

    assert [r.file for r in results] == ["rockets.md"]
    assert results[0].text == "Rockets burn fuel to reach orbit."
    assert results[0].score > 0


def test_score_follows_bm25_formula(tmp_path):
    write(tmp_path, "a.txt", "apple apple banana")  # len 3
    write(tmp_path, "b.txt", "cherry")  # len 1
    # N = 2, n(apple) = 1, f = 2, len = 3, avglen = 2
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))

    results = index_of(tmp_path).search("apple")

    assert len(results) == 1
    assert results[0].score == pytest.approx(expected)


def test_length_excludes_stopwords(tmp_path):
    write(tmp_path, "a.txt", "the the the the apple")
    write(tmp_path, "b.txt", "cherry")
    # both chunks have length 1 after stopword removal, so avglen = len = 1
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))

    results = index_of(tmp_path).search("apple")

    assert results[0].score == pytest.approx(idf)


def test_repeated_question_tokens_count_once(tmp_path):
    write(tmp_path, "a.txt", "apple banana")
    write(tmp_path, "b.txt", "cherry")
    index = index_of(tmp_path)

    assert index.search("apple apple apple") == index.search("apple")


def test_returns_at_most_three_results_best_first(tmp_path):
    write(tmp_path, "1.txt", "kiwi filler filler filler filler")
    write(tmp_path, "2.txt", "kiwi kiwi kiwi")
    write(tmp_path, "3.txt", "kiwi filler")
    write(tmp_path, "4.txt", "kiwi filler filler filler filler filler filler")
    write(tmp_path, "5.txt", "unrelated")

    results = index_of(tmp_path).search("kiwi")

    assert [r.file for r in results] == ["2.txt", "3.txt", "1.txt"]
    assert results[0].score > results[1].score > results[2].score


def test_ties_are_ordered_by_file_name_then_position():
    from app.retrieval import Chunk

    chunks = [
        Chunk("b.txt", 0, "mango"),
        Chunk("a.txt", 1, "mango"),
        Chunk("a.txt", 0, "mango"),
        Chunk("c.txt", 0, "mango"),
    ]

    results = Index(chunks).search("mango")

    assert [(r.file, r.position) for r in results] == [
        ("a.txt", 0),
        ("a.txt", 1),
        ("b.txt", 0),
    ]


def test_no_match_returns_empty_list(tmp_path):
    write(tmp_path, "a.txt", "apple")

    assert index_of(tmp_path).search("zeppelin") == []


def test_stopword_only_or_symbol_only_question_returns_empty_list(tmp_path):
    write(tmp_path, "a.txt", "what is the apple")
    index = index_of(tmp_path)

    assert index.search("What is the?") == []
    assert index.search("?!_ --") == []


def test_chunks_without_usable_tokens_do_not_break_ranking(tmp_path):
    write(tmp_path, "a.txt", "---\n\nthe and of")

    assert index_of(tmp_path).search("apple") == []


def test_index_reports_chunk_count(tmp_path):
    write(tmp_path, "a.txt", "one\n\ntwo")

    assert len(index_of(tmp_path)) == 2
