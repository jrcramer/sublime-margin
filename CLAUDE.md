# Margin — context for Claude

Sublime Text 4 plugin: structure view + Claude Code as a sparring partner that reads along *halfway through* writing. First user: a preacher who writes sermons in LaTeX (one spoken line per line, `---\\` divides sections, occasionally Buttrick-style moves with bold titles). The plugin itself must stay generic: no personal paths, profiles in settings.

## Design principles (agreed with the user, keep them)

- **Mirror, not plan.** Claude describes the structure and direction the text *has*; ways forward are options with gains/losses, never one recommended route. A structure that becomes Claude's makes it hard for the writer to own it.
- **Deviation is not error.** No upfront brief/plan is required. Claude shows what a deviating route leaves behind (unused "stock") and where it could still land.
- **Decisions stick.** Accept / reject-with-reason; a rejected point is not raised again unless the text changes so the reason no longer applies. Reasons may be non-exegetical (pastoral, context) — respect them.
- **Provenance visible.** Writer's notes (`origin: note`) vs Claude's additions (`origin: exegesis`).
- **Order of concern:** structure & direction → facts (with refs, quotations checked against a named translation) → options → language/spelling last, as a separate round.
- Pre-writing exegetical sparring stays in Claude chat, not in the plugin. The plugin is for joining mid-process.
- The document is never modified except when applying a language suggestion. All state lives in a sidecar `<doc>.margin.json` so PDF/pandoc/Jekyll pipelines are untouched.

## Working agreement

- **Small code, tested live.** No test suites, no scripts that test scripts: the user tests in Sublime, in real use. Don't add tests unless asked; it wastes tokens.

## Architecture

- `margin_core/` — no `sublime` imports. The existing tests (`python3 -m pytest tests`) may be run, but are not extended (see below).
  - `structure.py` rule-based parsing (profiles: regex per line, levels, separators, paragraphs, implicit *opening*).
  - `notes.py` writer's stock: `%>` lines, `\begin{notes}`, text after `\end{document}`.
  - `sidecar.py` items/labels/session id/last answer only (the conversation lives in the Claude session, not the sidecar); anchors relocate after edits (`orig` text guards suggestions).
  - `protocol.py` prompt templates (`prompts/*.md`, `{{placeholders}}`) and parsing of Claude's prose + one ```json block.
  - `claude_runner.py` `claude -p --output-format json`, `--session-id` first round then `--resume`; falls back to a new session if resume fails. cwd = document folder.
- `Margin.py` — Sublime layer: commands, region annotations, checklist quick panels, output panel, hover.
- Python 3.8 (ST4 plugin host): `.python-version` = 3.8; no 3.9+ syntax.

## Open / next

- **Sidebar (decided, first version built):** HTML sheet in a second column (`margin_sidebar`) with direction, clickable structure, open checklist with ✓/✗/done/apply, and Claude's *last* answer; the whole conversation stays in the bottom panel / Terminal. Inline annotations stay too (`inline_annotations` setting turns them off). Links are `subl:margin_sidebar_action {...}`. Not yet tried in real Sublime.
- **Shared checklist:** open items go to Claude with ids; Claude answers `updates` (weight high/normal/low, status done/open, reason) that Margin applies via `sidecar.apply_updates`, never on items the writer decided. Ask covers questions and general reactions (React was merged into it); Ask does not embed the document but lets Claude `Read` the saved file when needed. Claude does not edit the sidecar file itself (avoids write races with Sublime).
- Kinds off: legend toggles the global `skip_kinds` (hidden and Claude leaves them out); Claude may switch a kind off per document (`kinds_off` with reason), the writer switches it back on (`kinds_on`, then Claude leaves it on). Sections fold; a section bar shows one section at a time (minihtml has no hover or in-page anchors).
- `margin_open_terminal` opens the document's session in Terminal (`cd <doc dir> && claude --resume <session_id>`); that is the whole conversation.
- Links: `margin_link_file` stores related files (sermon ↔ liturgies) in both sidecars as relative paths; sidebar *Related* lists them newest first (date in the file name, else file date). `link_key` (regex, e.g. Bible text `Joh02.1-12` → `("joh","2")`, leading zeros dropped) only *suggests* files near the document with the same key: the user's sermons and liturgies are not 1:1 (versions `_v2`, a sermon held again elsewhere with another liturgy), so never auto-link. Layout of the first user: `Preken/<ref> - <title>[_vN].tex`, liturgies in `Preken/Liturgie/<yyyymmdd><mo|av>_<ref>_<place>[_HA].tex`. One sidebar per window, following the active document.
- Sidebar shows rounds in this conversation and tokens read in the last round (from Claude Code's `usage`).
- Real end-to-end test with Claude on the user's Mac (not possible in the build container).
