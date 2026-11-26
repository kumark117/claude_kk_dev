import math

import pytest

from app.retrieval import DocumentError, Index, chunk_text, load_chunks, tokenize


def write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def index_of(folder, files):
    for name, text in files.items():
        write(folder, name, text)
    return Index(load_chunks(folder))


# Loading


def test_reads_only_md_and_txt_files_sorted_by_name(tmp_path):
    write(tmp_path, "b.txt", "bravo")
    write(tmp_path, "a.md", "alpha")
    write(tmp_path, "c.rst", "charlie")
    write(tmp_path, "notes", "delta")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "d.md", "echo")
    (tmp_path / "folder.md").mkdir()

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "alpha"),
        ("b.txt", 0, "bravo"),
    ]


def test_each_chunk_keeps_file_name_and_position(tmp_path):
    write(tmp_path, "a.md", "one\n\ntwo\n\nthree")

    chunks = load_chunks(tmp_path)

    assert [(c.file, c.position, c.text) for c in chunks] == [
        ("a.md", 0, "one"),
        ("a.md", 1, "two"),
        ("a.md", 2, "three"),
    ]


def test_reads_non_ascii_utf8_and_ignores_byte_order_mark(tmp_path):
    (tmp_path / "a.md").write_bytes("﻿# Café\n\nCrème brûlée".encode("utf-8"))

    assert [c.text for c in load_chunks(tmp_path)] == ["# Café\nCrème brûlée"]


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocumentError, match="does-not-exist"):
        load_chunks(tmp_path / "does-not-exist")


def test_a_file_instead_of_a_folder_is_an_error(tmp_path):
    write(tmp_path, "a.md", "alpha")

    with pytest.raises(DocumentError, match="a.md"):
        load_chunks(tmp_path / "a.md")


def test_invalid_utf8_is_an_error_naming_the_file(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"ok \xff\xfe not utf-8")

    with pytest.raises(DocumentError, match="bad.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first line\nsecond line  \n \t \n\n\n third \n\n"

    assert chunk_text(text) == ["first line\nsecond line", "third"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["one", "two", "", "three"])

    assert chunk_text(text) == ["one\ntwo", "three"]


def test_heading_block_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody text.\n\nMore.") == [
        "# Title\nBody text.",
        "More.",
    ]


def test_consecutive_heading_blocks_all_join_the_next_block():
    assert chunk_text("# A\n## B\n\n### C\n\nBody") == ["# A\n## B\n### C\nBody"]


def test_block_with_heading_and_text_is_not_a_heading_block():
    assert chunk_text("# A\ntext\n\nnext") == ["# A\ntext", "next"]


def test_heading_with_no_following_block_is_dropped():
    assert chunk_text("Body\n\n## Dangling\n\n") == ["Body"]
    assert chunk_text("# Only a heading") == []


def test_chunk_of_exactly_800_characters_is_not_split():
    text = "a" * 800

    assert chunk_text(text) == [text]


def test_long_chunk_is_split_at_whitespace():
    words = [f"word{i:04d}" for i in range(300)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) > 1
    assert all(0 < len(piece) <= 800 for piece in pieces)
    assert " ".join(pieces).split() == words


def test_split_uses_the_last_whitespace_that_fits():
    first = "a" * 800

    assert chunk_text(first + " tail") == [first, "tail"]
    assert chunk_text("a" * 500 + "\n" + "b" * 500) == ["a" * 500, "b" * 500]


def test_a_single_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]
    assert chunk_text("hi " + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_long_chunk_pieces_get_consecutive_positions(tmp_path):
    write(tmp_path, "a.md", "x" * 900 + "\n\nlast")

    assert [(c.position, len(c.text)) for c in load_chunks(tmp_path)] == [
        (0, 800),
        (1, 100),
        (2, 4),
    ]


# Tokenizing


def test_tokenize_lowercases_and_splits_on_punctuation_and_underscore():
    assert tokenize("Hello, WORLD_wide-web! v2.0") == [
        "hello",
        "world",
        "wide",
        "web",
        "v2",
        "0",
    ]


def test_tokenize_keeps_unicode_letters_and_digits():
    assert tokenize("Éclair 東京 ٤٢") == ["éclair", "東京", "٤٢"]


def test_tokenize_removes_exactly_the_stopwords():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("not will have") == ["not", "will", "have"]


def test_no_stemming():
    assert tokenize("planets planet") == ["planets", "planet"]


# Ranking


def test_relevant_chunk_ranks_first(tmp_path):
    index = index_of(
        tmp_path,
        {
            "cats.md": "Cats purr and sleep all day.",
            "space.md": "Rockets reach orbit using liquid fuel.",
            "tea.md": "Green tea is steeped in warm water.",
        },
    )

    hits = index.search("How do rockets reach orbit?")

    assert [hit.file for hit in hits] == ["space.md"]
    assert hits[0].text == "Rockets reach orbit using liquid fuel."
    assert hits[0].score > 0


def test_score_follows_the_bm25_formula(tmp_path):
    index = index_of(
        tmp_path,
        {"a.md": "apple apple banana", "b.md": "cherry", "c.md": "banana date"},
    )

    hits = index.search("apple apple banana")

    n_chunks, avglen = 3, (3 + 1 + 2) / 3

    def term(n, f, length):
        idf = math.log(1 + (n_chunks - n + 0.5) / (n + 0.5))
        return idf * f * 2.5 / (f + 1.5 * (1 - 0.75 + 0.75 * length / avglen))

    assert [hit.file for hit in hits] == ["a.md", "c.md"]
    assert hits[0].score == pytest.approx(term(1, 2, 3) + term(2, 1, 3))
    assert hits[1].score == pytest.approx(term(2, 1, 2))


def test_chunk_length_excludes_stopwords(tmp_path):
    index = index_of(
        tmp_path,
        {"a.md": "the the the the apple", "b.md": "apple"},
    )

    first, second = index.search("apple")

    assert first.score == pytest.approx(second.score)


def test_returns_at_most_three_results(tmp_path):
    index = index_of(tmp_path, {f"{i}.md": "apple" for i in range(5)})

    assert len(index.search("apple")) == 3


def test_ties_are_ordered_by_file_name_then_position(tmp_path):
    write(tmp_path, "b.md", "apple one")
    write(tmp_path, "a.md", "apple two\n\napple three")
    chunks = load_chunks(tmp_path)

    hits = Index(list(reversed(chunks))).search("apple")

    assert [(hit.file, hit.text) for hit in hits] == [
        ("a.md", "apple two"),
        ("a.md", "apple three"),
        ("b.md", "apple one"),
    ]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?!_ --", ""])
def test_no_match_or_no_usable_tokens_returns_empty_list(tmp_path, question):
    index = index_of(tmp_path, {"a.md": "apple"})

    assert index.search(question) == []


def test_empty_folder_returns_empty_list(tmp_path):
    assert Index(load_chunks(tmp_path)).search("apple") == []


def test_chunks_without_usable_tokens_are_harmless(tmp_path):
    index = index_of(tmp_path, {"a.md": "the and of"})

    assert index.search("apple") == []
