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


def chunks_of(*texts, file="doc.md"):
    return [Chunk(file, position, text) for position, text in enumerate(texts)]


# Loading


def test_loads_only_md_and_txt_sorted_by_name(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "ignored")
    write(tmp_path, "d.markdown", "ignored")
    (tmp_path / "folder.md").mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    write(nested, "deep.md", "ignored")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "alpha"),
        Chunk("b.txt", 0, "bravo"),
    ]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")
    write(tmp_path, "b.md", "three")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("b.md", 0, "three"),
    ]


def test_reads_utf8_and_ignores_byte_order_mark(tmp_path):
    (tmp_path / "a.md").write_bytes("# Café\n\ncrème".encode("utf-8-sig"))

    assert load_chunks(tmp_path) == [Chunk("a.md", 0, "# Café\ncrème")]


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocumentError, match="nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_path_that_is_a_file_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_bad_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentError, match="bad.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_and_trims():
    text = "  first line\nsecond line  \n\n   \n\n\n third \n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["a", "b", "", "c"])

    assert chunk_text(text) == ["a\nb", "c"]


def test_blank_text_gives_no_chunks():
    assert chunk_text(" \n\n\t\n") == []


def test_heading_block_is_joined_to_next_block():
    assert chunk_text("# Title\n\nBody\n\nMore") == ["# Title\nBody", "More"]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# One\n## Two\n\n### Three\n\nBody"

    assert chunk_text(text) == ["# One\n## Two\n### Three\nBody"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n# Dangling\n\n## Also") == ["Body"]


def test_block_with_heading_and_text_stays_as_it_is():
    assert chunk_text("# Title\ntext\n\nnext") == ["# Title\ntext", "next"]


def test_chunk_of_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_splits_at_whitespace():
    text = " ".join(["word"] * 500)  # 2499 characters

    pieces = chunk_text(text)

    assert [len(piece) for piece in pieces] == [799, 799, 799, 99]
    assert " ".join(pieces) == text


def test_split_may_end_exactly_at_800():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_word():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_split_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nend")

    assert [(c.position, len(c.text)) for c in load_chunks(tmp_path)] == [
        (0, 800),
        (1, 100),
        (2, 3),
    ]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD_wide-web! v2.5") == [
        "hello",
        "world",
        "wide",
        "web",
        "v2",
        "5",
    ]


def test_tokenize_keeps_unicode_letters():
    assert tokenize("Ünïcode naïve ΑΒΓ") == ["ünïcode", "naïve", "αβγ"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []


# Ranking


def test_relevant_chunk_ranks_first():
    index = Index(
        chunks_of(
            "Bees make honey in the hive.",
            "Tides follow the moon.",
            "Coffee beans are roasted.",
        )
    )

    results = index.search("Why do tides follow the moon?")

    assert results[0].chunk.text == "Tides follow the moon."
    assert len(results) == 1


def test_score_matches_the_bm25_formula():
    index = Index(chunks_of("moon moon tide", "honey bee hive wax comb"))

    [result] = index.search("moon")

    n_chunks, containing, f, length, average = 2, 1, 2, 3, 4
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = idf * f * 2.5 / (f + 1.5 * (1 - 0.75 + 0.75 * length / average))
    assert result.score == pytest.approx(expected)


def test_length_excludes_stopwords():
    index = Index(chunks_of("the the the the moon", "moon"))

    first, second = index.search("moon")

    assert first.score == second.score


def test_repeated_question_tokens_count_once():
    index = Index(chunks_of("moon tide", "honey"))

    assert index.search("moon moon moon") == index.search("moon")


def test_at_most_three_results_ordered_by_score():
    index = Index(
        chunks_of(
            "moon a1 a2 a3 a4",
            "moon moon moon",
            "moon b1",
            "moon c1 c2",
            "other",
        )
    )

    results = index.search("moon")

    assert [r.chunk.position for r in results] == [1, 2, 3]
    assert [r.score for r in results] == sorted(
        (r.score for r in results), reverse=True
    )


def test_ties_are_ordered_by_file_then_position():
    chunks = [
        Chunk("b.md", 0, "moon"),
        Chunk("a.md", 1, "moon"),
        Chunk("c.md", 0, "moon"),
        Chunk("a.md", 0, "moon"),
    ]

    results = Index(chunks).search("moon")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


@pytest.mark.parametrize(
    "question", ["volcano", "what is the", "?!...", ""]
)
def test_no_usable_match_gives_empty_list(question):
    index = Index(chunks_of("what is the moon"))

    assert index.search(question) == []


def test_index_without_chunks_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("moon") == []


def test_chunks_without_any_tokens_do_not_break_search():
    assert Index(chunks_of("the and of", "---")).search("moon") == []
