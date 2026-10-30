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

## Architecture

- `margin_core/` — no `sublime` imports, tested with `python3 -m pytest tests`.
  - `structure.py` rule-based parsing (profiles: regex per line, levels, separators, paragraphs, implicit *opening*).
  - `notes.py` writer's stock: `%>` lines, `\begin{notes}`, text after `\end{document}`.
  - `sidecar.py` items/labels/transcript/session id; anchors relocate after edits (`orig` text guards suggestions).
  - `protocol.py` prompt templates (`prompts/*.md`, `{{placeholders}}`) and parsing of Claude's prose + one ```json block.
  - `claude_runner.py` `claude -p --output-format json`, `--session-id` first round then `--resume`; falls back to a new session if resume fails. cwd = document folder.
- `Margin.py` — Sublime layer: commands, region annotations, checklist quick panels, output panel, hover.
- Python 3.8 (ST4 plugin host): `.python-version` = 3.8; no 3.9+ syntax.

## Open / next

- **UX decision pending:** the user pictured a *side panel* (HTML sheet in a second column) holding direction, clickable structure tree, checklist with ✓/✗, Claude's answer — not inline annotations. Open question to the user: panel as overview only (conversation in Terminal via `claude --resume <id>`), or conversation readable in the panel too.
- Command to open the document's session in Terminal (`cd <doc dir> && claude --resume <session_id>`).
- Real end-to-end test with Claude on the user's Mac (not possible in the build container).
