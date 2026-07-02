# claude_skills

Shareable [Claude Code](https://claude.com/claude-code) skills.

All skills here were cleaned by `share_skill`: secrets and personal details are
removed and replaced with placeholders. Each skill's `SKILL.md` contains a
"Required setup" section telling your assistant what to ask you for on first use.

## Install a skill

```bash
npx skills add tommyck003/claude_skills@<skill-name>
```

Or copy the skill's folder into your own `~/.claude/skills/`.

## Skills

- **create_persona** — build a NotebookLM "persona" notebook from a person's
  best YouTube videos.
  `npx skills add tommyck003/claude_skills@create_persona`
- **podcast-to-notion** — summarize a YouTube/podcast episode and log it to a
  Notion database.
  `npx skills add tommyck003/claude_skills@podcast-to-notion`
