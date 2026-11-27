import math

import pytest

from app.retrieval import (
    Chunk,
    DocumentError,
    Index,
    load_chunks,
    tokenize,
)


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8", newline="")


def texts(chunks):
    return [chunk.text for chunk in chunks]


def index_of(*chunk_texts):
    return Index([Chunk("doc.md", i, text) for i, text in enumerate(chunk_texts)])


# Loading


def test_reads_only_md_and_txt_files_sorted_by_name(tmp_path):
    write(tmp_path, "c.txt", "third")
    write(tmp_path, "a.md", "first")
    write(tmp_path, "b.txt", "second")
    write(tmp_path, "d.rst", "ignored")
    write(tmp_path, "e.json", "ignored")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [
        ("a.md", "first"),
        ("b.txt", "second"),
        ("c.txt", "third"),
    ]


def test_does_not_recurse_into_subfolders(tmp_path):
    write(tmp_path, "top.md", "top")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "nested.md", "nested")
    (tmp_path / "folder.md").mkdir()

    assert texts(load_chunks(tmp_path)) == ["top"]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.md", "Crème brûlée")

    assert texts(load_chunks(tmp_path)) == ["Crème brûlée"]


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"

    with pytest.raises(DocumentError, match="Documents folder not found.*nope"):
        load_chunks(missing)


def test_invalid_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"ok \xff\xfe broken")

    with pytest.raises(DocumentError, match="bad.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks(tmp_path):
    write(tmp_path, "a.md", "\n\n  one  \nstill one\n   \t\ntwo\n\n\n\nthree\n\n")

    assert texts(load_chunks(tmp_path)) == ["one  \nstill one", "two", "three"]


def test_normalizes_line_endings(tmp_path):
    write(tmp_path, "a.md", "one\r\nmore\r\n\r\ntwo\r\rthree")

    assert texts(load_chunks(tmp_path)) == ["one\nmore", "two", "three"]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("b.md", 0, "three"),
    ]


def test_heading_blocks_join_the_next_block(tmp_path):
    write(tmp_path, "a.md", "# Title\n\n## Part\n### Sub\n\nBody text\n\nMore text")

    assert texts(load_chunks(tmp_path)) == [
        "# Title\n## Part\n### Sub\nBody text",
        "More text",
    ]


def test_heading_with_text_in_the_same_block_is_left_alone(tmp_path):
    write(tmp_path, "a.md", "# Title\ntext\n\nnext")

    assert texts(load_chunks(tmp_path)) == ["# Title\ntext", "next"]


def test_heading_without_a_following_block_is_dropped(tmp_path):
    write(tmp_path, "a.md", "Body\n\n# Dangling")
    write(tmp_path, "b.md", "Other file")

    assert texts(load_chunks(tmp_path)) == ["Body", "Other file"]


def test_chunk_of_800_characters_is_not_split(tmp_path):
    block = "a" * 399 + " " + "b" * 400
    write(tmp_path, "a.md", block)

    assert texts(load_chunks(tmp_path)) == [block]


def test_long_chunk_splits_at_whitespace(tmp_path):
    words = " ".join(["abcdefghi"] * 200)  # 1999 characters
    write(tmp_path, "a.md", words)

    chunks = load_chunks(tmp_path)

    assert len(chunks) == 3
    assert all(len(c.text) <= 800 for c in chunks)
    assert " ".join(texts(chunks)) == words
    assert [c.position for c in chunks] == [0, 1, 2]


def test_split_may_use_whitespace_right_after_800_characters(tmp_path):
    write(tmp_path, "a.md", "a" * 800 + " tail")

    assert texts(load_chunks(tmp_path)) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800(tmp_path):
    write(tmp_path, "a.md", "x" * 1700)

    assert [len(t) for t in texts(load_chunks(tmp_path))] == [800, 800, 100]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD_wide web-site v2.0! Café 42") == [
        "hello",
        "world",
        "wide",
        "web",
        "site",
        "v2",
        "0",
        "café",
        "42",
    ]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("How does the engine work?") == ["engine", "work"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Bicycles have two wheels and a chain.",
        "Honey bees make honey from nectar.",
        "A bee once rode a bicycle.",
    )

    results = index.search("How do bees make honey?")

    assert results[0].chunk.text == "Honey bees make honey from nectar."


def test_score_follows_the_bm25_formula():
    index = index_of("apple apple banana", "cherry")

    [result] = index.search("apple apple")  # duplicate question tokens count once

    n_chunks, containing, freq, length, average = 2, 1, 2, 3, 2.0
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = (
        idf * freq * 2.5 / (freq + 1.5 * (1 - 0.75 + 0.75 * length / average))
    )
    assert result.score == pytest.approx(expected)


def test_chunk_length_ignores_stopwords():
    index = index_of("apple the of and is", "apple")

    first, second = index.search("apple")

    assert first.score == pytest.approx(second.score)


def test_returns_at_most_three_results():
    index = index_of(*["apple"] * 5)

    assert len(index.search("apple")) == 3


def test_results_are_ordered_by_score_descending():
    index = index_of("apple pear plum fig", "apple apple apple", "apple apple pear")

    scores = [r.score for r in index.search("apple")]

    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == 3


def test_ties_are_ordered_by_file_name_then_position():
    index = Index(
        [
            Chunk("b.md", 0, "apple"),
            Chunk("a.md", 1, "apple"),
            Chunk("a.md", 0, "apple"),
            Chunk("c.md", 0, "apple"),
        ]
    )

    results = index.search("apple")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


def test_only_chunks_with_a_positive_score_are_returned():
    index = index_of("apple", "banana")

    assert [r.chunk.text for r in index.search("apple")] == ["apple"]


def test_no_match_gives_an_empty_list():
    assert index_of("apple").search("zebra") == []


@pytest.mark.parametrize("question", ["what is the", "?!_", ""])
def test_question_without_usable_tokens_gives_an_empty_list(question):
    assert index_of("what is the apple").search(question) == []


def test_empty_index_gives_an_empty_list():
    assert Index([]).search("apple") == []


def test_chunks_without_usable_tokens_do_not_break_search():
    assert index_of("the of and", "---").search("apple") == []


def test_empty_folder_searches_to_an_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []
