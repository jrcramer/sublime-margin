# Margin

A reading-along partner for long-form writing in **Sublime Text 4**: it shows the structure of your draft in the margin, and lets **Claude Code** read along halfway through the writing: mirroring what is there, taking stock of material you have not used, sketching ways forward. Your decisions are remembered.

Margin is built for writers who want a sparring partner, not a co-author:

- **Mirror, not plan.** Claude describes the structure and direction the text *has*, and offers ways forward as options with their gains and losses, never one recommended route.
- **Deviation is not error.** If your text took another route than planned, Margin shows what you left behind and where it could still land.
- **Your decisions stick.** Accept or reject every point; a rejection carries your reason, and Claude does not raise it again unless the text changes so that the reason no longer applies.
- **Provenance is visible.** Your own notes and Claude's additions are kept apart.
- **Spelling comes last**, as a separate round with suggestions you can apply.

Your document is never touched except when you apply a language suggestion. Everything Margin remembers lives in a sidecar file next to it (`draft.margin.json`), so PDF builds, pandoc pipelines or static-site generators keep working as before.

> Status: early. The core (structure parsing, anchoring, prompts, protocol) is unit-tested; the Sublime UI layer needs real-world use. Issues welcome.

## Requirements

- Sublime Text 4 (build 4050 or later, for margin annotations)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in (`claude` works in a terminal)

## Install

Clone into your Packages folder (Sublime Text → *Preferences → Browse Packages…*), named exactly `Margin`:

```sh
cd "<Packages folder>"
git clone https://github.com/jrcramer/sublime-margin.git Margin
```

