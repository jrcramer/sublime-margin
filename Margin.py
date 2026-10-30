"""Margin: a structure view and a Claude Code sparring partner for writers.

Sublime layer only. All logic that does not need Sublime lives in margin_core.
"""

import html
import os
import threading

import sublime
import sublime_plugin

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
ROUND_NAMES = {"mirror": "Mirror", "stock": "Stock", "ask": "Question", "language": "Language"}

_docs = {}       # view.id() -> Doc
_running = set()  # file paths with a round in progress


def settings():
    return sublime.load_settings(SETTINGS)


def setting(key, default=None):
    v = settings().get(key)
    return default if v is None else v


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
        if self.data["items"] or self.data["labels"] or self.data["session_id"]:
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
    if not doc.visible:
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
            response_language=settings().get("response_language"))
    except Exception as e:
        sublime.error_message("Margin: could not build prompt: %s" % e)
        return

    _running.add(doc.path)
    spinner(view, doc.path, ROUND_NAMES[kind], 0)
    round_no = sc.next_round(doc.data)
    doc.data["transcript"].append({"role": "user", "kind": kind, "at": sc.now(),
                                   "text": question or ROUND_NAMES[kind]})

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
            doc.data["transcript"].append({"role": "error", "kind": kind, "at": sc.now(), "text": err})
            doc.save()
            show_panel(view.window(), doc)
            sublime.status_message("Margin: Claude failed, see panel.")
            return
        doc.data["session_id"] = sid
        prose, payload = protocol.parse_response(result)
        lines = doc.lines()
        added = protocol.apply_payload(doc.data, lines, payload, round_no, kind)
        doc.data["transcript"].append({"role": "claude", "kind": kind, "at": sc.now(),
                                       "text": prose, "items": [a["id"] for a in added]})
        doc.save()
        doc.visible = True
        render(doc)
        show_panel(view.window(), doc)
        sublime.status_message("Margin: %s round done, %d items." % (ROUND_NAMES[kind], len(added)))

    threading.Thread(target=work, daemon=True).start()


def spinner(view, path, label, i):
    if path not in _running:
        view.erase_status("margin")
        return
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    view.set_status("margin", "Margin %s %s" % (label, frames[i % len(frames)]))
    sublime.set_timeout(lambda: spinner(view, path, label, i + 1), 120)


def transcript_text(doc, last_only=False):
    entries = doc.data["transcript"]
    if last_only:
        # last user entry and everything after it
        idx = max([i for i, e in enumerate(entries) if e["role"] == "user"] or [0])
        entries = entries[idx:]
    out = []
    for e in entries:
        if e["role"] == "user":
            out.append("## %s  (%s)\n" % (e["text"], e["at"]))
        elif e["role"] == "error":
            out.append("**Error:** %s\n" % e["text"])
        else:
            out.append(e["text"].strip() + "\n")
    if doc.data.get("direction"):
        out.append("\n---\n*Direction:* %s\n" % doc.data["direction"])
    return "\n".join(out)


def show_panel(window, doc, last_only=True):
    panel = window.create_output_panel(PANEL)
    panel.assign_syntax("Packages/Markdown/Markdown.sublime-syntax")
    panel.settings().set("word_wrap", True)
    panel.settings().set("line_numbers", False)
    panel.settings().set("gutter", False)
    panel.run_command("append", {"characters": transcript_text(doc, last_only)})
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


class MarginToggleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        doc = require_doc(self.view)
        if doc:
            doc.visible = not doc.visible
            doc.refresh()
            render(doc)


class MarginShowAnswerCommand(sublime_plugin.WindowCommand):
    def run(self, full=False):
        doc = require_doc(self.window.active_view())
        if doc:
            show_panel(self.window, doc, last_only=not full)


class MarginOutlineCommand(sublime_plugin.TextCommand):
    """Jump through the structure (units and labels)."""
    def run(self, edit):
        doc = require_doc(self.view)
        if not doc:
            return
        doc.refresh()
        lines = doc.lines()
        labels = dict((l["anchor"]["line"], l) for l in doc.data["labels"] if not l["anchor"].get("lost"))
        entries = []
        for u in doc.root.walk():
            if u.kind == "document":
                continue
            lb = labels.get(u.start)
            if u.kind == "paragraph" and not lb:
                continue
            indent = "    " if u.kind == "paragraph" else "  " * max(0, u.level - 1)
            name = (lb and lb["label"]) or u.label or first_words(lines, u)
            detail = (lb and lb.get("function")) or ""
            entries.append((u.start, sublime.QuickPanelItem(
                indent + name, detail, "r.%d–%d %s" % (u.start + 1, u.end + 1, u.kind))))
        if not entries:
            sublime.status_message("Margin: no structure found.")
            return
        self.view.window().show_quick_panel(
            [e[1] for e in entries],
            lambda i: i >= 0 and goto(self.view, entries[i][0]),
            on_highlight=lambda i: goto(self.view, entries[i][0], select=False))


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
                 if (kind is None or it["kind"] == kind) and (status == "all" or it["status"] == status)]
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
        if not doc or not doc.visible:
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

    def on_hover(self, view, point, zone):
        doc = doc_for(view, create=False)
        if not doc or not doc.visible or zone != sublime.HOVER_GUTTER:
            return
        row = view.rowcol(point)[0]
        hits = [it for it in doc.data["items"]
                if it.get("anchor") and not it["anchor"].get("lost")
                and (it["status"] == "open" or setting("show_resolved", False))
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
