# Design — `/create_persona` skill

Date: 2026-07-02
Status: Approved (design), pending spec review

## Purpose

Given a person, podcast, YouTube channel, topic, or YouTube link, find the most
relevant full-episode YouTube videos, clean the URLs, and add them as sources to
a named NotebookLM notebook — building up a "persona" knowledge base for that
person that can later be chatted with in NotebookLM. Also prints the clean raw
links so the user gets both.

## Approach

Turnkey bundled Python script (`create_persona.py`), mirroring the existing
`podcast_to_notion.py`. Claude does only the fuzzy "resolve who/what the user
means" step, then hands concrete inputs to the script, which performs the
deterministic work: discover → filter → rank → clean → push to NotebookLM.

Rejected alternatives:
- Fully Claude-driven (web search + nlm): like-count ranking unreliable, burns
  tokens every run.
- Pure script, no Claude resolution: breaks on ambiguous names (e.g. "Graham").

## Components

1. `SKILL.md` — triggers on `/create_persona`. Resolves the target, runs the
   script with concrete args, reports the result.
2. `create_persona.py` — the engine.

## `create_persona.py` behaviour

### Discovery (yt-dlp)
- Channel/playlist link → enumerate videos within that source first.
- Person/guest/topic/podcast name → `ytsearchN:` query.
- Specific video link → include it (and optionally its channel).
- Pull per video: `id`, `title`, `like_count`, `view_count`, `duration`,
  `uploader`/channel, `availability`, `webpage_url`.

### Filtering
Drop:
- Shorts (duration < ~60s, or `/shorts/` URLs).
- Clips / trailers / highlights (title heuristics + short duration).
- Unavailable, private, deleted, age-restricted (where detectable).
- Duplicates by video id.

### Ranking (per user spec, in order)
1. Highest visible like count.
2. If likes unavailable → view count.
3. If both unavailable → relevance to query / result order.
4. Prefer official channel uploads over reuploads.
5. Prefer full-length episodes over short clips.
6. Prefer recent active official channels when duplicates exist.

### URL cleaning
Reduce every link to: `https://www.youtube.com/watch?v=VIDEO_ID`

Strip: `utm_*`, `si`, `feature`, `pp`, playlist params (unless `--playlist`
requested), timestamps (unless requested).

### NotebookLM push (reuse `nlm` CLI)
- Reuse-or-create the notebook by name: if a notebook with the given name
  exists, add to it; otherwise create it.
- For each cleaned link, add it as a source, skipping links already present as
  sources (dedup against existing sources).
- Never delete the user's notebook (unlike podcast_to_notion, which cleans up
  notebooks it created — here the notebook is the deliverable).

### Output
- Default: raw links only, one per line, separated by blank lines.
- `--titles`: `N. Title — <url>`.
- `--ranking`: `N. Title — <likes> likes — <url>`.
- Plus a short summary line: "added X, skipped Y (already present)".

## Runtime call

```
python create_persona.py "<target>" --notebook "<name>" --count 10
```

Flags:
- `--count N` — number of links (default 10).
- `--notebook "<name>"` — target NotebookLM notebook (reuse-or-create).
- `--titles` / `--ranking` — output modes.
- `--no-push` / `--dry-run` — find + print only, do not touch NotebookLM.
- `--channel` / `--playlist` — hints for source-scoped discovery.

## Data flow

```
user request
  → Claude resolves the target (channel / person / podcast / topic / link)
  → python create_persona.py "<target>" --notebook "<name>" --count 10
      → yt-dlp discover
      → filter / dedup / rank
      → clean URLs
      → reuse-or-create notebook
      → add sources (skip dupes)
      → print raw links + "added X, skipped Y"
  → Claude reports back
```

## Error handling
- `yt-dlp` missing → print one-line install instruction
  (`uv tool install yt-dlp`).
- `nlm` missing or login expired → point to `podcast-to-notion` one-time setup
  (auth is shared: if podcast-to-notion works, this does too).
- No results after filtering → say so, don't push an empty notebook.
- Warn if a push would exceed NotebookLM's free-tier cap (~50 sources per
  notebook).

## Defaults (confirmed with user)
- Discovery/ranking engine: `yt-dlp` (true "most liked first").
- Notebook handling: reuse-or-create by name.
- Skill both prints raw links AND pushes to NotebookLM.
- `--dry-run` previews ranked links without adding them.

## Revisions (2026-07-02, from first live runs)
- **Discovery reworked for speed.** Full-extracting the whole pool timed out
  (>10 min for 30 videos). Now every multi-video target is flat-enumerated
  (cheap), pre-ranked by view count, and only ~15 candidates are fully extracted
  to read like counts (~35–45s).
- **Channel limitation found.** YouTube channel listings expose no view counts,
  so channel-URL targets can only rank "most-liked among *recent* uploads". For
  a person's all-time greatest hits from their own channel, search their name
  with the new `--official-only` flag (keeps only results whose channel name
  matches the query). Searches do carry view counts.
- **Dedup fixed.** `nlm source list --json` returns `url: null` for YouTube
  sources, so id-based dedup never matched. Dedup is now by normalized title
  (plus video id when a url is present).

## Testing
- `--dry-run` against a known channel/person → verify ranked, cleaned links.
- Unit-testable URL cleaning (various messy URL forms → canonical watch URL).
- Reuse-or-create: run twice for the same notebook name → second run skips
  already-present links, adds none new.
