import math

import pytest

from app.retrieval import (
    Chunk,
    DocumentError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def chunk(text, file="a.md", position=0):
    return Chunk(file=file, position=position, text=text)


# Loading


def test_loads_only_md_and_txt_sorted_by_name(tmp_path):
    write(tmp_path, "c.txt", "gamma")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "b.md", "beta")
    write(tmp_path, "skip.rst", "delta")
    write(tmp_path, "skip.md.bak", "epsilon")
    (tmp_path / "folder.md").mkdir()
    (tmp_path / "nested").mkdir()
    write(tmp_path / "nested", "deep.md", "zeta")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.text) for c in chunks] == [
        ("a.md", "alpha"),
        ("b.md", "beta"),
        ("c.txt", "gamma"),
    ]


def test_chunks_keep_file_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position) for c in chunks] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


def test_reads_utf8(tmp_path):
    (tmp_path / "a.txt").write_bytes("café Ünïcode".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café Ünïcode"


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(DocumentError, match="does-not-exist"):
        load_chunks(tmp_path / "does-not-exist")


def test_file_instead_of_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_bad_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "broken.txt").write_bytes(b"ok \xff\xfe not ok")

    with pytest.raises(DocumentError, match="broken.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n\n   \n\t\n\nthird\n\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "two", "", "three"])

    assert chunk_text(text) == ["one\ntwo", "three"]


def test_heading_block_joins_the_next_block():
    assert chunk_text("# Title\n\nBody text.") == ["# Title\nBody text."]


def test_consecutive_heading_blocks_all_join_the_next_block():
    text = "# Title\n## Sub\n\n### Deeper\n\nBody.\n\nMore."

    assert chunk_text(text) == ["# Title\n## Sub\n### Deeper\nBody.", "More."]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body.\n\n## Nothing follows") == ["Body."]
    assert chunk_text("# Only a heading") == []


def test_block_with_heading_and_text_is_not_a_heading_block():
    text = "# Title\nattached\n\nnext"

    assert chunk_text(text) == ["# Title\nattached", "next"]


def test_chunk_of_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_splits_at_whitespace():
    words = ["word%04d" % i for i in range(300)]
    text = " ".join(words)

    pieces = chunk_text(text)

    assert len(pieces) > 1
    assert all(0 < len(p) <= 800 for p in pieces)
    assert " ".join(pieces).split() == words


def test_split_uses_all_800_characters_when_a_word_ends_there():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_text():
    assert chunk_text("short " + "x" * 900) == ["short", "x" * 800, "x" * 100]


def test_heading_counts_toward_the_chunk_length():
    pieces = chunk_text("# Title\n\n" + "y " * 500)

    assert pieces[0].startswith("# Title\ny")
    assert all(len(p) <= 800 for p in pieces)


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD_wide web-page! v2.5") == [
        "hello",
        "world",
        "wide",
        "web",
        "page",
        "v2",
        "5",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Café Ünïcode ٤٢") == ["café", "ünïcode", "٤٢"]


def test_tokenize_removes_exactly_the_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("not we running runs") == ["not", "we", "running", "runs"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = Index(
        [
            chunk("Bananas are yellow fruit.", "a.md"),
            chunk("Volcanoes erupt molten lava.", "b.md"),
            chunk("Lava lamps glow.", "c.md"),
        ]
    )

    results = index.search("When do volcanoes erupt lava?")

    assert [r.chunk.file for r in results] == ["b.md", "c.md"]
    assert results[0].score > results[1].score > 0


def test_score_matches_the_bm25_formula():
    index = Index(
        [chunk("cat cat dog", "a.md"), chunk("fish", "b.md")],
    )

    [result] = index.search("the cat cat")

    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    assert result.score == pytest.approx(expected)


def test_length_excludes_stopwords():
    index = Index(
        [chunk("the the the cat", "a.md"), chunk("cat", "b.md")],
    )

    first, second = index.search("cat")

    assert first.score == pytest.approx(second.score)


def test_returns_at_most_three_results():
    index = Index([chunk("apple", f"{i}.md") for i in range(5)])

    assert len(index.search("apple")) == 3


def test_ties_are_ordered_by_file_then_position():
    index = Index(
        [
            chunk("apple", "b.md", 1),
            chunk("apple", "b.md", 0),
            chunk("apple", "c.md", 0),
            chunk("apple", "a.md", 2),
        ]
    )

    results = index.search("apple")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 2),
        ("b.md", 0),
        ("b.md", 1),
    ]


def test_no_match_gives_empty_list():
    assert Index([chunk("apple")]).search("zebra") == []


def test_stopword_only_question_gives_empty_list():
    assert Index([chunk("what is the apple")]).search("What is the?") == []


def test_empty_index_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_index_of_stopword_only_chunks_gives_empty_list():
    assert Index([chunk("the and of")]).search("apple") == []


def test_byte_order_mark_does_not_hide_a_heading(tmp_path):
    (tmp_path / "a.md").write_bytes(b"\xef\xbb\xbf# Title\n\nBody.")

    assert [c.text for c in load_chunks(tmp_path)] == ["# Title\nBody."]
