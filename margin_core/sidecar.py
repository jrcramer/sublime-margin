"""The sidecar file: everything Margin remembers about one document.

Stored next to the document as ``<name>.margin.json`` so the document itself
(and any pipeline that builds PDFs or web pages from it) stays untouched.

Items are anchored to lines *and* to the text of the first line, so they
survive edits: on load every anchor is re-located (exact line, then nearest
identical line, then nearest similar line). Anchors that cannot be found are
kept but flagged ``lost`` and only shown in the checklist.
"""

import datetime
import difflib
import io
import json
import os
import uuid

VERSION = 1

KINDS = ("structure", "direction", "fact", "stock", "option", "question", "language", "note")
STATUSES = ("open", "accepted", "rejected", "done")


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def new_id():
    return uuid.uuid4().hex[:8]


def path_for(doc_path, suffix=".margin.json"):
    base, _ = os.path.splitext(doc_path)
    return base + suffix


def empty(doc_path):
    return {
        "version": VERSION,
        "document": os.path.basename(doc_path),
        "session_id": None,
        "direction": None,
        "labels": [],      # structure labels: {anchor, label, function, source}
        "items": [],       # annotations / checklist entries
        # Only Claude's last answer: the conversation itself lives in the
        # Claude Code session (claude --resume <session_id>).
        "last_answer": None,  # {kind, at, question, text, error}
        "kinds_off": {},      # kind -> reason, turned off by Claude
        "kinds_on": [],       # kinds the writer turned back on; Claude leaves them on
    }


def load(path, doc_path=None):
    if not os.path.exists(path):
        return empty(doc_path or path)
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    base = empty(doc_path or path)
    base.update(data)
    # Older sidecars kept the whole conversation; keep only the last answer.
    old = base.pop("transcript", None)
    if old and not base.get("last_answer"):
        for e in reversed(old):
            if e.get("role") in ("claude", "error"):
                base["last_answer"] = {"kind": e.get("kind"), "at": e.get("at"), "question": None,
                                       "text": e.get("text", ""), "error": e["role"] == "error"}
                break
    return base


def off_kinds(data, writer_off):
    """Kinds not shown or asked for: the writer's own plus Claude's, minus kinds the writer kept on."""
    claude_off = set(data.get("kinds_off", {})) - set(data.get("kinds_on", []))
    return set(writer_off or []) | claude_off


def apply_kinds_off(data, entries):
    """Claude turning off a whole kind for this document. Returns kinds turned off."""
    done = []
    for e in entries or []:
        kind = e.get("kind") if isinstance(e, dict) else e
        if kind in KINDS and kind not in data.get("kinds_on", []):
            data.setdefault("kinds_off", {})[kind] = (e.get("reason") if isinstance(e, dict) else None) or ""
            done.append(kind)
    return done


def save(path, data):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def make_anchor(lines, line, end_line=None):
    line = max(0, min(line, len(lines) - 1)) if lines else 0
    end_line = line if end_line is None else max(line, min(end_line, len(lines) - 1))
    text = lines[line].strip() if lines else ""
    # "orig" never changes: a suggestion may only replace the text it was made for.
    return {"line": line, "end_line": end_line, "text": text, "orig": text, "lost": False}


def is_stale(anchor, lines):
    """True if the anchored line no longer reads as when the item was made."""
    if anchor.get("lost") or anchor["line"] >= len(lines):
        return True
    return lines[anchor["line"]].strip() != anchor.get("orig", anchor.get("text"))


def relocate(anchor, lines, window=400):
    """Update anchor in place for the current text. Returns True if found."""
    text = anchor.get("text", "")
    span = anchor.get("end_line", anchor["line"]) - anchor["line"]
    old = anchor["line"]
    if not text:
        anchor["lost"] = old >= len(lines)
        return not anchor["lost"]
    if 0 <= old < len(lines) and lines[old].strip() == text:
        anchor["lost"] = False
        return True
    stripped = [l.strip() for l in lines]
    lo, hi = max(0, old - window), min(len(lines), old + window + 1)
    exact = [i for i in range(lo, hi) if stripped[i] == text]
    if not exact:
        exact = [i for i, s in enumerate(stripped) if s == text]
    if exact:
        found = min(exact, key=lambda i: abs(i - old))
    else:
        best, found = 0.0, None
        for i in range(lo, hi):
            if not stripped[i]:
                continue
            r = difflib.SequenceMatcher(None, text, stripped[i]).quick_ratio()
            if r >= 0.75:
                r = difflib.SequenceMatcher(None, text, stripped[i]).ratio()
            if r > best or (r == best and found is not None and abs(i - old) < abs(found - old)):
                best, found = r, i
        if best < 0.75:
            anchor["lost"] = True
            return False
        anchor["text"] = stripped[found]
    anchor["line"] = found
    anchor["end_line"] = min(len(lines) - 1, found + span)
    anchor["lost"] = False
    return True


