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
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def index_of(folder, **files):
    for name, text in files.items():
        write(folder, f"{name}.md", text)
    return Index(load_chunks(folder))


# Loading


def test_loads_only_md_and_txt_sorted_by_name(tmp_path):
    write(tmp_path, "c.txt", "charlie")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "b.rst", "ignored")
    write(tmp_path, "d.json", "ignored")
    nested = tmp_path / "nested"
    nested.mkdir()
    write(nested, "e.md", "ignored, no recursion")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [("a.md", "alpha"), ("c.txt", "charlie")]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("b.md", 0, "three"),
    ]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.md", "Crème brûlée — 日本語")

    assert load_chunks(tmp_path)[0].text == "Crème brûlée — 日本語"


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="nope"):
        load_chunks(missing)


def test_a_file_as_documents_folder_is_an_error(tmp_path):
    path = write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(path)


def test_bad_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"ok \xff\xfe not utf-8")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n\n   \n\t\n\n\nthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


def test_normalizes_line_endings():
    assert chunk_text("a\r\nb\r\n\r\nc\r\rd") == ["a\nb", "c", "d"]


def test_heading_joins_the_next_block():
    assert chunk_text("# Title\n\nBody text.\n\nMore.") == ["# Title\nBody text.", "More."]


def test_consecutive_headings_all_join_the_next_block():
    text = "# Title\n\n## Sub\n### Subsub\n\nBody."

    assert chunk_text(text) == ["# Title\n## Sub\n### Subsub\nBody."]


def test_heading_with_no_following_block_is_dropped():
    assert chunk_text("Body.\n\n# Trailing\n\n## Also trailing") == ["Body."]
    assert chunk_text("# Only a heading") == []


def test_block_with_heading_and_text_is_a_normal_chunk():
    assert chunk_text("# Title\nBody.\n\nNext.") == ["# Title\nBody.", "Next."]


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_splits_at_whitespace():
    words = [f"word{i:04d}" for i in range(300)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(len(p) <= 800 for p in pieces)
    assert [w for p in pieces for w in p.split()] == words


def test_split_uses_all_800_characters_when_a_word_ends_there():
    assert chunk_text("a" * 800 + " tail") == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_text():
    assert chunk_text("hi " + "y" * 900) == ["hi", "y" * 800, "y" * 100]


def test_split_pieces_get_their_own_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nend")

    assert [c.position for c in load_chunks(tmp_path)] == [0, 1, 2]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case don't v2.5") == [
        "hello", "world", "snake", "case", "don", "t", "v2", "5",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Über café 東京 ٤٢") == ["über", "café", "東京", "٤٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("Where is the cat?") == ["cat"]


# Ranking


def test_relevant_chunk_ranks_first(tmp_path):
    index = index_of(
        tmp_path,
        cats="Cats purr and chase mice.",
        rockets="Rockets burn fuel to reach orbit.",
        bread="Bread needs flour, water and yeast.",
    )

    results = index.search("How does a rocket reach orbit?")

    assert results[0].chunk.file == "rockets.md"
    assert results[0].score > 0


def test_score_matches_bm25_formula(tmp_path):
    index = index_of(tmp_path, a="apple apple banana", b="cherry")

    [result] = index.search("apple apple")

    n_chunks, containing, f, length, average = 2, 1, 2, 3, 2.0
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = idf * f * 2.5 / (f + 1.5 * (1 - 0.75 + 0.75 * length / average))
    assert result.score == pytest.approx(expected)


def test_chunk_length_ignores_stopwords(tmp_path):
    index = index_of(tmp_path, a="the the the the apple", b="apple")

    first, second = index.search("apple")

    assert first.score == pytest.approx(second.score)


def test_returns_at_most_three_results(tmp_path):
    index = index_of(tmp_path, **{f"f{i}": "apple pie" for i in range(6)})

    assert len(index.search("apple")) == 3


def test_ties_order_by_file_then_position(tmp_path):
    index = index_of(tmp_path, b="apple\n\napple", a="pear\n\napple", c="apple")

    results = index.search("apple")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 1), ("b.md", 0), ("b.md", 1),
    ]


def test_higher_score_comes_first(tmp_path):
    index = index_of(tmp_path, a="apple kiwi plum fig", b="apple apple apple apple")

    results = index.search("apple")

    assert [r.chunk.file for r in results] == ["b.md", "a.md"]
    assert results[0].score > results[1].score


def test_no_match_returns_empty_list(tmp_path):
    assert index_of(tmp_path, a="apple").search("zebra") == []


@pytest.mark.parametrize("question", ["What is the?", "?!...", ""])
def test_question_without_usable_tokens_returns_empty_list(tmp_path, question):
    assert index_of(tmp_path, a="what is the apple").search(question) == []


def test_empty_folder_search_returns_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_made_only_of_stopwords_do_not_break_search(tmp_path):
    assert index_of(tmp_path, a="the and of").search("apple") == []
