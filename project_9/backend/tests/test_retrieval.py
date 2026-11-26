import math

import pytest

from app.retrieval import (
    Chunk,
    DocumentsError,
    Index,
    chunk_text,
    load_chunks,
    tokenize,
)


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


# Loading


def test_reads_only_md_and_txt_sorted_by_name(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "ignored")
    write(tmp_path, "notes.md.bak", "ignored")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "nested.md", "ignored")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert chunks == [Chunk("a.md", 0, "alpha"), Chunk("b.txt", 0, "bravo")]


def test_chunks_keep_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo")

    assert load_chunks(tmp_path) == [Chunk("a.md", 0, "one"), Chunk("a.md", 1, "two")]


def test_reads_utf8(tmp_path):
    write(tmp_path, "a.md", "café — naïve")

    assert load_chunks(tmp_path)[0].text == "café — naïve"


def test_byte_order_mark_does_not_hide_a_heading(tmp_path):
    (tmp_path / "a.md").write_bytes("﻿# Title\n\nBody".encode("utf-8"))

    assert [c.text for c in load_chunks(tmp_path)] == ["# Title\nBody"]


def test_empty_folder_has_no_chunks(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocumentsError, match="nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_file_instead_of_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentsError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_bad_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "broken.txt").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocumentsError, match="broken.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "  first \n\n\n   \n\t\nsecond line\nthird line  \n\n"

    assert chunk_text(text) == ["first", "second line\nthird line"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "two", "", "three"])

    assert chunk_text(text) == ["one\ntwo", "three"]


def test_heading_is_joined_to_next_block():
    assert chunk_text("# Title\n\nBody text") == ["# Title\nBody text"]


def test_consecutive_headings_are_all_joined():
    text = "# Title\n\n## Section\n### Sub\n\nBody"

    assert chunk_text(text) == ["# Title\n## Section\n### Sub\nBody"]


def test_block_with_heading_and_text_stays_on_its_own():
    assert chunk_text("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n# Dangling") == ["Body"]
    assert chunk_text("# Only heading") == []


def test_long_chunk_is_split_at_whitespace():
    words = ["word%03d" % i for i in range(300)]
    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(len(piece) <= 800 for piece in pieces)
    assert " ".join(pieces).split() == words


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_split_uses_whitespace_right_after_the_limit():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_short_text():
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_heading_join_happens_before_splitting():
    pieces = chunk_text("# Title\n\n" + "word " * 300)

    assert pieces[0].startswith("# Title\nword")
    assert all(len(piece) <= 800 for piece in pieces)


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD! snake_case v2.5 e-mail") == [
        "hello", "world", "snake", "case", "v2", "5", "e", "mail",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Ünïcode ΑΒΓ 東京 ٤٢") == ["ünïcode", "αβγ", "東京", "٤٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []


def test_tokenize_does_not_stem():
    assert tokenize("cats cat running") == ["cats", "cat", "running"]


# Ranking


def index(*texts):
    return Index([Chunk(f"f{i}.md", 0, text) for i, text in enumerate(texts)])


def test_most_relevant_chunk_ranks_first():
    hits = index(
        "The harbour opens at dawn.",
        "Penguins huddle together; penguins keep warm.",
        "A penguin was seen near the harbour office building yesterday.",
    ).search("How do penguins keep warm?")

    assert hits[0].file == "f1.md"
    assert hits[0].text == "Penguins huddle together; penguins keep warm."


def test_score_matches_the_bm25_formula():
    hits = index("apple apple banana", "cherry").search("apple apple")

    # N=2, n=1, f=2, len=3, avglen=2; the repeated question token counts once.
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.5 / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    assert [hit.score for hit in hits] == [pytest.approx(expected)]


def test_length_excludes_stopwords():
    hits = index("the the the apple", "apple").search("apple")

    assert hits[0].score == pytest.approx(hits[1].score)


def test_returns_at_most_three_hits():
    hits = index(*["apple"] * 5).search("apple")

    assert len(hits) == 3


def test_ties_are_ordered_by_file_then_position():
    chunks = [
        Chunk("b.md", 0, "apple"),
        Chunk("a.md", 10, "apple"),
        Chunk("a.md", 2, "apple"),
        Chunk("c.md", 0, "apple"),
    ]

    hits = Index(chunks).search("apple")

    assert [(hit.file, hit.position) for hit in hits] == [
        ("a.md", 2), ("a.md", 10), ("b.md", 0),
    ]


def test_only_chunks_with_a_match_are_returned():
    hits = index("apple pie", "banana bread").search("apple")

    assert [hit.file for hit in hits] == ["f0.md"]


def test_no_match_returns_empty_list():
    assert index("apple").search("zebra") == []


def test_stopword_only_question_returns_empty_list():
    assert index("what is the apple").search("What is the") == []
    assert index("apple").search("?!") == []


def test_no_chunks_returns_empty_list():
    assert Index([]).search("apple") == []


def test_chunks_without_any_tokens_are_harmless():
    assert index("the", "---").search("apple") == []
