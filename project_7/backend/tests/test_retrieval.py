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


def make_chunks(*texts, file="a.md"):
    return [Chunk(file=file, position=i, text=text) for i, text in enumerate(texts)]


# Loading


def test_loads_only_md_and_txt_files_sorted_by_name(tmp_path):
    (tmp_path / "c.txt").write_text("gamma", encoding="utf-8")
    (tmp_path / "a.md").write_text("alpha one\n\nalpha two", encoding="utf-8")
    (tmp_path / "b.rst").write_text("ignored", encoding="utf-8")
    (tmp_path / "d.md.bak").write_text("ignored", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "e.md").write_text("ignored", encoding="utf-8")
    (tmp_path / "folder.md").mkdir()

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "alpha one"),
        Chunk("a.md", 1, "alpha two"),
        Chunk("c.txt", 0, "gamma"),
    ]


def test_loads_utf8_text(tmp_path):
    (tmp_path / "a.md").write_bytes("café ünïcode".encode("utf-8"))

    assert load_chunks(tmp_path)[0].text == "café ünïcode"


def test_empty_folder_gives_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(DocumentError, match="does-not-exist"):
        load_chunks(tmp_path / "does-not-exist")


def test_file_instead_of_folder_is_an_error(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(path)


def test_bad_utf8_is_a_clear_error(tmp_path):
    (tmp_path / "broken.txt").write_bytes(b"ok \xff\xfe not ok")

    with pytest.raises(DocumentError, match="broken.txt.*UTF-8"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n\n   \n\n\n\tthird\n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


def test_normalizes_line_endings():
    assert chunk_text("one\r\ntwo\r\n\r\nthree\rfour\r\rfive") == [
        "one\ntwo",
        "three\nfour",
        "five",
    ]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text.\n\nMore.") == [
        "# Title\nBody text.",
        "More.",
    ]


def test_consecutive_headings_are_all_joined():
    assert chunk_text("# One\n## Two\n\n### Three\n\nBody") == [
        "# One\n## Two\n### Three\nBody"
    ]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n## Dangling") == ["Body"]
    assert chunk_text("# Only a heading") == []


def test_block_with_heading_and_text_is_not_a_heading_block():
    assert chunk_text("# Title\ntext\n\nnext") == ["# Title\ntext", "next"]


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 800

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = [f"word{i:04d}" for i in range(300)]
    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(0 < len(piece) <= 800 for piece in pieces)
    assert [word for piece in pieces for word in piece.split()] == words
    # Greedy: 89 eight-letter words and 88 spaces are the most that fit.
    assert len(pieces[0]) == 800


def test_split_can_break_right_at_the_limit():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_words():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_heading_counts_toward_the_split_length():
    pieces = chunk_text("# H\n\n" + "y" * 800)

    assert pieces == ["# H", "y" * 800]


def test_positions_count_within_each_file(tmp_path):
    (tmp_path / "a.md").write_text("z" * 900 + "\n\nlast", encoding="utf-8")
    (tmp_path / "b.md").write_text("other", encoding="utf-8")

    assert [(c.file, c.position) for c in load_chunks(tmp_path)] == [
        ("a.md", 0),
        ("a.md", 1),
        ("a.md", 2),
        ("b.md", 0),
    ]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.0 don't") == [
        "hello",
        "world",
        "snake",
        "case",
        "v2",
        "0",
        "don",
        "t",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Crème brûlée — ŽENA ١٢") == ["crème", "brûlée", "žena", "١٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("What is the Volcano?") == ["volcano"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = Index(
        make_chunks(
            "Bananas are yellow fruit.",
            "Volcanoes erupt with molten lava.",
            "Penguins live in the cold south.",
        )
    )

    matches = index.search("How do volcanoes erupt?")

    assert [m.text for m in matches] == ["Volcanoes erupt with molten lava."]
    assert matches[0].file == "a.md"
    assert matches[0].score > 0


def test_score_follows_the_bm25_formula():
    index = Index(make_chunks("cat cat dog", "dog bird fish ant bee", "cow"))
    n_chunks, avglen = 3, 3.0

    def term(n, f, length):
        idf = math.log(1 + (n_chunks - n + 0.5) / (n + 0.5))
        return idf * f * 2.5 / (f + 1.5 * (1 - 0.75 + 0.75 * length / avglen))

    matches = index.search("the cat and the dog, cat!")

    assert [m.text for m in matches] == ["cat cat dog", "dog bird fish ant bee"]
    assert matches[0].score == pytest.approx(term(1, 2, 3) + term(2, 1, 3))
    assert matches[1].score == pytest.approx(term(2, 1, 5))


def test_length_excludes_stopwords():
    index = Index(make_chunks("the a an of lava", "lava"))

    first, second = index.search("lava")

    assert first.score == pytest.approx(second.score)


def test_returns_at_most_three_results():
    index = Index(make_chunks(*["lava flow"] * 5))

    assert len(index.search("lava")) == 3


def test_ties_are_ordered_by_file_then_position():
    chunks = [
        Chunk("b.md", 0, "lava"),
        Chunk("a.md", 1, "lava"),
        Chunk("c.md", 0, "lava"),
        Chunk("a.md", 0, "lava"),
    ]

    matches = Index(chunks).search("lava")

    assert [(m.file, m.position) for m in matches] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


def test_higher_score_beats_file_order():
    chunks = [Chunk("a.md", 0, "lava rock"), Chunk("b.md", 0, "lava lava")]

    assert [m.file for m in Index(chunks).search("lava")] == ["b.md", "a.md"]


def test_no_match_gives_empty_list():
    assert Index(make_chunks("bananas")).search("volcano") == []


def test_stopword_only_or_symbol_only_question_gives_empty_list():
    index = Index(make_chunks("what is the thing"))

    assert index.search("what is the") == []
    assert index.search("?!...") == []


def test_no_chunks_gives_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("volcano") == []


def test_chunks_with_only_stopwords_are_harmless():
    assert Index(make_chunks("the of and", "---")).search("volcano") == []
