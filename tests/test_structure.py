from conftest import fixture, load_example, load_settings

from margin_core import notes, structure


def latex_profile():
    return structure.Profile(load_settings()["profiles"][0])


def sermon_profile():
    return structure.Profile(load_example("sermon-latex.sublime-settings")["profiles"][0])


def kinds(root):
    return [(u.kind, u.start + 1, u.end + 1) for u in root.walk() if u.kind != "document"]


def test_body_range_skips_preamble_and_tail():
    text = fixture("sermon.tex")
    lines = text.split("\n")
    first, last = structure.body_range(lines, latex_profile())
    assert lines[first - 1].startswith("\\begin{document}")
    assert lines[last + 1].startswith("\\end{document}")


def test_separators_make_sections_with_paragraphs():
    root = structure.parse(fixture("sermon.tex"), latex_profile())
    top = [c for c in root.children]
    assert [c.kind for c in top] == ["opening", "section", "section"]
    assert len(top[0].children) == 3
    sec1 = top[1]
    assert sec1.label is None  # separators carry no label
    assert sec1.start + 1 == 14  # the ---\\ line opens the unit
    assert all(c.kind == "paragraph" for c in sec1.children)


def test_bold_titles_become_moves_in_sermon_profile():
    root = structure.parse(fixture("sermon.tex"), sermon_profile())
    moves = [u for u in root.walk() if u.kind == "move"]
    assert [m.label for m in moves] == ["The open door", "The elder brother"]
    # moves sit inside the first section and end before the next separator
    sec1 = [u for u in root.children if u.kind == "section"][0]
    assert moves[0] in sec1.children and moves[1] in sec1.children
    assert moves[1].end + 1 == 24


def test_comment_lines_are_ignored_for_structure():
    root = structure.parse(fixture("sermon.tex"), sermon_profile())
    for u in root.walk():
        if u.kind == "paragraph":
            assert not fixture("sermon.tex").split("\n")[u.start].lstrip().startswith("%")


def test_outline_text_is_one_based():
    text = fixture("sermon.tex")
    root = structure.parse(text, sermon_profile())
    out = structure.outline_text(root, text.split("\n"))
    assert "section r.14-" in out
    assert "move r.19-" in out and "The open door" in out


def test_unit_at():
    root = structure.parse(fixture("sermon.tex"), sermon_profile())
    assert structure.unit_at(root, 19).label == "The open door"


def test_notes_marked_and_after_body():
    text = fixture("sermon.tex")
    ns = notes.extract(text, sermon_profile())
    hows = [n["how"] for n in ns]
    assert hows == ["marked", "after-body"]
    assert ns[0]["text"].startswith("note: prodigal son")
    assert "two pitfalls" in ns[1]["text"]


def test_markdown_profile():
    md = "# Title\n\nIntro para.\n\n## One\n\ntext\n\n---\n\nmore\n\n## Two\n\nend\n"
    root = structure.parse(md, structure.Profile(load_settings()["profiles"][1]))
    secs = [u for u in root.walk() if u.kind == "section"]
    assert [s.label for s in secs] == ["One", None, "Two"]


def test_empty_document():
    root = structure.parse("", latex_profile())
    assert root.children == []
