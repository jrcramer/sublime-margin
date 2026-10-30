from conftest import fixture, load_example

from margin_core import protocol, structure
from margin_core import sidecar as sc

ANSWER = """De tekst beweegt van dwang naar vrijheid.

r.13 opent goed.

```json
{
  "direction": "Van dwang naar ruimte.",
  "labels": [{"line": 14, "label": "Onverteerbaar", "function": "keerpunt"}],
  "items": [
    {"kind": "fact", "line": 15, "title": "Niet letterlijk NBV21", "refs": ["Joh 6:67"], "origin": "text"},
    {"kind": "option", "title": "Eindig bij de oudste broer"},
    {"kind": "stock", "line": 999, "title": "out of range line is kept unanchored"},
    {"kind": "option", "title": ""}
  ]
}
```
"""


def profile():
    return structure.Profile(load_example("sermon-latex.sublime-settings")["profiles"][0])


def test_parse_response_splits_prose_and_payload():
    prose, payload = protocol.parse_response(ANSWER)
    assert "```" not in prose and prose.startswith("De tekst")
    assert payload["direction"] == "Van dwang naar ruimte."


def test_parse_response_without_json():
    prose, payload = protocol.parse_response("Just prose.")
    assert payload == {} and prose == "Just prose."


def test_apply_payload():
    text = fixture("sermon.tex")
    lines = text.split("\n")
    d = sc.empty("sermon.tex")
    _, payload = protocol.parse_response(ANSWER)
    added = protocol.apply_payload(d, lines, payload, 1, "mirror")
    assert len(added) == 3  # empty title dropped
    assert added[0]["anchor"]["line"] == 14 and added[0]["refs"] == ["Joh 6:67"]
    assert added[1]["anchor"] is None and added[2]["anchor"] is None
    assert d["direction"] == "Van dwang naar ruimte."
    assert d["labels"][0]["anchor"]["line"] == 13


def test_build_all_rounds_fill_placeholders():
    text = fixture("sermon.tex")
    root = structure.parse(text, profile())
    d = sc.empty("sermon.tex")
    it = sc.add_item(d, text.split("\n"), "option", "older brother", line=20)
    sc.set_status(d, it["id"], "rejected", "too long already")
    for kind in protocol.ROUND_KINDS:
        system, user = protocol.build(kind, text, root, profile(), d, "sermon.tex",
                                      selection="r.1-2", question="Is this clear?",
                                      response_language="Dutch")
        assert "{{" not in system and "{{" not in user
        assert "too long already" in system
        assert "Write your prose in Dutch" in system
    _, user = protocol.build("mirror", text, root, profile(), d, "sermon.tex")
    assert "14| ---\\\\" in user
    assert "note: prodigal son" in user


def test_numbered_width():
    out = protocol.numbered(["a"] * 12)
    assert out.split("\n")[0] == " 1| a" and out.split("\n")[-1] == "12| a"
