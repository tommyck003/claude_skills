---
name: create_persona
description: Build a NotebookLM "persona" notebook from a person's best YouTube videos. Given a person, podcast, YouTube channel, topic, or YouTube link, find the most-liked full episodes, clean the links, and add them as sources to a named NotebookLM notebook (reuse-or-create) so it can be chatted with as that person. Use when the user invokes /create_persona, or asks to gather someone's talks/podcasts into NotebookLM, build a persona/knowledge base for a person, or "add the best videos of X to a notebook". Also prints the clean raw YouTube links.
---

# /create_persona — build a NotebookLM persona from someone's best videos

Turnkey: given a person / podcast / channel / topic / YouTube link, the bundled
script finds the top full-episode videos (ranked by like count), cleans the
URLs, and adds them as sources to a NotebookLM notebook whose name the user
gives. The notebook then works as that person's "persona" to chat with. The
clean raw links are printed too.

## When to use
The user invokes `/create_persona`, or asks to "build a persona for X", "add the
best videos of X to a NotebookLM notebook", "gather X's podcasts into a
notebook", or similar. If they don't say a notebook name, ask for one (or offer
`--dry-run` to just preview links).

## How to run it
1. **Resolve the target.** Turn the user's request into one concrete target:
   - a person / guest / podcast / topic → pass the name as free text (the script
     searches YouTube),
   - a channel, playlist, or video link → pass the URL.
   If a name is ambiguous (e.g. "Graham" = Paul vs. Benjamin), pick the most
   likely and state the assumption, or ask only if truly unclear.
2. **Get the notebook name.** The user supplies it (e.g. "Naval Ravikant
   Persona"). Reuse-or-create by name — running again later adds more of the
   same person's videos to the same notebook, skipping duplicates.
3. **Run the script:**

```bash
python "<skill_dir>/create_persona.py" "<target>" --notebook "<name>" --count 10
```

`<skill_dir>` is the folder containing this SKILL.md. Report the printed links
and the "added X, skipped Y" summary to the user.

## Flags
- `--notebook`, `-n NAME` — target notebook (reuse-or-create by name).
- `--count`, `-c N` — number of links (default 10).
- `--titles` — output as `N. Title — url`.
- `--ranking` — output as `N. Title — <likes> likes — url`.
- `--dry-run` / `--no-push` — discover + print only, don't touch NotebookLM.
- `--official-only` — for a person/topic **search**, keep only videos from their
  own channel (filters out guest appearances on other shows).
- `--channel` / `--playlist` — force how a URL target is treated.
- `--pool N` — for a person/topic search, how many results to scan before
  ranking (advanced; default 40). Channels/playlists always scan all uploads.

## Examples
- `/create_persona Naval Ravikant` → ask for a notebook name, then add his top
  10 talks.
- "Add the 20 most liked episodes of Lenny's Podcast to a notebook called
  'Lenny Persona'." → `... "Lenny's Podcast" -n "Lenny Persona" -c 20`
- "Just show me raw links for Charlie Munger, don't save them." →
  `... "Charlie Munger" --dry-run`

## Defaults
- Platform: YouTube. Count: 10. Ranking: most-liked first (then views, then
  relevance). Full episodes preferred over Shorts/clips/trailers/highlights.
  Duplicates and unavailable videos removed. Clean `watch?v=ID` URLs only.

## One-time setup (only if the script errors about missing prereqs)
1. `uv tool install yt-dlp` — installs `yt-dlp` (video discovery + like counts).
2. `uv tool install notebooklm-mcp-cli` — installs `nlm`.
3. `nlm login` — Google login for NotebookLM (shared with `/podcast-to-notion`
   and `/cpd_log`; if those work, this does too). Re-run when it expires.

## Notes
- A search scans up to `--pool` results (default 40) by view count, then fully
  reads like counts for only the top ~15 to stay fast.
- **Channel/playlist targets can't be ranked all-time** — YouTube doesn't expose
  view counts for channel listings, so they return the most-liked among *recent*
  uploads. For a person's all-time greatest hits from their own channel, search
  their **name** with `--official-only` rather than passing the channel URL.
- yt-dlp reads only publicly visible like/view counts; when a count is hidden it
  falls back to views, then relevance.
- NotebookLM free tier allows ~50 sources per notebook; the script warns before
  exceeding that.
- Depends only on Python stdlib + the `yt-dlp` and `nlm` CLIs, so it runs
  identically under Claude Code, Codex, or a plain shell.
