import os

from margin_core import sidecar as sc

LINES = ["alpha", "", "beta gamma delta", "epsilon", "zeta eta theta iota"]


def test_relocate_exact_moved_line():
    a = sc.make_anchor(LINES, 2)
    new = ["inserted"] * 3 + LINES
    assert sc.relocate(a, new)
    assert a["line"] == 5


def test_relocate_fuzzy_after_small_edit():
    a = sc.make_anchor(LINES, 4)
    new = list(LINES)
    new[4] = "zeta eta theta iotaa"
    assert sc.relocate(a, new)
    assert a["line"] == 4 and a["text"] == "zeta eta theta iotaa"


def test_relocate_lost():
    a = sc.make_anchor(LINES, 2)
    assert not sc.relocate(a, ["something completely different"] * 5)
    assert a["lost"]


def test_items_status_and_decisions():
    d = sc.empty("x.tex")
    it = sc.add_item(d, LINES, "fact", "not in the text", line=2, refs=["Gen 37:18"])
    sc.set_status(d, it["id"], "rejected", "pastoral reasons")
    txt = sc.decisions_text(d)
    assert "rejected" in txt and "pastoral reasons" in txt and "r.3" in txt
    assert sc.open_items_text(d) == "(none)"


def test_user_label_wins_and_survives_claude_rounds():
    d = sc.empty("x.tex")
    sc.set_labels(d, LINES, [{"line": 0, "label": "mine"}], source="user")
    sc.set_labels(d, LINES, [{"line": 0, "label": "claude"}, {"line": 2, "label": "c2"}])
    sc.set_labels(d, LINES, [{"line": 3, "label": "c3"}])  # next round replaces claude set
    got = [(l["anchor"]["line"], l["label"], l["source"]) for l in d["labels"]]
    assert got == [(0, "mine", "user"), (3, "c3", "claude")]
    sc.set_labels(d, LINES, [{"line": 2, "label": "mine2"}], source="user")
    assert [l["label"] for l in d["labels"]] == ["mine", "mine2", "c3"]


def test_save_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "doc.margin.json")
    d = sc.empty("doc.tex")
    sc.add_item(d, LINES, "option", "ga naar de oudste broer", line=1)
    sc.save(p, d)
    back = sc.load(p, "doc.tex")
    assert back["items"][0]["title"] == "ga naar de oudste broer"
    assert sc.path_for("/a/b/preek.tex") == "/a/b/preek.margin.json"
    assert os.path.exists(p)


def test_stale_detection_after_edit():
    a = sc.make_anchor(LINES, 4)
    new = list(LINES)
    new[4] = "zeta eta theta iotaa"
    sc.relocate(a, new)
    assert sc.is_stale(a, new)          # found, but text differs from original
    assert not sc.is_stale(sc.make_anchor(LINES, 2), LINES)
