# Design Spec: `/share_skill` skill + `claude_skills` hosting repo

- **Date:** 2026-07-02
- **Author:** Tommy (Chun Kee Tham), with Claude Code
- **Status:** Awaiting user review

## 1. Purpose

Let Tommy safely share Claude Code skills (e.g. `create_persona`,
`podcast-to-notion`) with friends. A new `/share_skill` skill takes a local
skill, removes every secret and personal detail on a disposable copy, leaves a
setup note so the recipient's Claude knows what to ask for, then stages the
cleaned skill into a public GitHub repo (`tommyck003/claude_skills`) and pushes
it.

The core problem this solves: the source skill folders contain live
credentials (`podcast-to-notion/.env`, `bot.log`, `.tg_offset`) that must never
be shared. The tooling guarantees they cannot leak.

## 2. Deliverables

### A. `share_skill` skill — `~/.claude/skills/share_skill/`

| File | Responsibility |
| --- | --- |
| `SKILL.md` | Trigger on `/share_skill <name>`; orchestrate the workflow; document required setup for recipients. |
| `share_skill.py` | Deterministic security engine: clean, scan, redact, build setup manifest, inject setup note, stage into repo, print report. |
| `README.md` | Human-facing usage and safety explanation. |
| `test_share_skill.py` | Unit tests for the scanner using fixture files with fake secrets. |

### B. `claude_skills` repo

- Public GitHub repo `tommyck003/claude_skills`, cloned locally at
  `C:\Users\tt1r25\claude_skills`.
- Layout: one directory per hosted skill, plus a top-level `README.md` that
  lists the skills and the install command
  `npx skills add tommyck003/claude_skills@<name>`.

## 3. Workflow (`/share_skill <name>`)

```
locate ~/.claude/skills/<name>
      -> copy to temp staging dir            (source is NEVER modified)
      -> share_skill.py: clean + scan + redact + build manifest
      -> inject setup note into shared SKILL.md + regenerate env.example
      -> print report of every deletion / redaction
      -> FINAL SECURITY-REVIEW GATE: re-scan final staged bytes; HALT on any hit
      -> move cleaned skill into claude_skills/<name>/
      -> git add / commit / push  (repo auto-created public via gh if missing)
      -> print install command for friends
```

**Safety invariant:** all cleaning happens on a copy in the scratchpad. The
original skill folder (with its real `.env`) is never mutated, so aggressive
auto-stripping cannot destroy Tommy's credentials.

## 4. Security engine (`share_skill.py`)

Operates only on the staged copy.

### 4.1 Junk files — deleted outright

`.env`, `.env.*` (but keep `env.example.*`), `*.log`, `__pycache__/`, `*.pyc`,
`.tg_offset`, `.DS_Store`, `*.key`, `*.pem`, `id_rsa*`, and other session/token
files.

### 4.2 Secret content patterns — scanned in every remaining text file

- Notion tokens: `secret_[A-Za-z0-9]+`, `ntn_[A-Za-z0-9]+`
- Telegram bot tokens: `\d{6,}:[A-Za-z0-9_-]{35}`
- OpenAI/Anthropic keys: `sk-[A-Za-z0-9-]+`
- GitHub tokens: `ghp_[A-Za-z0-9]+`, `gho_[A-Za-z0-9]+`
- Google API keys: `AIza[0-9A-Za-z_-]{35}`
- AWS access keys: `AKIA[0-9A-Z]{16}`
- Bearer tokens: `Bearer\s+[A-Za-z0-9._-]+`
- Private keys: `-----BEGIN [A-Z ]*PRIVATE KEY-----`

### 4.3 Personal-data patterns

- Email addresses
- Home paths: `C:\Users\tt1r25`, `/Users/<name>`, `/home/<name>`
- 32-char hex / UUID Notion database & page IDs

### 4.4 Auto-strip behaviour (chosen: strip everything flagged)

