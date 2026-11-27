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


def index_of(*texts):
    return Index([Chunk(f"{i}.md", 0, text) for i, text in enumerate(texts)])


# Loading


def test_loads_only_md_and_txt_sorted_by_name(tmp_path):
    write(tmp_path, "b.txt", "bee")
    write(tmp_path, "a.md", "ay")
    write(tmp_path, "c.rst", "sea")
    write(tmp_path, "notes", "no suffix")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "d.md", "nested")
    (tmp_path / "dir.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "ay"),
        ("b.txt", 0, "bee"),
    ]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo\n\nthree")

    chunks = load_chunks(tmp_path)

    assert chunks == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("a.md", 2, "three"),
    ]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.md", "crème brûlée")

    assert load_chunks(tmp_path)[0].text == "crème brûlée"


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(DocumentError, match="nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_invalid_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_and_trims():
    text = "\n\n  first line\nsecond line  \n\n   \n\t\n\nthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "still one", "", "two"])

    assert chunk_text(text) == ["one\nstill one", "two"]


def test_heading_is_joined_to_next_block():
    assert chunk_text("# Title\n\nBody\n\nMore") == ["# Title\nBody", "More"]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# A\n## B\n\n### C\n\nBody"

    assert chunk_text(text) == ["# A\n## B\n### C\nBody"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n## Dangling") == ["Body"]
    assert chunk_text("# Only a heading") == []


def test_block_with_heading_and_text_is_a_normal_block():
    assert chunk_text("# A\ntext\n\nnext") == ["# A\ntext", "next"]


def test_chunk_of_exactly_800_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = ["word%03d" % i for i in range(300)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) == 3
    assert all(len(piece) <= 800 for piece in pieces)
    assert " ".join(pieces).split() == words


def test_split_uses_whitespace_right_after_the_limit():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_text():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_heading_counts_toward_split_length(tmp_path):
    write(tmp_path, "a.md", "# Head\n\n" + "word " * 200)

    chunks = load_chunks(tmp_path)

    assert [c.position for c in chunks] == [0, 1]
    assert chunks[0].text.startswith("# Head\nword")
    assert all(len(c.text) <= 800 for c in chunks)


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.5 don't") == [
        "hello",
        "world",
        "snake",
        "case",
        "v2",
        "5",
        "don",
        "t",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Über café ١٢٣") == ["über", "café", "١٢٣"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("The cat is on the mat.") == ["cat", "mat"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Penguins live in Antarctica and eat fish.",
        "Volcanoes erupt molten lava.",
        "Fish swim in rivers.",
    )

    hits = index.search("What do penguins eat?")

    assert hits[0].file == "0.md"
    assert hits[0].text == "Penguins live in Antarctica and eat fish."
    assert hits[0].score > 0


def test_score_matches_the_bm25_formula():
    index = index_of("cat cat dog", "bird")

    [hit] = index.search("cat cat")  # repeated question tokens count once

    n_chunks, containing, freq, length, average = 2, 1, 2, 3, 2.0
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = (
        idf * freq * 2.5 / (freq + 1.5 * (1 - 0.75 + 0.75 * length / average))
    )
    assert hit.score == pytest.approx(expected)


def test_returns_at_most_three_ordered_by_score():
    index = index_of(
        "apple pear plum fig date",
        "apple apple apple",
        "apple pear",
        "apple pear plum",
        "apple",
    )

    hits = index.search("apple")

    assert len(hits) == 3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert hits[0].file == "1.md"


def test_ties_are_ordered_by_file_then_position():
    index = Index(
        [
            Chunk("b.md", 0, "kiwi"),
            Chunk("a.md", 1, "kiwi"),
            Chunk("c.md", 0, "kiwi"),
            Chunk("a.md", 0, "kiwi"),
        ]
    )

    hits = index.search("kiwi")

    assert [(h.file, h.position) for h in hits] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


def test_only_positive_scores_are_returned():
    index = index_of("mango", "banana")

    assert [h.file for h in index.search("mango")] == ["0.md"]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!_", ""])
def test_no_match_or_no_usable_tokens_gives_empty_list(question):
    assert index_of("mango is what the").search(question) == []


def test_empty_index_gives_empty_list():
    assert Index([]).search("mango") == []


def test_chunks_with_only_stopwords_do_not_break_search():
    assert index_of("the and of").search("mango") == []


def test_empty_folder_search_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("anything") == []
