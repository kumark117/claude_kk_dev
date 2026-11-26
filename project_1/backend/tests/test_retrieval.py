import math

import pytest

from app.retrieval import (
    BM25Index,
    Chunk,
    DocumentError,
    chunk_text,
    load_chunks,
    tokenize,
)


def index_of(*texts):
    return BM25Index([Chunk(f"{i}.md", 0, text) for i, text in enumerate(texts)])


# Loading


def test_loads_only_md_and_txt_sorted_by_name_without_recursion(tmp_path):
    (tmp_path / "b.txt").write_text("bravo", encoding="utf-8")
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "c.rst").write_text("charlie", encoding="utf-8")
    (tmp_path / "notes").write_text("no suffix", encoding="utf-8")
    (tmp_path / "folder.md").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.md").write_text("delta", encoding="utf-8")

    chunks = load_chunks(tmp_path)

    assert chunks == [Chunk("a.md", 0, "alpha"), Chunk("b.txt", 0, "bravo")]


def test_chunks_keep_file_name_and_position(tmp_path):
    (tmp_path / "a.md").write_text("one\n\ntwo", encoding="utf-8")
    (tmp_path / "b.md").write_text("three", encoding="utf-8")

    assert load_chunks(tmp_path) == [
        Chunk("a.md", 0, "one"),
        Chunk("a.md", 1, "two"),
        Chunk("b.md", 0, "three"),
    ]


def test_reads_utf8_and_drops_byte_order_mark(tmp_path):
    (tmp_path / "a.md").write_bytes(b"\xef\xbb\xbf# Caf\xc3\xa9\n\nOpen daily.")

    assert load_chunks(tmp_path) == [Chunk("a.md", 0, "# Café\nOpen daily.")]


def test_empty_folder_is_allowed(tmp_path):
    assert load_chunks(tmp_path) == []


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(DocumentError, match="not found.*nowhere"):
        load_chunks(tmp_path / "nowhere")


def test_file_instead_of_folder_is_an_error(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("alpha", encoding="utf-8")

    with pytest.raises(DocumentError, match="not found"):
        load_chunks(path)


def test_invalid_utf8_is_an_error(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"ok \xff\xfe not ok")

    with pytest.raises(DocumentError, match="UTF-8.*bad.txt"):
        load_chunks(tmp_path)


# Chunking


def test_splits_on_blank_lines_trims_and_drops_empty_blocks():
    text = "\n\n  first \nstill first\n \t \n\n\nsecond  \n\n   \n"

    assert chunk_text(text) == ["first \nstill first", "second"]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_normalizes_line_endings(newline):
    text = newline.join(["a", "b", "", "c"])

    assert chunk_text(text) == ["a\nb", "c"]


def test_heading_is_joined_to_the_next_block():
    assert chunk_text("# Title\n\nBody\n\nMore") == ["# Title\nBody", "More"]


def test_consecutive_heading_blocks_are_all_joined():
    text = "# One\n## Two\n\n### Three\n\nBody"

    assert chunk_text(text) == ["# One\n## Two\n### Three\nBody"]


def test_block_with_heading_and_text_is_not_joined():
    assert chunk_text("# Title\nBody\n\nNext") == ["# Title\nBody", "Next"]


def test_trailing_heading_is_dropped():
    assert chunk_text("Body\n\n# Orphan\n\n## Orphan too") == ["Body"]


def test_text_of_800_characters_is_not_split():
    text = "a" * 399 + " " + "b" * 400

    assert chunk_text(text) == [text]


def test_long_chunk_splits_at_whitespace():
    words = [f"w{i:04d}" for i in range(399)]

    pieces = chunk_text(" ".join(words))

    assert len(pieces) == 3
    assert all(len(piece) <= 800 for piece in pieces)
    assert " ".join(pieces).split() == words


def test_split_uses_whitespace_just_after_the_limit():
    text = "a" * 800 + " tail"

    assert chunk_text(text) == ["a" * 800, "tail"]


def test_long_word_is_cut_at_800():
    assert chunk_text("x" * 1700) == ["x" * 800, "x" * 800, "x" * 100]


def test_long_word_after_a_short_one():
    assert chunk_text("hi\n" + "x" * 900) == ["hi", "x" * 800, "x" * 100]


def test_length_limit_includes_a_joined_heading():
    pieces = chunk_text("# Title\n\n" + "word " * 200)

    assert pieces[0].startswith("# Title\nword")
    assert len(pieces) == 2
    assert all(len(piece) <= 800 for piece in pieces)


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
    assert tokenize("Ünïcode — привет ١٢") == ["ünïcode", "привет", "١٢"]


def test_tokenize_removes_every_stopword():
    stopwords = (
        "a an and are as at be by can do does for from how i in is it of on or "
        "that the this to was what when where which who why with you"
    )

    assert tokenize(stopwords.upper()) == []
    assert tokenize("Where is the cat?") == ["cat"]


# Ranking


def test_relevant_chunk_ranks_first():
    index = index_of(
        "Bananas are yellow fruit.",
        "Penguins live in Antarctica. Penguins swim.",
        "Penguins are birds that appear in many films and books and stories.",
    )

    results = index.search("Where do penguins live?")

    assert [r.chunk.file for r in results] == ["1.md", "2.md"]
    assert results[0].score > results[1].score > 0


def test_score_matches_the_bm25_formula():
    index = index_of("cat cat dog", "bird")

    (result,) = index.search("cat cat")

    n_chunks, containing, freq, length, average = 2, 1, 2, 3, 2.0
    idf = math.log(1 + (n_chunks - containing + 0.5) / (containing + 0.5))
    expected = (
        idf * freq * 2.5 / (freq + 1.5 * (1 - 0.75 + 0.75 * length / average))
    )
    assert result.score == pytest.approx(expected)


def test_score_sums_over_question_tokens():
    index = index_of("cat dog", "bird fish")

    both = index.search("cat dog")[0].score

    assert both == pytest.approx(
        index.search("cat")[0].score + index.search("dog")[0].score
    )


def test_chunk_length_excludes_stopwords():
    index = index_of("cat", "the cat is on a")

    first, second = index.search("cat")

    assert first.score == second.score


def test_returns_at_most_three_results():
    index = index_of(*["cat"] * 5)

    assert len(index.search("cat")) == 3


def test_ties_are_ordered_by_file_then_position():
    index = BM25Index(
        [
            Chunk("b.md", 0, "cat"),
            Chunk("a.md", 1, "cat"),
            Chunk("c.md", 0, "cat"),
            Chunk("a.md", 0, "cat"),
        ]
    )

    results = index.search("cat")

    assert [(r.chunk.file, r.chunk.position) for r in results] == [
        ("a.md", 0),
        ("a.md", 1),
        ("b.md", 0),
    ]


@pytest.mark.parametrize("question", ["zebra", "what is the", "?! _ ...", ""])
def test_no_usable_match_returns_empty_list(question):
    assert index_of("the cat is here", "what a dog").search(question) == []


def test_no_chunks_returns_empty_list():
    assert BM25Index([]).search("cat") == []


def test_chunks_without_tokens_do_not_break_search():
    assert index_of("the a an", "---").search("cat") == []


def test_index_reports_chunk_count():
    assert len(index_of("one", "two").chunks) == 2