- A secret inside a **source/markdown** file is **redacted to a placeholder**
  (e.g. `secret_abc123` -> `<YOUR_NOTION_TOKEN>`), never deleting the file, so
  the shared skill still runs. Each redaction is logged.
- A file that is **itself** a credential/junk file is **deleted**, and its keys
  captured into `env.example` + the setup note.
- Personal data is redacted to placeholders (`<YOUR_EMAIL>`, `<HOME_PATH>`,
  `<YOUR_NOTION_DATABASE_ID>`).
- Every deletion and redaction is printed in a report before the push.

### 4.5 Final security-review gate (runs last, before push)

After cleaning, redaction, and setup-note injection, the engine performs a
second, independent **verification pass** over the *final staged content* — the
exact bytes that would be pushed. This is a belt-and-suspenders check that the
cleaning actually worked:

- Re-run every secret and personal-data pattern (4.2, 4.3) over all files in
  the staged directory.
- If **any** match remains, **HALT** — do not stage into the repo, do not
  commit, do not push. Print the offending file, line, and matched pattern.
- Also fail if a placeholder was left in a credential *file* that should have
  been deleted, or if a `.env`-style file survived.
- Only when the verification pass is completely clean does the workflow proceed
  to `git add / commit / push`.

This gate means a bug in the redaction logic can never result in a secret being
pushed to the public repo: the worst case is a halt, not a leak.

### 4.6 Setup manifest + recipient note

Each redaction/removal is recorded in a setup manifest (field name, placeholder,
value kind). The engine then writes this into the shared copy:

1. An **AI-facing section injected into the shared `SKILL.md`**, instructing the
   recipient's Claude to collect the details before first use:

   ```markdown
   ## Required setup - ask the user for these before running

   This skill was shared with personal credentials removed. Before first use,
   ask the user to provide the following and store them (e.g. in a local .env):

   - **Notion integration token** -> replaces `<YOUR_NOTION_TOKEN>`
   - **Notion database ID** -> replaces `<YOUR_NOTION_DATABASE_ID>`
   - **Telegram bot token** -> replaces `<YOUR_TELEGRAM_BOT_TOKEN>`

   Do not proceed until these are supplied.
   ```

   Because Claude reads `SKILL.md` on trigger, the recipient's Claude will know
   exactly what to ask for.

2. A regenerated **`env.example`** listing the same keys, so setup also works
   outside Claude.

The section is generated only from fields the manifest actually captured, and
is idempotent (re-running replaces the existing block rather than duplicating).

## 5. Edge cases

- `gh` not authenticated -> stop and instruct `gh auth login`.
- Skill already exists in the repo -> overwrite that directory + "update" commit.
- Skill name not found -> list available skills under `~/.claude/skills`.
- Nothing flagged -> report "no secrets found" and proceed.

## 6. Testing

`test_share_skill.py` builds a fixture skill folder containing fake secrets, a
fake `.env`, a `__pycache__`, a hardcoded home path, an email, and a fake Notion
DB ID. Tests assert:

- Junk files/dirs are removed.
- `env.example.*` is preserved.
- Each secret pattern is redacted to the correct placeholder.
- The setup manifest lists every stripped field.
- The setup note block is injected into `SKILL.md` and is idempotent on re-run.
- The source fixture is never modified (copy-only guarantee).
- The final security-review gate HALTS when a planted secret survives cleaning
  (simulated by disabling redaction), and passes cleanly on a properly cleaned
  folder — proving the gate blocks pushes rather than leaking.

No real credentials are used anywhere in the tests.

## 7. Code standards

Per Tommy's global instructions: PEP8, module + function docstrings,
descriptive names (e.g. `redact_secrets_in_text`, `build_setup_manifest`),
small focused functions, `python3`.

## 8. Open items / assumptions

- Repo name taken as `claude_skills` (underscore) per latest instruction; can be
  renamed to `claude-skills` if preferred.
- Repo created public via `gh repo create`.
- Local clone path assumed `C:\Users\tt1r25\claude_skills`.