def relocate_all(data, lines):
    for it in data["items"]:
        if it.get("anchor"):
            relocate(it["anchor"], lines)
    for lb in data["labels"]:
        relocate(lb["anchor"], lines)


def add_item(data, lines, kind, title, body="", line=None, end_line=None,
             source="claude", origin=None, suggestion=None, refs=None, round_no=None):
    item = {
        "id": new_id(),
        "kind": kind if kind in KINDS else "note",
        "title": title,
        "body": body,
        "anchor": make_anchor(lines, line, end_line) if line is not None else None,
        "source": source,
        "origin": origin,
        "suggestion": suggestion,
        "refs": refs or [],
        "status": "open",
        "reason": None,
        "created": now(),
        "round": round_no,
    }
    data["items"].append(item)
    return item


def find(data, item_id):
    for it in data["items"]:
        if it["id"] == item_id:
            return it
    return None


WEIGHTS = ("high", "normal", "low")


def set_status(data, item_id, status, reason=None, by="user"):
    it = find(data, item_id)
    if it and status in STATUSES:
        it["status"] = status
        if reason is not None:
            it["reason"] = reason
        it["resolved"] = now()
        it["status_by"] = by
    return it


def apply_updates(data, updates):
    """Claude's re-weighing of open items. Returns the items changed.

    Claude may change the weight of an open item, close it as done, or reopen
    one it closed itself. What the writer decided is never touched.
    """
    changed = []
    for up in updates or []:
        it = find(data, str(up.get("id", "")))
        if not it:
            continue
        own = it["status"] == "open" or it.get("status_by") == "claude"
        if not own:
            continue
        # An item the writer reopened stays open; Claude may only re-weigh it.
        reopened = it["status"] == "open" and it.get("status_by") == "user"
        note = (up.get("reason") or "").strip() or None
        if up.get("weight") in WEIGHTS:
            it["weight"] = up["weight"]
        if up.get("status") in ("open", "done") and not reopened:
            set_status(data, it["id"], up["status"], by="claude")
            it["seen"] = False
        if note:
            it["claude_note"] = note
        changed.append(it)
    return changed


def set_labels(data, lines, labels, source="claude"):
    """Set unit labels.

    Claude's labels are replaced as a set on every round that returns labels.
    A user label replaces whatever label sits on that line, and Claude never
    overwrites a user label.
    """
    if source == "claude":
        data["labels"] = [l for l in data["labels"] if l["source"] != "claude"]
    user_lines = set(l["anchor"]["line"] for l in data["labels"] if l["source"] == "user")
    for lb in labels:
        if source == "claude" and lb["line"] in user_lines:
            continue
        if source == "user":
            data["labels"] = [l for l in data["labels"] if l["anchor"]["line"] != lb["line"]]
        data["labels"].append({
            "anchor": make_anchor(lines, lb["line"]),
            "label": lb.get("label", ""),
            "function": lb.get("function"),
            "source": source,
        })
    data["labels"].sort(key=lambda l: l["anchor"]["line"])


def next_round(data):
    rounds = [it.get("round") or 0 for it in data["items"]]
    return (max(rounds) if rounds else 0) + 1


def decisions_text(data):
    """What the writer has decided so far, for the next prompt."""
    out = []
    for it in data["items"]:
        if it["status"] == "open":
            continue
        line = ""
        if it.get("anchor") and not it["anchor"].get("lost"):
            line = " (r.%d)" % (it["anchor"]["line"] + 1)
        reason = (" — reason: %s" % it["reason"]) if it.get("reason") else ""
        by = " by you" if it.get("status_by") == "claude" else ""
        out.append("- [%s%s] %s/%s%s: %s%s" % (it["status"], by, it["kind"], it["source"],
                                              line, it["title"], reason))
    return "\n".join(out) if out else "(no decisions yet)"


def open_items_text(data):
    out = []
    for it in data["items"]:
        if it["status"] != "open":
            continue
        line = ""
        if it.get("anchor") and not it["anchor"].get("lost"):
            line = " (r.%d)" % (it["anchor"]["line"] + 1)
        weight = (" [%s]" % it["weight"]) if it.get("weight", "normal") != "normal" else ""
        out.append("- [%s] %s%s%s: %s" % (it["id"], it["kind"], line, weight, it["title"]))
    return "\n".join(out) if out else "(none)"
