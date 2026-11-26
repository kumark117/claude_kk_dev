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


def texts(raw: str) -> list[str]:
    return [chunk.text for chunk in chunk_text("doc.md", raw)]


def index_of(*bodies: str) -> Index:
    return Index([Chunk(f"{i}.md", 0, body) for i, body in enumerate(bodies)])


# Loading


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    write(tmp_path, "b.txt", "bee")
    write(tmp_path, "a.md", "ay")
    write(tmp_path, "c.rst", "ignored")
    write(tmp_path, "d.markdown", "ignored")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "e.md", "ignored")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "ay"),
        ("b.txt", 0, "bee"),
    ]


def test_reads_files_as_utf8(tmp_path):
    (tmp_path / "a.md").write_bytes("café über".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café über"


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error_naming_the_folder(tmp_path):
    with pytest.raises(DocumentError, match="nope"):
        load_chunks(tmp_path / "nope")


def test_a_file_in_place_of_the_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "text")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_invalid_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_and_trims_blocks():
    assert texts("  one  \n\n\n   \n\t\n two\nstill two \n\n") == [
        "one",
        "two\nstill two",
    ]


def test_normalizes_line_endings():
    assert texts("one\r\ntwo\r\n\r\nthree\rfour") == ["one\ntwo", "three\nfour"]


def test_chunks_keep_file_name_and_position():
    chunks = chunk_text("notes.txt", "one\n\ntwo")

    assert [(c.file, c.position) for c in chunks] == [
        ("notes.txt", 0),
        ("notes.txt", 1),
    ]


def test_heading_block_is_joined_to_next_block():
    assert texts("# Title\n\nBody\n\nMore") == ["# Title\nBody", "More"]


def test_consecutive_heading_blocks_are_all_joined_to_next_block():
    assert texts("# Title\n## Sub\n\n### Deeper\n\nBody") == [
        "# Title\n## Sub\n### Deeper\nBody"
    ]


def test_block_with_heading_and_text_is_not_joined_forward():
    assert texts("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_trailing_heading_is_dropped():
    assert texts("Body\n\n# Dangling\n\n## Also dangling") == ["Body"]


def test_splits_long_chunk_at_whitespace():
    words = " ".join(["abcdefghi"] * 200)  # 1999 characters

    pieces = texts(words)

    assert all(len(piece) <= 800 for piece in pieces)
    assert [len(piece) for piece in pieces] == [799, 799, 399]
    assert " ".join(pieces) == words


def test_chunk_of_exactly_800_characters_is_not_split():
    assert texts("a" * 399 + " " + "b" * 400) == ["a" * 399 + " " + "b" * 400]


def test_word_ending_at_the_limit_stays_whole():
    assert texts("a" * 800 + " tail") == ["a" * 800, "tail"]


def test_cuts_a_single_long_word_at_800():
    assert texts("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_word():
    assert texts("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_split_pieces_get_consecutive_positions():
    chunks = chunk_text("a.md", "x" * 900 + "\n\nlast")

    assert [c.position for c in chunks] == [0, 1, 2]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, World! snake_case x-ray v2.5 Über") == [
        "hello",
        "world",
        "snake",
        "case",
        "x",
        "ray",
        "v2",
        "5",
        "über",
    ]


def test_tokenize_removes_exactly_the_listed_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("we not has") == ["we", "not", "has"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Cats purr and sleep all day.",
        "Volcanoes erupt molten lava.",
        "Bread needs flour and yeast.",
    )

    results = index.search("Why do volcanoes erupt?")

    assert [r.file for r in results] == ["1.md"]
    assert results[0].text == "Volcanoes erupt molten lava."


def test_score_follows_bm25_formula():
    index = index_of("apple apple banana", "banana cherry", "date")

    results = index.search("apple apple banana")

    n_chunks, avg_len = 3, (3 + 2 + 1) / 3

    def term(f, n, length):
        idf = math.log(1 + (n_chunks - n + 0.5) / (n + 0.5))
        return idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / avg_len))

    assert [r.file for r in results] == ["0.md", "1.md"]
    assert results[0].score == pytest.approx(term(2, 1, 3) + term(1, 2, 3))
    assert results[1].score == pytest.approx(term(1, 2, 2))


def test_length_excludes_stopwords():
    index = index_of("zebra the the the the the", "zebra lion")

    results = index.search("zebra")

    assert [r.file for r in results] == ["0.md", "1.md"]


def test_returns_at_most_three_results():
    index = index_of(*["zebra"] * 5)

    assert len(index.search("zebra")) == 3


def test_ties_are_ordered_by_file_then_position():
    chunks = [
        Chunk("b.md", 0, "zebra"),
        Chunk("a.md", 10, "zebra"),
        Chunk("a.md", 2, "zebra"),
        Chunk("c.md", 0, "zebra"),
    ]

    results = Index(chunks).search("zebra")

    assert [(r.file, r.position) for r in results] == [
        ("a.md", 2),
        ("a.md", 10),
        ("b.md", 0),
    ]


@pytest.mark.parametrize(
    "question", ["giraffe", "what is the", "?!... ___", ""]
)
def test_no_usable_match_gives_empty_list(question):
    assert index_of("zebra stripes", "the what is").search(question) == []


def test_empty_index_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("zebra") == []


def test_index_of_stopword_only_chunks_gives_empty_list():
    assert index_of("the and of").search("zebra") == []
