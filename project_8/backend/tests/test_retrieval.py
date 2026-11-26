import math

import pytest

from app.retrieval import (
    Chunk,
    Index,
    StartupError,
    load_chunks,
    split_chunks,
    tokenize,
)


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def make_index(*texts):
    return Index([Chunk(file=f"{i}.md", position=0, text=t) for i, t in enumerate(texts)])


# Loading


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "ignored")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "d.md", "ignored too")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [("a.md", "alpha"), ("b.txt", "bravo")]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("b.md", 0, "three"),
    ]


def test_reads_files_as_utf8(tmp_path):
    (tmp_path / "a.txt").write_bytes("café — naïve".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café — naïve"


def test_empty_folder_has_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_startup_error(tmp_path):
    with pytest.raises(StartupError, match="nope"):
        load_chunks(tmp_path / "nope")


def test_invalid_utf8_is_a_startup_error_naming_the_file(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(StartupError, match="bad.md"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_and_trims_blocks():
    assert split_chunks("  one  \n\n   \n\n\ttwo\nstill two\n\n\n") == ["one", "two\nstill two"]


def test_normalizes_line_endings():
    assert split_chunks("one\r\n\r\ntwo\rtwo b\r\rthree") == ["one", "two\ntwo b", "three"]


def test_heading_blocks_join_the_next_block():
    text = "# Title\n\n## Sub\n### Subsub\n\nBody.\n\nMore."

    assert split_chunks(text) == ["# Title\n## Sub\n### Subsub\nBody.", "More."]


def test_heading_with_text_in_the_same_block_is_not_joined_forward():
    assert split_chunks("# Title\nBody.\n\nMore.") == ["# Title\nBody.", "More."]


def test_trailing_heading_is_dropped():
    assert split_chunks("Body.\n\n# Orphan") == ["Body."]
    assert split_chunks("# Only") == []


def test_chunk_of_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert split_chunks(text) == [text]


def test_long_chunk_splits_at_whitespace():
    text = " ".join(["abcdefghi"] * 200)  # 1999 characters

    pieces = split_chunks(text)

    assert [len(p) for p in pieces] == [799, 799, 399]
    assert " ".join(pieces) == text


def test_split_uses_whitespace_right_after_the_limit():
    text = "a" * 800 + " tail"

    assert split_chunks(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert [len(p) for p in split_chunks("x" * 1700)] == [800, 800, 100]


def test_heading_is_included_before_splitting():
    pieces = split_chunks("# Head\n\n" + "x" * 800)

    assert pieces == ["# Head", "x" * 800]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! foo_bar-baz v2.5") == [
        "hello",
        "world",
        "foo",
        "bar",
        "baz",
        "v2",
        "5",
    ]


def test_tokenize_keeps_unicode_letters():
    assert tokenize("Éclair über Straße") == ["éclair", "über", "straße"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper() + " kettle") == ["kettle"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = make_index(
        "Bananas are yellow fruit.",
        "The kettle boils water for tea.",
        "Water is wet.",
    )

    results = index.search("How do I boil water in the kettle?")

    assert results[0].file == "1.md"
    assert results[0].text == "The kettle boils water for tea."


def test_score_matches_bm25_formula():
    index = make_index("apple apple banana", "cherry")
    # N=2, n=1, f=2, len=3, avglen=2, k1=1.5, b=0.75
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))

    [result] = index.search("apple")

    assert result.score == pytest.approx(expected)


def test_repeated_question_tokens_count_once():
    index = make_index("apple banana", "cherry")

    assert index.search("apple apple apple")[0].score == index.search("apple")[0].score


def test_stopwords_do_not_count_toward_chunk_length():
    index = make_index("the a of apple", "apple")

    first, second = index.search("apple")

    assert first.score == second.score


def test_returns_at_most_three_results():
    index = make_index(*["apple"] * 5)

    assert len(index.search("apple")) == 3


def test_only_scores_above_zero_are_returned():
    index = make_index("apple", "banana")

    assert [r.file for r in index.search("apple")] == ["0.md"]


def test_ties_order_by_file_then_position():
    chunks = [
        Chunk(file="b.md", position=0, text="apple"),
        Chunk(file="a.md", position=1, text="apple"),
        Chunk(file="a.md", position=0, text="apple"),
        Chunk(file="c.md", position=0, text="apple"),
    ]

    results = Index(chunks).search("apple")

    assert [(r.file, r.position) for r in results] == [("a.md", 0), ("a.md", 1), ("b.md", 0)]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!_", ""])
def test_no_match_or_no_usable_tokens_gives_empty_list(question):
    assert make_index("apple").search(question) == []


def test_empty_index_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_with_no_tokens_do_not_break_search():
    assert make_index("the", "---").search("apple") == []
