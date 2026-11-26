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


def build(folder, files):
    for name, text in files.items():
        write(folder, name, text)
    return Index(load_chunks(folder))


# --- loading ---


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "charlie")
    write(tmp_path, "d.json", "delta")
    (tmp_path / "nested").mkdir()
    write(tmp_path / "nested", "e.md", "echo")
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


def test_reads_files_as_utf8(tmp_path):
    write(tmp_path, "a.txt", "café naïve")

    assert load_chunks(tmp_path)[0].text == "café naïve"


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_file_instead_of_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_invalid_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


# --- chunking ---


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n   \t \n\n\n\nthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "still one", "", "two"])

    assert chunk_text(text) == ["one\nstill one", "two"]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text") == ["# Title\nBody text"]


def test_consecutive_headings_are_all_joined_to_the_next_block():
    text = "# One\n\n## Two\n### Three\n\nBody\n\nAfter"

    assert chunk_text(text) == ["# One\n## Two\n### Three\nBody", "After"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n# Orphan") == ["Body"]
    assert chunk_text("# Only heading") == []


def test_block_with_heading_and_body_is_not_treated_as_heading_only():
    assert chunk_text("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_chunk_of_exactly_800_characters_is_not_split():
    text = ("abcd " * 160)[:-1] + "e"
    assert len(text) == 800

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = [f"word{i:04d}" for i in range(300)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(0 < len(p) <= 800 for p in pieces)
    assert " ".join(pieces).split() == words


def test_split_piece_may_be_exactly_800_characters():
    first = "a" * 800

    assert chunk_text(first + " tail") == [first, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_word_splits_at_the_whitespace_first():
    assert chunk_text("hi " + "y" * 900) == ["hi", "y" * 800, "y" * 100]


def test_joined_heading_counts_towards_the_length_limit():
    pieces = chunk_text("# Head\n\n" + "z " * 399 + "zz")

    assert len(pieces) == 2
    assert pieces[0].startswith("# Head\nz")
    assert all(len(p) <= 800 for p in pieces)


# --- tokenizing ---


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.5 re-use") == [
        "hello", "world", "snake", "case", "v2", "5", "re", "use",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Café ÜBER naïve 42") == ["café", "über", "naïve", "42"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on "
        "or that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []


def test_tokenize_does_not_stem():
    assert tokenize("cats cat running") == ["cats", "cat", "running"]


# --- ranking ---


def test_relevant_chunk_ranks_first(tmp_path):
    index = build(tmp_path, {
        "cats.md": "Cats purr and sleep all day.",
        "ships.md": "Ships sail across the ocean with cargo.",
        "mixed.md": "A cat once boarded a ship.\n\nThe ocean is deep.",
    })

    results = index.search("How do ships sail the ocean?")

    assert results[0].file == "ships.md"
    assert results[0].text == "Ships sail across the ocean with cargo."


def test_score_follows_the_bm25_formula(tmp_path):
    index = build(tmp_path, {
        "a.txt": "apple apple banana",
        "b.txt": "banana cherry cherry cherry cherry",
    })

    results = index.search("apple apple")

    n_chunks, containing, freq, length, average = 2, 1, 2, 3, 4
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * length / average))
    assert [r.file for r in results] == ["a.txt"]
    assert results[0].score == pytest.approx(expected)


def test_score_sums_over_question_tokens_and_ignores_stopwords_in_length(tmp_path):
    index = build(tmp_path, {
        "a.txt": "the apple and the banana",
        "b.txt": "cherry date",
    })

    results = index.search("apple banana")

    # Both chunks have 2 tokens, so the length factor is 1 and f is 1.
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    assert results[0].score == pytest.approx(2 * idf)


def test_returns_at_most_three_results(tmp_path):
    index = build(tmp_path, {f"{n}.txt": "shared term" for n in "abcde"})

    assert len(index.search("shared")) == 3


def test_ties_are_ordered_by_file_then_position(tmp_path):
    write(tmp_path, "b.txt", "same words")
    write(tmp_path, "a.txt", "same words\n\nsame words")
    chunks = load_chunks(tmp_path)
    index = Index(list(reversed(chunks)))

    results = index.search("same")

    assert [(r.file, r.position) for r in results] == [
        ("a.txt", 0), ("a.txt", 1), ("b.txt", 0),
    ]
    assert len({r.score for r in results}) == 1


def test_only_chunks_with_positive_score_are_returned(tmp_path):
    index = build(tmp_path, {"a.txt": "apple", "b.txt": "banana"})

    results = index.search("apple")

    assert [r.file for r in results] == ["a.txt"]
    assert results[0].score > 0


@pytest.mark.parametrize(
    "question",
    ["zebra", "what is the", "?!... ___", ""],
    ids=["no-match", "stopwords-only", "punctuation-only", "empty"],
)
def test_returns_empty_list_without_a_usable_match(tmp_path, question):
    index = build(tmp_path, {"a.txt": "apple banana"})

    assert index.search(question) == []


def test_empty_folder_returns_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_without_usable_tokens_do_not_break_search(tmp_path):
    index = build(tmp_path, {"a.txt": "the and of"})

    assert index.search("apple") == []


def test_index_reports_chunk_count(tmp_path):
    assert len(build(tmp_path, {"a.txt": "one\n\ntwo"})) == 2
