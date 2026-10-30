"""Extract the writer's own *stock* (loose notes, fragments) from a document.

Configured per profile under ``"notes"``:

    "notes": {
        "line_prefixes": ["%>", "%?"],          # comment lines marked as notes
        "environments": ["notes", "voorraad"],   # \\begin{notes} ... \\end{notes}
        "after_body": true                       # everything after body_end
    }

Notes that are just loose text inside the body cannot be told apart
mechanically; the prompt asks Claude to recognise those itself.
"""

import re

from .structure import body_range


def extract(text, profile):
    cfg = profile.notes or {}
    lines = text.split("\n")
    notes = []

    prefixes = cfg.get("line_prefixes", [])
    for i, l in enumerate(lines):
        s = l.lstrip()
        for p in prefixes:
            if s.startswith(p):
                body = s[len(p):].strip()
                if body:
                    notes.append({"line": i, "end_line": i, "text": body, "how": "marked"})
                break

    for env in cfg.get("environments", []):
        b = re.compile(r"\\begin\{%s\}" % re.escape(env))
        e = re.compile(r"\\end\{%s\}" % re.escape(env))
        i = 0
        while i < len(lines):
            if b.search(lines[i]):
                j = i + 1
                while j < len(lines) and not e.search(lines[j]):
                    j += 1
                chunk = "\n".join(lines[i + 1:j]).strip()
                if chunk:
                    notes.append({"line": i + 1, "end_line": j - 1, "text": chunk, "how": env})
                i = j
            i += 1

    if cfg.get("after_body") and profile.body_end:
        _, last = body_range(lines, profile)
        tail_start = last + 2  # skip the body_end line itself
        chunk = "\n".join(lines[tail_start:]).strip()
        if chunk:
            notes.append({"line": tail_start, "end_line": len(lines) - 1,
                          "text": chunk, "how": "after-body"})

    notes.sort(key=lambda n: n["line"])
    return notes


def notes_text(notes):
    if not notes:
        return "(no marked notes)"
    return "\n".join("r.%d: %s" % (n["line"] + 1, n["text"].replace("\n", " / ")) for n in notes)