If Sublime cannot find `claude` (Sublime often does not see your shell's `PATH`), set the full path in the settings:

```json
{ "claude_command": ["/opt/homebrew/bin/claude"] }
```

## Use

All commands are in the Command Palette under **Margin:**.

| Command | What it does |
|---|---|
| **Mirror** | Claude reads the draft: labels for the units, the direction it sees, where the structure does not run, factual problems (with references), and two or three options for continuing. |
| **Stock** | Inventory of material not (yet) in the draft: your marked notes and loose fragments, plus what Claude would add from the sources, each with where it could land. |
| **Ask Claude…** | A question during writing, about the selection if there is one. Also in the right-click menu. |
| **Show/hide sidebar** | A sheet next to the document with the direction, the structure (click to jump), the open checklist with ✓ / ✗, and Claude's last answer. While it is open, answers go there instead of the bottom panel. |
| **React to the last round…** | A general reaction ("the exegetical detail matters less to me here"). Claude interprets it and re-weighs the open items: lower or higher weight, or closed with a reason. What you accepted or rejected is never changed. Items Claude closed appear under *Changed by Claude* in the sidebar until you mark them fine or reopen them. |
| **Checklist** | All open items in line order. Pick one to jump there, then accept, reject (with reason), mark done, reopen, or apply a suggestion. |
| **Outline / Go to unit** | Jump through sections, moves and labelled paragraphs. |
| **Label this unit…** | Your own name for the unit at the cursor. Your labels always win over Claude's. |
| **Add note at cursor…** | A reminder of your own in the margin and checklist. |
| **Language round** | Spelling and typos, once, at the end. Each finding is an applicable suggestion; **Apply all language suggestions** does them in one go (undo works). A suggestion is refused if its line changed in the meantime. |
| **Show last answer** | Claude's prose of the last round in a panel. |
| **Open conversation in Terminal** | The whole conversation lives in the document's Claude Code session; this opens it with `claude --resume` (on macOS in Terminal, elsewhere the command is copied). |
| **New Claude conversation** | Starts a fresh Claude session; checklist, labels and decisions stay. |

In every round, the open items go to Claude with their ids, so the list works both ways: Claude can re-weigh or close its own points when the text changes, and you decide with ✓ (agree: the point stands, you will do or did it), ✗ (not this, with a reason Claude respects) or *done* (handled in the text).

In the margin, every item shows a letter for its kind (**S**tructure, **D**irection, **F**act, stock **V**, **O**ption, **?** question, **L**anguage, **N**ote) followed by its title, plus ✓ and ✗ to accept or reject. Click the title, or hover the gutter dot, for the explanation and references.

Each document has its own Claude Code session, so a conversation about a text can continue over days. The sidecar keeps the session id, and if the session cannot be resumed (another machine, say), a new one starts. Your decisions are carried over because they travel with every prompt.

## Structure profiles

What counts as structure is set per document type with line-based regular expressions. The defaults cover LaTeX (`\part`/`\chapter`, `\section`, `\subsection`, and `---` lines as separators), Markdown (headings and `---`) and plain text. Blank lines make paragraphs. Text before the first marked unit becomes an implicit *opening*.

```jsonc
{
  "name": "LaTeX",
  "selector": "text.tex",                 // syntax scope; "" matches anything
  "comment_prefix": "%",                  // comment lines are not structure
  "body_start": "\\\\begin\\{document\\}",   // parse only between these
  "body_end": "\\\\end\\{document\\}",
  "ignore": ["^\\s*(\\\\[a-zA-Z]+\\*?\\s*)+$"],  // e.g. a bare \large
  "structure": [
    {"kind": "section", "level": 2, "pattern": "^\\s*\\\\section\\*?\\{(.+?)\\}"},
    {"kind": "section", "level": 2, "pattern": "^\\s*---\\s*(\\\\\\\\)?\\s*$", "separator": true},
    {"kind": "move",    "level": 3, "pattern": "^\\s*\\\\textbf\\{([^}]+)\\}\\s*$"}
  ],
  "notes": {
    "line_prefixes": ["%>"],              // %> a note to self
    "environments": ["notes"],            // \begin{notes} ... \end{notes}
    "after_body": true                    // anything after \end{document}
  },
  "context": "Free text for Claude: genre, conventions, which sources to quote from."
}
```

- `level`: lower is coarser. A capture group in `pattern` becomes the unit's title; `"separator": true` marks dividers without a title.
- `notes`: how *you* mark your stock. Loose fragments in the running text are recognised by Claude in the Stock round.
- `context`: tells Claude what kind of text this is. See `examples/sermon-latex.sublime-settings` for a complete profile for sermon manuscripts (one spoken line per line, `---\\` sections, bold move titles, quotations checked against a named Bible translation).

User profiles replace the default list, so copy any defaults you still want.

## Prompts

The prompts are plain Markdown in `prompts/` (`system`, `mirror`, `stock`, `ask`, `language`). To change them, copy the ones you want into a folder of your own and set `"prompt_dir"`. Placeholders such as `{{document}}`, `{{outline}}`, `{{notes}}`, `{{decisions}}` and `{{context}}` are filled in by Margin.

Claude answers with prose followed by one JSON block (`direction`, `labels`, `items`); the shape is documented in `prompts/system.md`.

## Settings

See *Preferences → Package Settings → Margin → Settings*. Most relevant:

- `claude_command`, `extra_path`, `model`, `claude_extra_args`, `timeout`
- `claude_tools`: tools Claude may use while reading (default: web fetch and search, for checking quotations; `[]` for none)
- `response_language`: e.g. `"Dutch"`; default is the language of the document
- `skip_kinds`: kinds you never want (the sidebar legend switches them)
- `inline_annotations`: `false` keeps the text clean and uses only the sidebar
- `show_labels`, `show_resolved`, `colors`, `auto_show`, `sidecar_suffix`

## Privacy

Each round sends the full document, your notes and your decisions to Claude through your own Claude Code installation, under your own account and its terms. Margin itself sends nothing anywhere.

## Development

The logic lives in `margin_core/`, which does not import `sublime` and is tested with pytest:

```sh
python3 -m pytest tests
```

`Margin.py` is the Sublime layer: commands, margin rendering, events.

## License

MIT
