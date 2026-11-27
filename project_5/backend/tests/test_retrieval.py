import math
from pathlib import Path

import pytest

from app.retrieval import (
    Chunk,
    DocsError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def index_of(*texts: str) -> Index:
    return Index([Chunk("doc.md", position, text) for position, text in enumerate(texts)])


# Loading


def test_loads_only_md_and_txt_files_sorted_by_name(tmp_path):
    write(tmp_path, "c.txt", "gamma")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "b.rst", "ignored")
    write(tmp_path, "d.json", "ignored")
    (tmp_path / "nested").mkdir()
    write(tmp_path / "nested", "e.md", "ignored")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [("a.md", "alpha"), ("c.txt", "gamma")]


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


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocsError, match="not found.*nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_file_in_place_of_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "text")

    with pytest.raises(DocsError, match="not found"):
        load_chunks(tmp_path / "a.md")


def test_invalid_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocsError, match="bad.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n   \n\n\n\tthird\t\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "two", "", "three"])

    assert chunk_text(text) == ["one\ntwo", "three"]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text") == ["# Title\nBody text"]


def test_consecutive_headings_are_all_joined_to_the_next_block():
    text = "# Title\n## Subtitle\n\n### Section\n\nBody\n\nMore"

    assert chunk_text(text) == ["# Title\n## Subtitle\n### Section\nBody", "More"]


def test_block_with_heading_and_text_is_not_treated_as_heading_only():
    assert chunk_text("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n# Dangling") == ["Body"]
    assert chunk_text("# Only a heading") == []


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "x" * 800

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = ["word%04d" % number for number in range(250)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(len(piece) <= 800 for piece in pieces)
    assert all(piece == piece.strip() for piece in pieces)
    assert " ".join(pieces).split() == words


def test_split_may_use_whitespace_right_after_the_800th_character():
    assert chunk_text("a" * 800 + " tail") == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("ab " + "x" * 1700) == ["ab", "x" * 800, "x" * 800, "x" * 100]


def test_joined_heading_counts_towards_the_length_limit():
    pieces = chunk_text("# Title\n\n" + "y" * 800)

    assert pieces == ["# Title", "y" * 800]


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "z" * 900 + "\n\nafter")

    assert [(c.position, len(c.text)) for c in load_chunks(tmp_path)] == [
        (0, 800),
        (1, 100),
        (2, 5),
    ]


# Tokenizing


def test_tokenize_lowercases_and_separates_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.5 e-mail") == [
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
    assert tokenize("Éclair Ünïcode 東京 ٤٢") == ["éclair", "ünïcode", "東京", "٤٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or that "
        "the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("The cat is on the mat") == ["cat", "mat"]


def test_tokenize_does_not_stem():
    assert tokenize("cats running") == ["cats", "running"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Bananas are yellow fruit.",
        "Volcanoes erupt molten lava.",
        "Penguins swim in cold water.",
    )

    results = index.search("How do volcanoes erupt?")

    assert results[0].text == "Volcanoes erupt molten lava."
    assert results[0].file == "doc.md"
    assert results[0].score > 0


def test_score_follows_the_bm25_formula():
    index = index_of("apple apple banana", "banana cherry", "date")

    results = index.search("apple apple")

    # N = 3 chunks, n = 1 contains "apple", f = 2, len = 3, avglen = 2.
    idf = math.log(1 + (3 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    assert len(results) == 1
    assert results[0].score == pytest.approx(expected)


def test_chunk_length_excludes_stopwords():
    index = index_of("the the the the apple", "apple", "pear")

    results = index.search("apple")

    assert results[0].score == pytest.approx(results[1].score)


def test_returns_at_most_three_results_in_score_order():
    index = index_of(
        "kiwi",
        "kiwi kiwi kiwi",
        "kiwi plum plum plum plum",
        "kiwi kiwi",
        "plum",
    )

    results = index.search("kiwi")

    assert [r.text for r in results] == ["kiwi kiwi kiwi", "kiwi kiwi", "kiwi"]
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_ties_are_ordered_by_file_name_then_position():
    index = Index(
        [
            Chunk("b.md", 0, "mango tree"),
            Chunk("a.md", 1, "mango tree"),
            Chunk("c.md", 0, "mango tree"),
            Chunk("a.md", 0, "mango tree"),
        ]
    )

    results = index.search("mango tree")

    assert [r.file for r in results] == ["a.md", "a.md", "b.md"]
    assert index.search("mango tree") == results


def test_no_match_returns_empty_list():
    assert index_of("apple", "banana").search("zeppelin") == []


@pytest.mark.parametrize("question", ["what is the", "?!...", ""])
def test_question_without_usable_tokens_returns_empty_list(question):
    assert index_of("what is the apple").search(question) == []


def test_empty_index_returns_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_without_any_tokens_do_not_break_search():
    assert index_of("the and of", "---").search("apple") == []
