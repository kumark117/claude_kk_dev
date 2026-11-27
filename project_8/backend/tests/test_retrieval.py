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


def index_of(folder, files):
    for name, text in files.items():
        write(folder, name, text)
    return Index(load_chunks(folder))


# Loading


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "charlie")
    write(tmp_path, "d.json", "delta")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "e.md", "echo")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [("a.md", "alpha"), ("b.txt", "bravo")]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.txt", "Zürich café")

    assert load_chunks(tmp_path)[0].text == "Zürich café"


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("b.md", 0, "three"),
    ]


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocumentError, match="nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_invalid_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n   \n\n\nthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["a", "b", "", "c"])

    assert chunk_text(text) == ["a\nb", "c"]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text") == ["# Title\nBody text"]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# Title\n## Sub\n\n### Deeper\n\nBody\n\nNext"

    assert chunk_text(text) == ["# Title\n## Sub\n### Deeper\nBody", "Next"]


def test_block_with_heading_and_body_lines_is_not_treated_as_heading():
    assert chunk_text("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n## Orphan") == ["Body"]
    assert chunk_text("# Only heading") == []


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = ["word%04d" % i for i in range(300)]  # 8 characters each
    text = " ".join(words)

    pieces = chunk_text(text)

    assert len(pieces) > 1
    assert all(len(p) <= 800 for p in pieces)
    assert " ".join(pieces).split() == words
    # 89 words take 800 characters, so the split is greedy.
    assert len(pieces[0]) == 800


def test_split_happens_at_any_whitespace():
    text = "a" * 500 + "\n" + "b" * 500

    assert chunk_text(text) == ["a" * 500, "b" * 500]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_a_short_one():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_split_pieces_get_their_own_positions(tmp_path):
    write(tmp_path, "a.txt", "x" * 900 + "\n\ntail")

    chunks = load_chunks(tmp_path)

    assert [c.position for c in chunks] == [0, 1, 2]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, snake_case WORLD-wide 42x!") == [
        "hello",
        "snake",
        "case",
        "world",
        "wide",
        "42x",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Zürich Ünïcode ٣") == ["zürich", "ünïcode", "٣"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("What is the moon?") == ["moon"]


# Ranking


def test_relevant_chunk_ranks_first(tmp_path):
    index = index_of(
        tmp_path,
        {
            "cats.md": "Cats purr and chase mice.",
            "rockets.md": "Rockets burn fuel to reach orbit.",
            "soup.md": "Soup simmers slowly on the stove.",
        },
    )

    results = index.search("How do rockets reach orbit?")

    assert results[0].file == "rockets.md"
    assert results[0].text == "Rockets burn fuel to reach orbit."
    assert [r.file for r in results] == ["rockets.md"]


def test_score_matches_the_bm25_formula(tmp_path):
    index = index_of(
        tmp_path,
        {"a.txt": "apple apple banana", "b.txt": "cherry"},
    )

    # N = 2, n = 1, f = 2, len = 3, avglen = 2. Repeated and stopword question
    # tokens do not count twice.
    results = index.search("the apple, apple?")

    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    assert results[0].score == pytest.approx(expected)


def test_score_sums_over_question_tokens(tmp_path):
    index = index_of(tmp_path, {"a.txt": "apple banana", "b.txt": "cherry date"})

    both = index.search("apple banana")[0].score
    one = index.search("apple")[0].score

    assert both == pytest.approx(2 * one)


def test_returns_at_most_three_results_best_first(tmp_path):
    index = index_of(
        tmp_path,
        {
            "a.txt": "kiwi one two three four",
            "b.txt": "kiwi kiwi kiwi",
            "c.txt": "kiwi kiwi one",
            "d.txt": "kiwi one two three four five six",
        },
    )

    results = index.search("kiwi")

    assert [r.file for r in results] == ["b.txt", "c.txt", "a.txt"]
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_ties_are_ordered_by_file_name_then_position(tmp_path):
    index = index_of(
        tmp_path,
        {"b.txt": "mango", "a.txt": "mango\n\nmango", "c.txt": "mango"},
    )

    results = index.search("mango")

    assert [(r.file, r.position) for r in results] == [
        ("a.txt", 0),
        ("a.txt", 1),
        ("b.txt", 0),
    ]


def test_no_match_returns_empty_list(tmp_path):
    index = index_of(tmp_path, {"a.txt": "apple"})

    assert index.search("zebra") == []


def test_stopword_only_question_returns_empty_list(tmp_path):
    index = index_of(tmp_path, {"a.txt": "what is the apple"})

    assert index.search("What is the?") == []
    assert index.search("?!") == []


def test_empty_folder_returns_empty_list(tmp_path):
    index = Index(load_chunks(tmp_path))

    assert index.search("apple") == []


def test_chunks_without_usable_tokens_return_empty_list(tmp_path):
    index = index_of(tmp_path, {"a.txt": "the and of"})

    assert index.search("apple") == []
