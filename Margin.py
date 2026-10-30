"""Margin: a structure view and a Claude Code sparring partner for writers.

Sublime layer only. All logic that does not need Sublime lives in margin_core.
"""

import html
import importlib
import os
import re
import shlex
import subprocess
import sys
import threading

import sublime
import sublime_plugin

# Sublime reloads this file after an update, but not margin_core: reload it
# here (dependencies first) so a git pull needs no restart.
for _name in ("structure", "notes", "sidecar", "protocol", "claude_runner"):
    _mod = sys.modules.get("%s.margin_core.%s" % (__package__, _name))
    if _mod is not None:
        importlib.reload(_mod)

from .margin_core import claude_runner, protocol
from .margin_core import sidecar as sc
from .margin_core import structure as st

PKG = "Margin"
SETTINGS = "Margin.sublime-settings"
PANEL = "margin"
KIND_LETTERS = {
    "structure": "S", "direction": "D", "fact": "F", "stock": "V",
    "option": "O", "question": "?", "language": "L", "note": "N",
}
KIND_NAMES = {
    "structure": "structure", "direction": "direction", "fact": "fact", "stock": "stock",
    "option": "option", "question": "question", "language": "language", "note": "note",
}
ROUND_NAMES = {"mirror": "Mirror", "stock": "Stock", "ask": "Question", "react": "Reaction",
               "language": "Language"}
WEIGHT_ORDER = {"high": 0, "normal": 1, "low": 2}

_docs = {}       # view.id() -> Doc
_running = set()  # file paths with a round in progress


def settings():
    return sublime.load_settings(SETTINGS)


def setting(key, default=None):
    v = settings().get(key)
    return default if v is None else v


def off_kinds(doc):
    """Kinds switched off: by the writer (skip_kinds, the legend) or by Claude for this text."""
    return sc.off_kinds(doc.data, setting("skip_kinds", []))


def shown(doc, it):
    return it["kind"] not in off_kinds(doc)


def runner_settings():
    keys = ("claude_command", "extra_path", "claude_tools", "model",
            "claude_extra_args", "timeout")
    return dict((k, settings().get(k)) for k in keys)


# --------------------------------------------------------------------------
# Per-document state


class Doc(object):
    def __init__(self, view):
        self.view = view
        self.path = view.file_name()
        self.profile = self._pick_profile()
        self.sidecar_path = sc.path_for(self.path, setting("sidecar_suffix", ".margin.json"))
        self.data = sc.load(self.sidecar_path, self.path)
        self.visible = False
        self.collapsed = set()  # sidebar sections folded in
        self.root = None
        self.refresh()

    def _pick_profile(self):
        for p in setting("profiles", []):
            sel = p.get("selector", "")
            if not sel or self.view.match_selector(0, sel):
                return st.Profile(p)
        return st.Profile({"name": "Plain text"})

    def text(self):
        return self.view.substr(sublime.Region(0, self.view.size()))

    def lines(self):
        return self.text().split("\n")

    def refresh(self):
        text = self.text()
        self.root = st.parse(text, self.profile)
        sc.relocate_all(self.data, text.split("\n"))

    def save(self):
        if self.data["items"] or self.data["labels"] or self.data["session_id"] or self.data["last_answer"]:
            sc.save(self.sidecar_path, self.data)


def doc_for(view, create=True):
    if view is None or not view.file_name():
        return None
    d = _docs.get(view.id())
    if d is None and create:
        d = Doc(view)
        _docs[view.id()] = d
    return d


def require_doc(view):
    d = doc_for(view)
    if d is None:
        sublime.status_message("Margin: save the document first.")
    return d


# --------------------------------------------------------------------------
# Rendering the margin


def line_region(view, line):
    return view.line(view.text_point(line, 0))


def render(doc):
    view = doc.view
    clear(view)
    update_sidebar(doc)
    if not doc.visible or not setting("inline_annotations", True):
        return
    colors = setting("colors", {})

    if setting("show_labels", True):
        regs, notes = [], []
        heads = set()
        for u in doc.root.walk():
            if u.kind in ("document", "paragraph"):
                continue
            heads.add(u.start)
        labels = dict((l["anchor"]["line"], l) for l in doc.data["labels"]
                      if not l["anchor"].get("lost"))
        for line in sorted(heads | set(labels)):
            if line >= view.rowcol(view.size())[0] + 1:
                continue
            regs.append(line_region(view, line))
            lb = labels.get(line)
            if lb:
                tag = "<b>%s</b>" % esc(lb["label"]) if lb["source"] == "user" else "<i>%s</i>" % esc(lb["label"])
                notes.append('<span class="margin-label">%s</span>' % tag)
            else:
                notes.append("")
        view.add_regions("margin.labels", regs, "comment", "dot",
                         sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.PERSISTENT,
                         annotations=notes, annotation_color=colors.get("label", "#888"))

    show_resolved = setting("show_resolved", False)
    by_kind = {}
    for it in doc.data["items"]:
        a = it.get("anchor")
        if not a or a.get("lost"):
            continue
        if it["status"] != "open" and not show_resolved:
            continue
        if not shown(doc, it):
            continue
        by_kind.setdefault(it["kind"], []).append(it)

    for kind, items in by_kind.items():
        regs, notes = [], []
        for it in sorted(items, key=lambda i: i["anchor"]["line"]):
            regs.append(line_region(view, it["anchor"]["line"]))
            notes.append(annotation_html(it))
        view.add_regions(
            "margin.item.%s" % kind, regs, "region.bluish", "circle",
            sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.PERSISTENT,
            annotations=notes, annotation_color=colors.get(kind, "#888"),
            on_navigate=lambda href, v=view: on_navigate(v, href))


