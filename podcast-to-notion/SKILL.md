---
name: podcast-to-notion
description: Summarize a YouTube/podcast episode and log it to the Notion "Podcast Notes" database. Use when the user sends one or more podcast/YouTube links and wants them summarized and saved. Runs end-to-end via the bundled script — no manual configuration needed.
---

# Podcast → Notion

Turnkey: given one or more podcast/YouTube URLs, summarize each via NotebookLM and append a row (with a balanced summary in the page body) to the user's Notion "Podcast Notes" database.

## When to use
The user pastes a podcast or YouTube link (or several) and wants it captured for later recall. Trigger phrases: "log this podcast", "summarize and save", "add these to my podcast notes", or just a bare YouTube/Spotify link with intent to save.

## How to run it
Everything is done by the bundled script. Do NOT re-derive the steps — just run it:

```bash
python "<skill_dir>/podcast_to_notion.py" <url1> [<url2> ...]
```

`<skill_dir>` is the folder containing this SKILL.md. The script:
1. Creates a NotebookLM notebook (via the `nlm` CLI).
2. Ingests each URL as a source and waits for processing.
3. Queries NotebookLM for a structured balanced summary per episode.
4. Writes one Notion page per episode (Episode, Show, Link, Date listened, Topics, TL;DR, Length + a body with TL;DR / Key Points / Takeaways / **Next Actions to Improve Myself** / Topics).
5. Deletes the NotebookLM notebook it created, so NotebookLM stays tidy (the summary already lives in Notion).

Report the returned Notion page URLs to the user.

To preview without writing to Notion: add `--dry-run`.
To keep the NotebookLM notebook instead of deleting it (e.g. to inspect or reuse it): add `--keep-notebook`.
To reuse an existing notebook: add `--notebook <id>`. A notebook passed this way is the user's own and is **never** deleted — only notebooks this run created are cleaned up.
To include the listener's own notes: add `--notes "..."`. The notes are added to the
NotebookLM notebook as a separate "Listener notes" source so the summary grounds on both
the episode and the notes; the page gets a "From My Notes — Highlights" section plus the
raw notes saved verbatim.

## One-time setup (only if the script errors about missing prereqs)
1. Install the CLI: `uv tool install notebooklm-mcp-cli`
2. Authenticate NotebookLM: `nlm login` (opens a browser; user logs into Google). Verify with `nlm login --check`. Cookies last ~2-4 weeks; re-run `nlm login` when the check fails.
3. Notion access: create an internal integration at https://www.notion.so/my-integrations, copy its token, and share the "Podcast Notes" database with that integration.
4. Export the token so the script can read it:
   - bash/zsh: `export NOTION_TOKEN="ntn_..."`
   - PowerShell: `$env:NOTION_TOKEN="ntn_..."`
   Put it in your shell profile to persist.

## Configuration (env vars)
- `NOTION_TOKEN` — required (Notion integration token).
- `NOTION_DB_ID` — optional; defaults to the Podcast Notes DB (`<YOUR_NOTION_DATABASE_ID>`).
- `NLM_BIN` — optional; path to `nlm` if not on PATH (auto-detects `~/.local/bin/nlm`).

## Notes
- Works for sources NotebookLM can read (YouTube/Spotify/Apple with transcripts). Raw audio-only files with no transcript won't summarize.
- Free NotebookLM tier is ~50 queries/day.
- By default the notebook created for an import is deleted once Notion is written (cleanup runs even if a step fails partway, so clutter never accumulates). Use `--keep-notebook` to retain it.
- Topic tags are created automatically in Notion (the raw API auto-adds new multi-select options).
- Portable: depends only on Python stdlib + the `nlm` CLI, so it runs identically under Claude Code, Codex, or a plain shell/cron.

<!-- SHARE_SKILL_SETUP_START -->
## Required setup - ask the user for these before running

This skill was shared with personal details removed. Before first use,
ask the user to provide the following and store them (e.g. in a local
.env), then replace the placeholders:

- **Home directory path** -> replaces `<HOME_PATH>` (env: `HOME_PATH`)
- **Notion database or page ID** -> replaces `<YOUR_NOTION_DATABASE_ID>` (env: `NOTION_DATABASE_ID`)
- **Windows security identifier (SID)** -> replaces `<WINDOWS_SID>` (env: `WINDOWS_SID`)
- **Windows/AD domain** -> replaces `<WINDOWS_DOMAIN>` (env: `WINDOWS_DOMAIN`)
- **Operating-system username** -> replaces `<USERNAME>` (env: `USER_NAME`)

Do not proceed until these are supplied.
<!-- SHARE_SKILL_SETUP_END -->
