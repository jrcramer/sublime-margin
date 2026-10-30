"""Rule-based structure parsing.

A *profile* describes how a document type marks its structure. Every rule
is a regular expression that is matched against single lines:

    {"kind": "section", "level": 2, "pattern": "^\\\\section\\*?\\{(.+?)\\}"}

* ``level``      smaller number = coarser unit (1 = part, 2 = section, ...)
* ``kind``       free name shown in the outline ("section", "move", ...)
* ``pattern``    regex; if it has a capture group, group 1 is the label
* ``separator``  true for marks such as ``---\\\\`` that only *divide* and
                 carry no label of their own

Lines matching an ``ignore`` pattern (e.g. a bare ``\\large``) are skipped.
Blank lines optionally create paragraph units below the deepest level.
Only the *body* of the document is parsed (``body_start`` / ``body_end``).
Pure Python, no Sublime imports: this module is unit-tested on its own.
"""

import re

PARAGRAPH_LEVEL = 99


class Unit(object):
    __slots__ = ("kind", "level", "label", "start", "end", "children", "rule_line")

    def __init__(self, kind, level, label, start, rule_line=None):
        self.kind = kind
        self.level = level
        self.label = label
        self.start = start          # 0-based first line of the unit
        self.end = start            # 0-based last line (inclusive)
        self.children = []
        self.rule_line = rule_line  # line holding the heading/separator, if any

    def walk(self):
        yield self
        for c in self.children:
            for u in c.walk():
                yield u

    def to_dict(self):
        return {
            "kind": self.kind,
            "level": self.level,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self):
        return "Unit(%s, %r, %d-%d)" % (self.kind, self.label, self.start, self.end)


class Profile(object):
    def __init__(self, data):
        self.name = data.get("name", "default")
        self.selector = data.get("selector", "")
        self.comment_prefix = data.get("comment_prefix")
        self.body_start = _rx(data.get("body_start"))
        self.body_end = _rx(data.get("body_end"))
        self.paragraphs = data.get("paragraphs", True)
        self.ignore = [re.compile(p) for p in data.get("ignore", [])]
        self.rules = []
        for r in data.get("structure", []):
            self.rules.append({
                "kind": r.get("kind", "section"),
                "level": int(r.get("level", 2)),
                "pattern": re.compile(r["pattern"]),
                "separator": bool(r.get("separator", False)),
            })
        self.notes = data.get("notes", {})
        self.context = data.get("context", "")


def _rx(p):
    return re.compile(p) if p else None


def body_range(lines, profile):
    """Return (first, last) 0-based body lines, inclusive."""
    if not lines:
        return 0, -1
    first, last = 0, len(lines) - 1
    if profile.body_start:
        for i, l in enumerate(lines):
            if profile.body_start.search(l):
                first = i + 1
                break
    if profile.body_end:
        for i in range(len(lines) - 1, first - 1, -1):
            if profile.body_end.search(lines[i]):
                last = i - 1
                break
    return first, max(first, last)


def is_comment(line, profile):
    p = profile.comment_prefix
    return bool(p) and line.lstrip().startswith(p)


def match_rule(line, profile):
    for r in profile.rules:
        m = r["pattern"].search(line)
        if m:
            label = None
            if not r["separator"] and m.groups():
                label = (m.group(1) or "").strip() or None
            return r, label
    return None, None


def parse(text, profile):
    """Parse text into a tree of Units. The root unit spans the body."""
    lines = text.split("\n")
    first, last = body_range(lines, profile)
    root = Unit("document", 0, None, first)
    root.end = last
    stack = [root]

    def close_until(level, at_line):
        while len(stack) > 1 and stack[-1].level >= level:
            u = stack.pop()
            u.end = max(u.start, at_line - 1)

    def open_unit(kind, level, label, start, rule_line=None):
        u = Unit(kind, level, label, start, rule_line)
        stack[-1].children.append(u)
        stack.append(u)
        return u

    para_open = False
    for i in range(first, last + 1):
        line = lines[i]
        if is_comment(line, profile) or any(rx.search(line) for rx in profile.ignore):
            continue
        rule, label = match_rule(line, profile)
        if rule:
            if para_open:
                close_until(PARAGRAPH_LEVEL, i)
                para_open = False
            close_until(rule["level"], i)
            # A separator line belongs to the unit it opens.
            open_unit(rule["kind"], rule["level"], label, i, rule_line=i)
            continue
        if not profile.paragraphs:
            continue
        if line.strip() == "":
            if para_open:
                close_until(PARAGRAPH_LEVEL, i)
                para_open = False
        elif not para_open:
            open_unit("paragraph", PARAGRAPH_LEVEL, None, i)
            para_open = True

    close_until(1, last + 1)
    root.end = last
    _wrap_opening(root)
    _trim(root, lines)
    return root


def _wrap_opening(root):
    """Paragraphs before the first marked unit become an implicit 'opening'.

    Without this, the text before the first separator would be invisible in
    the outline, while it is often where a text sets its tone.
    """
    kids = root.children
    first = next((i for i, c in enumerate(kids) if c.kind != "paragraph"), None)
    if not first:
        return
    lead = kids[:first]
    u = Unit("opening", kids[first].level, None, lead[0].start)
    u.end = kids[first].start - 1
    u.children = lead
    root.children = [u] + kids[first:]


def _trim(unit, lines):
    """Pull unit ends back over trailing blank lines."""
    for c in unit.children:
        _trim(c, lines)
    while unit.end > unit.start and lines[unit.end].strip() == "":
        unit.end -= 1


def outline_text(root, lines, labels=None, max_preview=60):
    """Compact, 1-based outline for prompts and quick panels.

    ``labels`` maps a 0-based start line to a user/Claude label.
    """
    labels = labels or {}
    out = []

    def preview(u):
        for i in range(u.start, u.end + 1):
            if u.rule_line == i:
                continue
            s = lines[i].strip().rstrip("\\").strip()
            if s:
                return s[:max_preview]
        return ""

    def rec(u, depth):
        for c in u.children:
            if c.kind == "paragraph":
                continue
            name = c.label or labels.get(c.start) or preview(c)
            out.append("%s%s r.%d-%d: %s (%d par.)" % (
                "  " * depth, c.kind, c.start + 1, c.end + 1, name,
                sum(1 for x in c.children if x.kind == "paragraph")))
            rec(c, depth + 1)

    rec(root, 0)
    if not out:
        n = sum(1 for c in root.children if c.kind == "paragraph")
        out.append("(no marked structure; %d paragraphs)" % n)
    return "\n".join(out)


def unit_at(root, line):
    """Deepest non-paragraph unit containing a 0-based line."""
    best = root
    for u in root.walk():
        if u.kind != "paragraph" and u.start <= line <= u.end and u.level >= best.level:
            best = u
    return best