def clear(view):
    view.erase_regions("margin.labels")
    for k in KIND_LETTERS:
        view.erase_regions("margin.item.%s" % k)


def esc(s):
    return html.escape(s or "", quote=False)


def annotation_html(it):
    mark = "" if it["status"] == "open" else " [%s]" % it["status"]
    who = "" if it["source"] == "user" else ("·" if it.get("origin") == "note" else "")
    return ('<a href="open:{id}">{k} {t}{m}{w}</a> '
            '<a href="accepted:{id}">✓</a> <a href="rejected:{id}">✗</a>').format(
        id=it["id"], k=KIND_LETTERS.get(it["kind"], "•"), t=esc(it["title"]), m=mark, w=who)


def item_html(it):
    parts = ["<b>%s</b> <i>(%s, %s%s)</i>" % (
        esc(it["title"]), it["kind"], it["source"],
        (", " + it["origin"]) if it.get("origin") else "")]
    if it.get("body"):
        parts.append(esc(it["body"]).replace("\n", "<br>"))
    if it.get("refs"):
        parts.append("<i>refs: %s</i>" % esc(", ".join(it["refs"])))
    if it.get("suggestion"):
        parts.append("<b>Suggestion:</b><br><code>%s</code>" % esc(it["suggestion"]).replace("\n", "<br>"))
    if it.get("reason"):
        parts.append("<i>Reason: %s</i>" % esc(it["reason"]))
    links = ['<a href="accepted:{id}">accept</a>', '<a href="rejected:{id}">reject…</a>',
             '<a href="done:{id}">done</a>', '<a href="open-again:{id}">reopen</a>']
    if it.get("suggestion"):
        links.insert(0, '<a href="apply:{id}">apply</a>')
    parts.append(" · ".join(links).format(id=it["id"]))
    return "<body id='margin'><div style='padding:4px'>%s</div></body>" % "<br><br>".join(parts)


def on_navigate(view, href):
    action, _, item_id = href.partition(":")
    doc = doc_for(view)
    if not doc:
        return
    it = sc.find(doc.data, item_id)
    if not it:
        return
    if action == "open":
        view.show_popup(item_html(it), sublime.HIDE_ON_MOUSE_MOVE_AWAY, max_width=700,
                        location=view.text_point(it["anchor"]["line"], 0),
                        on_navigate=lambda h: (view.hide_popup(), on_navigate(view, h)))
    elif action == "apply":
        view.run_command("margin_apply_suggestion", {"item_id": item_id})
    elif action == "open-again":
        resolve(view, item_id, "open")
    else:
        resolve(view, item_id, action)


def resolve(view, item_id, status):
    doc = doc_for(view)
    it = sc.find(doc.data, item_id)
    if not it:
        return

    def finish(reason=None):
        sc.set_status(doc.data, item_id, status, reason)
        doc.save()
        render(doc)
        update_sidebar(doc, repaint=True)

    if status == "rejected":
        view.window().show_input_panel(
            "Why not? (Claude will not raise this again unless the reason stops applying)",
            it.get("reason") or "", lambda r: finish(r.strip() or None), None, None)
    elif status == "accepted":
        view.window().show_input_panel(
            "Note for yourself (optional):", it.get("reason") or "",
            lambda r: finish(r.strip() or None), None, lambda: finish(None))
    else:
        finish()


# --------------------------------------------------------------------------
# Sidebar: an HTML sheet next to the document with direction, structure,
# checklist and Claude's last answer. Links run margin_sidebar_action.

_sidebars = {}  # view.id() -> HtmlSheet
_sidebar_last = {}  # view.id() -> last HTML sent to the sheet

