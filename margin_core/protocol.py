"""Building prompts and reading Claude's answers.

Claude answers in two parts: prose for the writer, then one fenced ```json
block that Margin turns into margin annotations. The contract is in
``prompts/system.md``; this module only fills templates and parses.
"""

import json
import os
import re

from . import notes as notes_mod
from . import sidecar as sc
from .structure import outline_text

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
ROUND_KINDS = ("mirror", "stock", "ask", "react", "language")


def numbered(lines, first=0, last=None):
    last = len(lines) - 1 if last is None else last
    width = len(str(last + 1))
    return "\n".join("%*d| %s" % (width, i + 1, lines[i]) for i in range(first, last + 1))


def load_template(name, override_dir=None):
    for d in (override_dir, PROMPT_DIR):
        if d:
            p = os.path.join(os.path.expanduser(d), name + ".md")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read()
    raise IOError("prompt template not found: %s" % name)


def fill(template, values):
    out = template
    for k, v in values.items():
        out = out.replace("{{%s}}" % k, v if v is not None else "")
    return out


def build(kind, text, root, profile, data, doc_name, selection=None,
          question=None, override_dir=None, response_language=None, skip_kinds=None):
    """Return (system_prompt, user_prompt) for a round."""
    if kind not in ROUND_KINDS:
        raise ValueError(kind)
    lines = text.split("\n")
    labels = dict((l["anchor"]["line"], l["label"]) for l in data.get("labels", []))
    values = {
        "document_name": doc_name,
        "document": numbered(lines),
        "outline": outline_text(root, lines, labels),
        "notes": notes_mod.notes_text(notes_mod.extract(text, profile)),
        "decisions": sc.decisions_text(data),
        "open_items": sc.open_items_text(data),
        "direction": data.get("direction") or "(not yet mirrored)",
        "context": profile.context or "(none)",
        "selection": selection or "(no selection)",
        "question": question or "",
        "language": response_language or "the language the document is written in",
        "skip_kinds": ", ".join(skip_kinds) if skip_kinds else "(none)",
    }
    system = fill(load_template("system", override_dir), values)
    user = fill(load_template(kind, override_dir), values)
    return system, user


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def parse_response(text):
    """Split an answer into (prose, payload dict). Payload may be empty."""
    payload, prose = {}, text or ""
    blocks = list(_FENCE.finditer(prose))
    for m in reversed(blocks):
        try:
            cand = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(cand, dict):
            payload = cand
            prose = (prose[:m.start()] + prose[m.end():]).strip()
            break
    return prose, payload


def apply_payload(data, lines, payload, round_no, kind, skip_kinds=()):
    """Merge Claude's structured answer into the sidecar. Returns new items."""
    added = []
    if payload.get("direction"):
        data["direction"] = payload["direction"]
    labels = []
    for lb in payload.get("labels") or []:
        ln = _line(lb.get("line"), lines)
        if ln is not None and lb.get("label"):
            labels.append({"line": ln, "label": lb["label"], "function": lb.get("function")})
    if labels:
        sc.set_labels(data, lines, labels, source="claude")
    for it in payload.get("items") or []:
        title = (it.get("title") or "").strip()
        if not title or it.get("kind") in skip_kinds:
            continue
        ln = _line(it.get("line"), lines)
        end = _line(it.get("end_line"), lines)
        added.append(sc.add_item(
            data, lines,
            kind=it.get("kind", "note"),
            title=title,
            body=it.get("body", ""),
            line=ln,
            end_line=end,
            source="claude",
            origin=it.get("origin"),
            suggestion=it.get("suggestion"),
            refs=it.get("refs") or [],
            round_no=round_no,
        ))
    return added


def _line(v, lines):
    """1-based line from Claude -> 0-based index, or None."""
    try:
        n = int(v) - 1
    except (TypeError, ValueError):
        return None
    if 0 <= n < len(lines):
        return n
    return None
