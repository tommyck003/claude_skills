# podcast-to-notion

Summarize YouTube/podcast episodes via NotebookLM and log them to a Notion database.
Self-contained: Python stdlib + the `nlm` CLI only. Works under Claude Code, Codex, or any shell.

## Quick start

```bash
# one-time
uv tool install notebooklm-mcp-cli
nlm login                       # Google login for NotebookLM
export NOTION_TOKEN="ntn_..."   # Notion integration token (share the DB with it)

# every time
python podcast_to_notion.py https://www.youtube.com/watch?v=XXXX
python podcast_to_notion.py URL1 URL2 URL3        # batch
python podcast_to_notion.py --dry-run URL         # summarize, don't write to Notion
python podcast_to_notion.py --notes "key idea X" URL   # add your own notes as a source too
```

## Adding your own notes

Pass `--notes "..."` (CLI) or, from Telegram, just type the link followed by your notes
(optionally on a line starting with `Notes:`). A plain Share from the YouTube app adds no
notes. Your notes become a second NotebookLM source, so the summary reflects them, and the
Notion page stores both a "From My Notes — Highlights" section and your raw notes verbatim.

See `SKILL.md` for the full agent-facing instructions and configuration.

## Using it as a skill

- **Claude Code:** this folder lives in `~/.claude/skills/podcast-to-notion/`; invoke the
  `podcast-to-notion` skill (or just paste a link and ask to save it).
- **Codex CLI:** a command wrapper is installed at `~/.codex/prompts/podcast-to-notion.md`.
  Invoke `/podcast-to-notion <url>` in Codex. If your Codex prompts directory differs, copy
  that file there.

## Notion database

Defaults to the "Podcast Notes" database (`<YOUR_NOTION_DATABASE_ID>`).
Properties: Episode (title), Show, Link, Date listened, Topics (multi-select), TL;DR, Length.
Override the target with `NOTION_DB_ID`.