SIDEBAR_CSS = """
body { padding: 0.6rem 1rem; font-size: 0.95rem; }
.nav { margin-top: 0.4rem; font-size: 0.85rem; }
.legend { margin-top: 0.4rem; font-size: 0.85rem; }
.low { color: color(var(--foreground) alpha(0.6)); }
h2 { font-size: 1.05rem; margin: 1rem 0 0.3rem 0; }
a { text-decoration: none; }
.dim { color: color(var(--foreground) alpha(0.55)); }
.dir { font-style: italic; }
.unit { margin: 0.1rem 0; }
.item { margin: 0.35rem 0; }
.answer { margin-top: 0.3rem; }
.bar a { padding-right: 0.8rem; }
"""


def sidebar_for(doc):
    sheet = _sidebars.get(doc.view.id())
    if sheet is not None and sheet.window() is None:  # closed by the user
        _sidebars.pop(doc.view.id(), None)
        sheet = None
    return sheet


def open_sidebar(doc):
    window = doc.view.window()
    if window.num_groups() == 1:
        window.set_layout({"cols": [0.0, 0.68, 1.0], "rows": [0.0, 1.0],
                           "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]})
    group = 1 if window.get_view_index(doc.view)[0] == 0 else 0
    sheet = window.new_html_sheet("Margin: " + os.path.basename(doc.path),
                                  sidebar_html(doc), group=group)
    _sidebars[doc.view.id()] = sheet
    window.focus_view(doc.view)
    return sheet


def update_sidebar(doc, repaint=False):
    sheet = sidebar_for(doc)
    if sheet is not None:
        # Skip unchanged content: set_contents scrolls the sheet back to the top,
        # which would happen on every pause while typing.
        content = sidebar_html(doc)
        if content == _sidebar_last.get(doc.view.id()) and not repaint:
            return
        _sidebar_last[doc.view.id()] = content
        sheet.set_contents(content)
        if repaint:
            # An HTML sheet that is not focused may not redraw until clicked;
            # focus it for a moment and give focus back.
            window = sheet.window()
            back = window.active_sheet()
            window.focus_sheet(sheet)
            if back is not None:
                window.focus_sheet(back)


def close_sidebar(view_id):
    sheet = _sidebars.pop(view_id, None)
    _sidebar_last.pop(view_id, None)
    if sheet is not None and sheet.window() is not None:
        sheet.close()


def act(doc, action, **args):
    args.update(view_id=doc.view.id(), action=action)
    return "subl:margin_sidebar_action " + html.escape(sublime.encode_value(args))


def prose_html(text):
    out = []
    for para in (text or "").strip().split("\n\n"):
        p = esc(para)
        p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        p = re.sub(r"`([^`]+)`", r"<code>\1</code>", p)
        p = re.sub(r"^#+\s*(.+)$", r"<b>\1</b>", p, flags=re.M)
        out.append("<p>%s</p>" % p.replace("\n", "<br>"))
    return "".join(out)


SECTIONS = [("direction", "Direction"), ("structure", "Structure"), ("checklist", "Checklist"),
            ("claude", "Changed by Claude"), ("answer", "Last answer")]


def item_line(doc, it, actions):
    a = it.get("anchor")
    here = a and not a.get("lost")
    color = setting("colors", {}).get(it["kind"], "#888")
    title = esc(it["title"])
    if here:
        title = "<a href='%s'>%s</a>" % (act(doc, "show", item_id=it["id"]), title)
    cls = "item low" if it.get("weight") == "low" else "item"
    weight = {"high": " <b>!</b>", "low": " <span class='dim'>(low)</span>"}.get(it.get("weight"), "")
    note = ('<br><span class="dim">Claude: %s</span>' % esc(it["claude_note"])) if it.get("claude_note") else ""
    links = "&nbsp;&nbsp;".join("<a href='%s'>%s</a>" % (href, label) for label, href in actions)
    return ('<div class="%s"><span style="color: %s"><b>%s</b></span> %s%s '
            '<span class="dim">%s</span>%s<br>%s</div>' % (
                cls, color, KIND_LETTERS.get(it["kind"], "•"), title, weight,
                ("r.%d" % (a["line"] + 1)) if here else "—", note, links))


def sidebar_html(doc):
    d = doc.data
    out = ["<style>%s</style>" % SIDEBAR_CSS]

    rounds = [("mirror", "Mirror"), ("stock", "Stock"), ("ask", "Ask…"), ("react", "React…")]
    if doc.path in _running:
        out.append('<div class="dim">Claude is reading…</div>')
    else:
        out.append('<div class="bar">%s</div>' % "".join(
            '<a href=\'%s\'>%s</a>' % (act(doc, "round", kind=k), name) for k, name in rounds))

    # Section bar: click a name to show only that section, "all" for everything.
    out.append('<div class="bar nav">%s<a href=\'%s\'>all</a></div>' % (
        "".join("<a href='%s'>%s</a>" % (act(doc, "solo", section=k), name) for k, name in SECTIONS),
        act(doc, "solo", section="")))

    # Legend: click a kind to switch it off (Claude leaves it out too) or on.
    off = off_kinds(doc)
    by_claude = set(d.get("kinds_off", {})) - set(d.get("kinds_on", []))
    colors = setting("colors", {})
    legend = []
    for k in KIND_LETTERS:
        style = "color: %s" % colors.get(k, "#888")
        label = "<b>%s</b> %s" % (KIND_LETTERS[k], KIND_NAMES[k])
        if k in off:
            style = "color: color(var(--foreground) alpha(0.35))"
            label = "<s>%s</s>%s" % (label, " (Claude)" if k in by_claude else "")
        legend.append("<a href='%s' style='%s'>%s</a>" % (act(doc, "toggle_kind", kind=k), style, label))
    out.append('<div class="legend">%s</div>' % "&nbsp;&nbsp; ".join(legend))
    for k in sorted(by_claude - set(setting("skip_kinds", []))):
        reason = d["kinds_off"].get(k)
        out.append('<div class="dim">Claude turned off <i>%s</i>%s. Click it to turn it back on.</div>' % (
            KIND_NAMES.get(k, k), (": " + esc(reason)) if reason else ""))

    def section(key, title):
        closed = key in doc.collapsed
        out.append("<h2><a href='%s'>%s %s</a></h2>" % (
            act(doc, "collapse", section=key), "▸" if closed else "▾", title))
        return not closed

    if section("direction", "Direction"):
        out.append('<div class="dir">%s</div>' % (esc(d.get("direction")) or
                                                  '<span class="dim">not yet mirrored</span>'))

    if section("structure", "Structure"):
        entries = outline_entries(doc)
        if not entries:
            out.append('<div class="dim">no structure found</div>')
        for u, depth, name, function in entries:
            out.append('<div class="unit" style="margin-left: %.1frem">'
                       '<a href=\'%s\'>%s</a> <span class="dim">r.%d%s</span></div>' % (
                           depth * 0.9, act(doc, "goto", line=u.start), esc(name), u.start + 1,
                           (" · " + esc(function)) if function else ""))

    items = [it for it in d["items"] if it["status"] == "open" and shown(doc, it)]
    items.sort(key=lambda it: (WEIGHT_ORDER.get(it.get("weight"), 1),
                               (it["anchor"] or {}).get("line", 10 ** 9)))
    if section("checklist", "Checklist (%d open)" % len(items)):
        for it in items:
            acts = [("✓ agree", act(doc, "resolve", item_id=it["id"], status="accepted")),
                    ("✗ not this", act(doc, "resolve", item_id=it["id"], status="rejected")),
                    ("done", act(doc, "resolve", item_id=it["id"], status="done"))]
            if it.get("suggestion") and it.get("anchor") and not it["anchor"].get("lost"):
                acts.append(("apply", act(doc, "apply", item_id=it["id"])))
            out.append(item_line(doc, it, acts))

    # Items Claude closed that the writer has not looked at yet.
    closed = [it for it in d["items"] if it.get("status_by") == "claude"
              and it["status"] == "done" and not it.get("seen") and shown(doc, it)]
    if closed and section("claude", "Changed by Claude (%d)" % len(closed)):
        for it in closed:
            out.append(item_line(doc, it, [
                ("✓ fine", act(doc, "seen", item_id=it["id"])),
                ("reopen", act(doc, "resolve", item_id=it["id"], status="open"))]))

    last = d.get("last_answer")
    if section("answer", "Last answer"):
        if not last:
            out.append('<div class="dim">no rounds yet</div>')
        else:
            out.append('<div class="dim">%s · %s%s</div>' % (
                ROUND_NAMES.get(last.get("kind"), ""), (last.get("at") or "").replace("T", " ")[:16],
                (" · <a href='%s'>conversation in Terminal</a>" % act(doc, "terminal"))
                if d.get("session_id") else ""))
            if last.get("question"):
                out.append('<div class="dim"><i>%s</i></div>' % esc(last["question"]))
            if last.get("error"):
                out.append('<div class="answer"><b>Error:</b> %s</div>' % esc(last["text"]))
            else:
                out.append('<div class="answer">%s</div>' % prose_html(last["text"]))
    return "<body id='margin-sidebar'>%s</body>" % "".join(out)


class MarginSidebarCommand(sublime_plugin.TextCommand):
    """Show or hide the sidebar for this document."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        if sidebar_for(doc) is not None:
            close_sidebar(self.view.id())
        else:
            doc.refresh()
            open_sidebar(doc)


class MarginSidebarActionCommand(sublime_plugin.WindowCommand):
    """Clicks in the sidebar."""
    def run(self, view_id, action, line=None, item_id=None, status=None, kind=None, section=None):
        view = next((v for v in self.window.views() if v.id() == view_id), None)
        doc = view and doc_for(view, create=False)
        if not doc:
            return
        # Actions that only change the sidebar itself.
        if action == "collapse":
            doc.collapsed ^= {section}
            return update_sidebar(doc, repaint=True)
        if action == "solo":
            doc.collapsed = set(k for k, _ in SECTIONS if section and k != section)
            return update_sidebar(doc, repaint=True)
        if action == "toggle_kind":
            toggle_kind(doc, kind)
            render_all()
            return update_sidebar(doc, repaint=True)
        if action == "seen":
            it = sc.find(doc.data, item_id)
            if it:
                it["seen"] = True
                doc.save()
            return update_sidebar(doc, repaint=True)
        if action == "resolve":
            return resolve(view, item_id, status)
        self.window.focus_view(view)
        if action == "goto":
            goto(view, line)
        elif action == "round":
            view.run_command("margin_" + kind)
        elif action == "terminal":
            view.run_command("margin_open_terminal")
        elif action == "show":
            it = sc.find(doc.data, item_id)
            if it and it.get("anchor"):
                goto(view, it["anchor"]["line"])
                on_navigate(view, "open:" + item_id)
        elif action == "apply":
            view.run_command("margin_apply_suggestion", {"item_id": item_id})


def toggle_kind(doc, kind):
    """Writer's switch. Off: for every document (skip_kinds). Turning on a kind
    Claude turned off keeps it on for this document."""
    writer_off = set(setting("skip_kinds", []))
    if kind in writer_off:
        writer_off.discard(kind)
    elif kind in off_kinds(doc):
        doc.data.setdefault("kinds_on", []).append(kind)
        doc.data.get("kinds_off", {}).pop(kind, None)
        doc.save()
        return
    else:
        writer_off.add(kind)
        if kind in doc.data.get("kinds_on", []):
            doc.data["kinds_on"].remove(kind)
            doc.save()
    settings().set("skip_kinds", sorted(writer_off))
    sublime.save_settings(SETTINGS)


def render_all():
    for d in list(_docs.values()):
        if d.view.is_valid():
            render(d)


# --------------------------------------------------------------------------
# Rounds with Claude


def start_round(view, kind, question=None, selection=None):
    doc = require_doc(view)
    if not doc:
        return
    if doc.path in _running:
        sublime.status_message("Margin: Claude is still reading this document.")
        return
    if view.is_dirty():
        view.run_command("save")
    doc.refresh()
    text = doc.text()
    try:
        system, user = protocol.build(
            kind, text, doc.root, doc.profile, doc.data, os.path.basename(doc.path),
            selection=selection, question=question,
            override_dir=settings().get("prompt_dir"),
            response_language=settings().get("response_language"),
            skip_kinds=sorted(off_kinds(doc)))
    except Exception as e:
        sublime.error_message("Margin: could not build prompt: %s" % e)
        return

    sent_lines = text.split("\n")  # Claude's line numbers refer to this text
    _running.add(doc.path)
    update_sidebar(doc)
    spinner(view, doc.path, ROUND_NAMES[kind], 0)
    round_no = sc.next_round(doc.data)

    def work():
        try:
            result, sid = claude_runner.run(
                runner_settings(), system, user,
                session_id=doc.data.get("session_id"),
                cwd=os.path.dirname(doc.path))
            err = None
        except Exception as e:  # ClaudeError or anything unexpected
            result, sid, err = None, None, str(e)
        sublime.set_timeout(lambda: finish(result, sid, err), 0)

    def finish(result, sid, err):
        _running.discard(doc.path)
        if err:
            doc.data["last_answer"] = {"kind": kind, "at": sc.now(), "question": question,
                                       "text": err, "error": True}
            doc.save()
            update_sidebar(doc, repaint=True)
            if sidebar_for(doc) is None:
                show_panel(view.window(), doc)
            sublime.status_message("Margin: Claude failed, see %s." % (
                "sidebar" if sidebar_for(doc) else "panel"))
            return
        doc.data["session_id"] = sid
        prose, payload = protocol.parse_response(result)
        # Anchor to the text Claude read, then move the anchors to wherever
        # those lines are now, in case the writer kept writing meanwhile.
        added = protocol.apply_payload(doc.data, sent_lines, payload, round_no, kind,
                                       off_kinds(doc))
        updated = sc.apply_updates(doc.data, payload.get("updates"))
        sc.apply_kinds_off(doc.data, payload.get("kinds_off"))
        doc.refresh()
        doc.data["last_answer"] = {"kind": kind, "at": sc.now(), "question": question,
                                   "text": prose, "error": False}
        doc.save()
        doc.visible = True
        render(doc)
        update_sidebar(doc, repaint=True)
        if sidebar_for(doc) is None:
            show_panel(view.window(), doc)
        sublime.status_message("Margin: %s round done, %d new items, %d updated." % (
            ROUND_NAMES[kind], len(added), len(updated)))

    threading.Thread(target=work, daemon=True).start()


def spinner(view, path, label, i):
    if path not in _running:
        view.erase_status("margin")
        return
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    view.set_status("margin", "Margin %s %s" % (label, frames[i % len(frames)]))
    sublime.set_timeout(lambda: spinner(view, path, label, i + 1), 120)


def answer_text(doc):
    last = doc.data.get("last_answer") or {}
    out = []
    if last:
        out.append("## %s  (%s)\n" % (last.get("question") or ROUND_NAMES.get(last.get("kind"), ""),
                                       last.get("at")))
        out.append(("**Error:** %s\n" if last.get("error") else "%s\n") % last.get("text", "").strip())
    if doc.data.get("direction"):
        out.append("\n---\n*Direction:* %s\n" % doc.data["direction"])
    if doc.data.get("session_id"):
        out.append("\n*Whole conversation:* Margin: Open conversation in Terminal\n")
    return "\n".join(out) or "No rounds yet."


def show_panel(window, doc):
    panel = window.create_output_panel(PANEL)
    panel.assign_syntax("Packages/Markdown/Markdown.sublime-syntax")
    panel.settings().set("word_wrap", True)
    panel.settings().set("line_numbers", False)
    panel.settings().set("gutter", False)
    panel.run_command("append", {"characters": answer_text(doc)})
    panel.set_read_only(True)
    window.run_command("show_panel", {"panel": "output." + PANEL})


# --------------------------------------------------------------------------
# Commands


class MarginMirrorCommand(sublime_plugin.TextCommand):
    """Read along: structure as it is, direction, where it does not run, options."""
    def run(self, edit):
        start_round(self.view, "mirror")


class MarginStockCommand(sublime_plugin.TextCommand):
    """Inventory of notes and material not (yet) used."""
    def run(self, edit):
        start_round(self.view, "stock")


class MarginLanguageCommand(sublime_plugin.TextCommand):
    """Final round: spelling and typos, as applicable suggestions."""
    def run(self, edit):
        if sublime.ok_cancel_dialog("Run the language round (spelling, typos)?\n"
                                    "Best done once, when the text is nearly finished.", "Run"):
            start_round(self.view, "language")


class MarginAskCommand(sublime_plugin.TextCommand):
    """Ask Claude something, about the selection if there is one."""
    def run(self, edit):
        sel = "\n".join(self.view.substr(r) for r in self.view.sel() if not r.empty())
        if sel:
            first = self.view.rowcol(self.view.sel()[0].begin())[0] + 1
            last = self.view.rowcol(self.view.sel()[-1].end())[0] + 1
            sel = "r.%d-%d:\n%s" % (first, last, sel)
        caption = "Ask Claude about the selection:" if sel else "Ask Claude:"
        self.view.window().show_input_panel(
            caption, "", lambda q: q.strip() and start_round(self.view, "ask", q.strip(), sel),
            None, None)


class MarginReactCommand(sublime_plugin.TextCommand):
    """A general reaction to the last round; Claude re-weighs the open items."""
    def run(self, edit):
        self.view.window().show_input_panel(
            "Your reaction to Claude's last round (Claude will re-weigh the open items):", "",
            lambda q: q.strip() and start_round(self.view, "react", q.strip()), None, None)


class MarginToggleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        doc = require_doc(self.view)
        if doc:
            doc.visible = not doc.visible
            doc.refresh()
            render(doc)


class MarginShowAnswerCommand(sublime_plugin.WindowCommand):
    def run(self):
        doc = require_doc(self.window.active_view())
        if doc:
            show_panel(self.window, doc)


class MarginOpenTerminalCommand(sublime_plugin.TextCommand):
    """The whole conversation: this document's Claude session, in Terminal."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc or not doc.data.get("session_id"):
            sublime.status_message("Margin: no Claude conversation yet for this document.")
            return
        claude = (setting("claude_command", ["claude"]) or ["claude"])[0]
        cmd = "cd %s && %s --resume %s" % (shlex.quote(os.path.dirname(doc.path)),
                                          shlex.quote(claude), shlex.quote(doc.data["session_id"]))
        if sublime.platform() == "osx":
            script = 'tell application "Terminal" to do script "%s"' % cmd.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
        else:
            sublime.set_clipboard(cmd)
            sublime.status_message("Margin: command copied; paste it in a terminal.")


class MarginOutlineCommand(sublime_plugin.TextCommand):
    """Jump through the structure (units and labels)."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        doc.refresh()
        entries = []
        for u, depth, name, detail in outline_entries(doc):
            entries.append((u.start, sublime.QuickPanelItem(
                "  " * depth + name, detail, "r.%d–%d %s" % (u.start + 1, u.end + 1, u.kind))))
        if not entries:
            sublime.status_message("Margin: no structure found.")
            return
        self.view.window().show_quick_panel(
            [e[1] for e in entries],
            lambda i: i >= 0 and goto(self.view, entries[i][0]),
            on_highlight=lambda i: goto(self.view, entries[i][0], select=False))


def outline_entries(doc):
    """(unit, depth, name, function) for headings, separators and labelled paragraphs."""
    lines = doc.lines()
    labels = dict((l["anchor"]["line"], l) for l in doc.data["labels"] if not l["anchor"].get("lost"))
    out = []
    for u in doc.root.walk():
        if u.kind == "document":
            continue
        lb = labels.get(u.start)
        if u.kind == "paragraph" and not lb:
            continue
        depth = 2 if u.kind == "paragraph" else max(0, u.level - 1)
        name = (lb and lb["label"]) or u.label or first_words(lines, u)
        out.append((u, depth, name, (lb and lb.get("function")) or ""))
    return out


def first_words(lines, u, n=50):
    for i in range(u.start, u.end + 1):
        if i == u.rule_line:
            continue
        s = lines[i].strip().rstrip("\\").strip()
        if s:
            return s[:n]
    return u.kind


def goto(view, line, select=True):
    pt = view.text_point(line, 0)
    view.show_at_center(pt)
    if select:
        view.sel().clear()
        view.sel().add(sublime.Region(pt))
        view.window().focus_view(view)


class MarginLabelUnitCommand(sublime_plugin.TextCommand):
    """Give the unit at the cursor your own label (wins over Claude's)."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        doc.refresh()
        row = self.view.rowcol(self.view.sel()[0].begin())[0]
        # Prefer the paragraph or unit that starts closest above the cursor.
        starts = [u.start for u in doc.root.walk() if u.kind != "document" and u.start <= row]
        line = max(starts) if starts else row
        current = next((l["label"] for l in doc.data["labels"] if l["anchor"]["line"] == line), "")

        def done(label):
            label = label.strip()
            lines = doc.lines()
            if label:
                sc.set_labels(doc.data, lines, [{"line": line, "label": label}], source="user")
            else:
                doc.data["labels"] = [l for l in doc.data["labels"] if l["anchor"]["line"] != line]
            doc.save()
            doc.visible = True
            render(doc)
        self.view.window().show_input_panel("Label for r.%d:" % (line + 1), current, done, None, None)


class MarginNoteCommand(sublime_plugin.TextCommand):
    """Put a reminder of your own in the margin and checklist."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        row = self.view.rowcol(self.view.sel()[0].begin())[0]

        def done(text):
            if text.strip():
                sc.add_item(doc.data, doc.lines(), "note", text.strip(), line=row, source="user")
                doc.save()
                doc.visible = True
                render(doc)
        self.view.window().show_input_panel("Margin note:", "", done, None, None)


class MarginChecklistCommand(sublime_plugin.TextCommand):
    """All items: jump to one, then accept, reject with reason, mark done…"""
    def run(self, edit, kind=None, status="open"):
        doc = require_doc(self.view)
        if not doc:
            return
        doc.refresh()
        items = [it for it in doc.data["items"]
                 if (kind is None or it["kind"] == kind) and (status == "all" or it["status"] == status)
                 and shown(doc, it)]
        items.sort(key=lambda it: (it["anchor"] or {}).get("line", 10 ** 9))
        if not items:
            sublime.status_message("Margin: checklist is empty.")
            return
        qp = []
        for it in items:
            a = it.get("anchor")
            where = "r.%d" % (a["line"] + 1) if a and not a.get("lost") else "—"
            qp.append(sublime.QuickPanelItem(
                "%s  %s" % (KIND_LETTERS.get(it["kind"], "•"), it["title"]),
                (it.get("body") or "")[:200],
                "%s · %s%s" % (where, it["source"], "" if it["status"] == "open" else " · " + it["status"])))

        def chosen(i):
            if i < 0:
                return
            it = items[i]
            if it.get("anchor") and not it["anchor"].get("lost"):
                goto(self.view, it["anchor"]["line"])
            self.actions(it)

        def highlight(i):
            it = items[i]
            if it.get("anchor") and not it["anchor"].get("lost"):
                goto(self.view, it["anchor"]["line"], select=False)

        self.view.window().show_quick_panel(qp, chosen, on_highlight=highlight)

    def actions(self, it):
        acts = [("Accept", "accepted"), ("Reject, with reason…", "rejected"),
                ("Done", "done"), ("Reopen", "open"), ("Show explanation", "show")]
        if it.get("suggestion") and it["status"] == "open":
            acts.insert(0, ("Apply suggestion", "apply"))

        def pick(i):
            if i < 0:
                return
            a = acts[i][1]
            if a == "show":
                on_navigate(self.view, "open:" + it["id"])
            elif a == "apply":
                self.view.run_command("margin_apply_suggestion", {"item_id": it["id"]})
            else:
                resolve(self.view, it["id"], a)
        self.view.window().show_quick_panel([a[0] for a in acts], pick)


class MarginApplySuggestionCommand(sublime_plugin.TextCommand):
    def run(self, edit, item_id):
        doc = require_doc(self.view)
        it = doc and sc.find(doc.data, item_id)
        if not it or not it.get("suggestion") or not it.get("anchor") or it["anchor"].get("lost"):
            sublime.status_message("Margin: nothing to apply.")
            return
        apply_one(self.view, edit, doc, it)
        doc.refresh()
        doc.save()
        render(doc)


def apply_one(view, edit, doc, it):
    a = it["anchor"]
    if sc.is_stale(a, doc.lines()):
        sublime.status_message("Margin: r.%d changed since the suggestion; not applied." % (a["line"] + 1))
        return False
    start = view.text_point(a["line"], 0)
    end = view.line(view.text_point(a.get("end_line", a["line"]), 0)).end()
    view.replace(edit, sublime.Region(start, end), it["suggestion"].rstrip("\n"))
    sc.set_status(doc.data, it["id"], "done")
    return True


class MarginApplyAllLanguageCommand(sublime_plugin.TextCommand):
    """Apply every open language suggestion (bottom to top)."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        doc.refresh()
        items = [it for it in doc.data["items"] if it["kind"] == "language" and it["status"] == "open"
                 and it.get("suggestion") and it.get("anchor") and not it["anchor"].get("lost")]
        if not items or not sublime.ok_cancel_dialog(
                "Apply %d language suggestions? (Undo works as usual.)" % len(items), "Apply"):
            return
        for it in sorted(items, key=lambda i: i["anchor"]["line"], reverse=True):
            apply_one(self.view, edit, doc, it)
        doc.refresh()
        doc.save()
        render(doc)


class MarginNewSessionCommand(sublime_plugin.TextCommand):
    """Start a fresh Claude conversation; decisions and checklist are kept."""
    def run(self, edit):
        doc = require_doc(self.view)
        if doc and sublime.ok_cancel_dialog(
                "Start a new Claude conversation for this document?\n"
                "Checklist, labels and decisions stay.", "New conversation"):
            doc.data["session_id"] = None
            doc.save()


# --------------------------------------------------------------------------
# Events


class MarginEvents(sublime_plugin.EventListener):
    def on_load_async(self, view):
        self._maybe_show(view)

    def on_activated_async(self, view):
        if view.id() not in _docs:
            self._maybe_show(view)

    def _maybe_show(self, view):
        path = view.file_name()
        if not path or not setting("auto_show", True):
            return
        if os.path.exists(sc.path_for(path, setting("sidecar_suffix", ".margin.json"))):
            doc = doc_for(view)
            doc.visible = True
            render(doc)

    def on_modified_async(self, view):
        doc = doc_for(view, create=False)
        if not doc or not (doc.visible or sidebar_for(doc)):
            return
        cc = view.change_count()

        def later():
            if view.change_count() == cc and view.is_valid():
                doc.refresh()
                render(doc)
        sublime.set_timeout_async(later, 700)

    def on_post_save_async(self, view):
        doc = doc_for(view, create=False)
        if doc:
            doc.refresh()
            doc.save()

    def on_close(self, view):
        _docs.pop(view.id(), None)
        close_sidebar(view.id())

    def on_hover(self, view, point, zone):
        doc = doc_for(view, create=False)
        if not doc or not doc.visible or zone != sublime.HOVER_GUTTER:
            return
        row = view.rowcol(point)[0]
        hits = [it for it in doc.data["items"]
                if it.get("anchor") and not it["anchor"].get("lost")
                and (it["status"] == "open" or setting("show_resolved", False)) and shown(doc, it)
                and it["anchor"]["line"] <= row <= it["anchor"].get("end_line", it["anchor"]["line"])]
        if hits:
            body = "<hr>".join(item_html(h)[len("<body id='margin'>"):-len("</body>")] for h in hits)
            view.show_popup("<body id='margin'>%s</body>" % body, sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                            location=point, max_width=700,
                            on_navigate=lambda h: (view.hide_popup(), on_navigate(view, h)))


def plugin_unloaded():
    for w in sublime.windows():
        for v in w.views():
            clear(v)
    for vid in list(_sidebars):
        close_sidebar(vid)
