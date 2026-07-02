#!/usr/bin/env python3
"""
podcast_to_notion.py — summarize YouTube/podcast episodes via NotebookLM (nlm CLI)
and append a row to a Notion database. Self-contained: only Python stdlib + the
`nlm` CLI (notebooklm-mcp-cli) are required.

Usage:
    python podcast_to_notion.py <url> [<url> ...]
    python podcast_to_notion.py --dry-run <url>      # summarize + print, don't write Notion
    python podcast_to_notion.py --notebook <id> <url>  # reuse an existing nlm notebook
    python podcast_to_notion.py --notes "my notes..." <url>  # add your notes as a source too
    python podcast_to_notion.py --keep-notebook <url>  # keep the NotebookLM notebook (default: delete it)

Environment:
    NOTION_TOKEN   (required unless --dry-run) Notion internal integration token (ntn_/secret_...)
    NOTION_DB_ID   (optional) Notion database id. Defaults to the "Podcast Notes" DB.
    NLM_BIN        (optional) path to the nlm executable. Auto-detected otherwise.

Prereqs (one-time):
    uv tool install notebooklm-mcp-cli   # installs `nlm`
    nlm login                            # Google login for NotebookLM
    Create a Notion integration at notion.so/my-integrations, copy its token,
    and share the Podcast Notes database with that integration.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import date

# Windows stdout/stderr default to cp1252, which crashes when printing episode
# titles that contain CJK/accented characters or emoji (e.g. 《 U+300A). Force
# UTF-8 so no print can ever take the script down.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_DB_ID = "<YOUR_NOTION_DATABASE_ID>"
NOTION_VERSION = "2022-06-28"

SUMMARY_PROMPT = (
    "Summarize this episode based strictly on the transcript. Output these sections "
    "with each header on its own line: TLDR: (one sentence). SHOW: (podcast name and hosts). "
    "LENGTH: (approx, or note if not stated). KEYPOINTS: (6-8 bullets starting with '* '). "
    "TAKEAWAYS: (3-4 bullets starting with '* '). ACTIONS: (3-4 concrete next actions the "
    "listener could take to apply these ideas and improve themselves, each starting with an "
    "action verb and starting with '* '). TOPICS: (3-5 comma-separated tags)."
)

# Appended to the prompt when the listener attached their own notes (added to the
# notebook as a separate source titled "Listener notes").
NOTES_CLAUSE = (
    " The notebook also contains a source titled 'Listener notes' with the listener's own "
    "notes on this episode. Add one more section MYNOTES: (2-4 bullets starting with '* ') "
    "that surfaces the most important points and any personal action items from those notes, "
    "and weave anything important from the notes into the takeaways where relevant."
)


def find_nlm():
    cand = os.environ.get("NLM_BIN")
    if cand and os.path.exists(cand):
        return cand
    p = shutil.which("nlm")
    if p:
        return p
    for guess in (
        os.path.expanduser("~/.local/bin/nlm"),
        os.path.expanduser("~/.local/bin/nlm.exe"),
    ):
        if os.path.exists(guess):
            return guess
    sys.exit("ERROR: `nlm` not found. Install with: uv tool install notebooklm-mcp-cli")


# On Windows, launching the `nlm` console app flashes a console window. These
# flags tell the OS to run the child process with no window at all. They are
# silently ignored on non-Windows platforms (the kwargs are only set there).
_NO_WINDOW_KWARGS = {}
if os.name == "nt":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # honor wShowWindow
    _si.wShowWindow = subprocess.SW_HIDE            # hide the window
    _NO_WINDOW_KWARGS = {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": _si,
    }


def run(cmd, timeout=600):
    # Force UTF-8 decoding: `nlm` emits emoji/non-ASCII (✓, video titles) that
    # Windows' default cp1252 codec can't decode, which would crash the capture
    # thread and yield None output. errors="replace" makes capture crash-proof.
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace", **_NO_WINDOW_KWARGS)
    return res.returncode, res.stdout, res.stderr


def youtube_title(url):
    """Authoritative episode title via YouTube's public oEmbed endpoint.
    Returns '' for non-YouTube URLs or on any failure (callers fall back)."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})", url)
    if not m:
        return ""
    api = ("https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v="
           + m.group(1) + "&format=json")
    try:
        with urllib.request.urlopen(api, timeout=20) as r:
            return (json.load(r).get("title") or "").strip()
    except Exception:
        return ""


def strip_citations(text):
    text = re.sub(r"\s*\[[\d,\s–\-]+\]", "", text)   # [1], [1-5], [2, 7]
    text = text.replace("**", "")
    return text.strip()


def find_answer(obj):
    if isinstance(obj, dict):
        for k in ("answer", "text", "response", "content", "message"):
            v = obj.get(k)
            if isinstance(v, str) and len(v) > 50:
                return v
        for v in obj.values():
            r = find_answer(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_answer(v)
            if r:
                return r
    return None


def parse_summary(answer):
    """Split the NotebookLM answer into named sections."""
    sections = {"TLDR": "", "SHOW": "", "LENGTH": "", "KEYPOINTS": [], "TAKEAWAYS": [],
                "ACTIONS": [], "MYNOTES": [], "TOPICS": ""}
    headers = {"TLDR", "SHOW", "LENGTH", "KEYPOINTS", "TAKEAWAYS", "ACTIONS", "MYNOTES", "TOPICS"}
    current = None
    for raw in answer.splitlines():
        line = raw.rstrip()
        m = re.match(r"^\**\s*(TLDR|TL;DR|SHOW|LENGTH|KEY ?POINTS|KEYPOINTS|TAKEAWAYS|"
                     r"ACTIONS|NEXT ?ACTIONS|ACTION ?ITEMS|MYNOTES|MY ?NOTES|TOPICS)\s*:?\s*(.*)$",
                     line, re.IGNORECASE)
        if m:
            key = m.group(1).upper().replace(" ", "").replace("TL;DR", "TLDR")
            key = {"TLDR": "TLDR", "SHOW": "SHOW", "LENGTH": "LENGTH",
                   "KEYPOINTS": "KEYPOINTS", "TAKEAWAYS": "TAKEAWAYS",
                   "ACTIONS": "ACTIONS", "NEXTACTIONS": "ACTIONS", "ACTIONITEMS": "ACTIONS",
                   "MYNOTES": "MYNOTES",
                   "TOPICS": "TOPICS"}.get(key, key)
            current = key if key in headers else None
            rest = strip_citations(m.group(2))
            if current and rest:
                if current in ("KEYPOINTS", "TAKEAWAYS", "ACTIONS", "MYNOTES"):
                    pass  # inline bullets rare; handled below
                else:
                    sections[current] = rest
            continue
        if current is None:
            continue
        cleaned = strip_citations(line)
        if not cleaned:
            continue
        if current in ("KEYPOINTS", "TAKEAWAYS", "ACTIONS", "MYNOTES"):
            cleaned = re.sub(r"^[\*\-•]\s*", "", cleaned)
            if cleaned:
                sections[current].append(cleaned)
        else:
            sections[current] = (sections[current] + " " + cleaned).strip()
    return sections


def rt(content):
    return [{"type": "text", "text": {"content": content[:2000]}}]


def heading(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": rt(text)}}


def bullet(text):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rt(text)}}


def para(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": rt(text)}}


def build_children(s, notes=""):
    blocks = []
    if s["TLDR"]:
        blocks += [heading("TL;DR"), para(s["TLDR"])]
    if s["KEYPOINTS"]:
        blocks.append(heading("Key Points"))
        blocks += [bullet(b) for b in s["KEYPOINTS"]]
    if s["TAKEAWAYS"]:
        blocks.append(heading("Takeaways"))
        blocks += [bullet(b) for b in s["TAKEAWAYS"]]
    if s["ACTIONS"]:
        blocks.append(heading("Next Actions to Improve Myself"))
        blocks += [bullet(b) for b in s["ACTIONS"]]
    if s.get("MYNOTES"):
        blocks.append(heading("From My Notes — Highlights"))
        blocks += [bullet(b) for b in s["MYNOTES"]]
    if notes.strip():
        blocks.append(heading("My Notes (verbatim)"))
        # Notion blocks cap at 2000 chars; chunk long notes across paragraphs.
        for i in range(0, len(notes), 1900):
            blocks.append(para(notes[i:i + 1900]))
    if s["TOPICS"]:
        blocks += [heading("Topics"), para(s["TOPICS"])]
    blocks.append(para("Summary generated via NotebookLM from the transcript."))
    return blocks


def notion_create_page(token, db_id, props, children):
    payload = {"parent": {"database_id": db_id}, "properties": props, "children": children}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages", data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: Notion API {e.code}: {e.read().decode()}")


def make_properties(episode, show, url, topics, tldr, length):
    topic_opts = [{"name": t.strip()[:100]} for t in topics if t.strip()]
    return {
        "Episode": {"title": rt(episode)},
        "Show": {"rich_text": rt(show)},
        "Link": {"url": url},
        "Date listened": {"date": {"start": date.today().isoformat()}},
        "Topics": {"multi_select": topic_opts},
        "TL;DR": {"rich_text": rt(tldr)},
        "Length": {"rich_text": rt(length)},
    }


def delete_notebook(nlm, notebook):
    """Permanently delete the NotebookLM notebook (best-effort; never raises).

    Runs in main()'s finally block, so it must not let a delete failure mask the
    real error or leave the script in a worse state — hence the broad guard.
    """
    try:
        rc, out, err = run([nlm, "delete", "notebook", notebook, "--confirm"], timeout=120)
    except Exception as e:  # timeout, decode error, etc.
        print(f"!! could not delete notebook {notebook} — remove it manually. ({e})")
        return
    if rc == 0:
        print(f"cleaned up notebook: {notebook}")
    else:
        print(f"!! could not delete notebook {notebook} — remove it manually.\n{out}\n{err}")


def main():
    args = sys.argv[1:]
    dry_run = False
    keep_notebook = False
    notebook = None
    notes = ""
    urls = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run":
            dry_run = True
        elif a == "--keep-notebook":
            keep_notebook = True
        elif a == "--notebook":
            i += 1
            notebook = args[i]
        elif a == "--notes":
            i += 1
            notes = args[i]
        elif a.startswith("-"):
            sys.exit(f"Unknown flag: {a}")
        else:
            urls.append(a)
        i += 1
    if not urls:
        sys.exit(__doc__)

    nlm = find_nlm()
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DB_ID", DEFAULT_DB_ID)
    if not dry_run and not token:
        sys.exit("ERROR: NOTION_TOKEN not set. Use --dry-run to test without writing to Notion.")

    created = False
    try:
        if not notebook:
            rc, out, err = run([nlm, "notebook", "create", f"Podcast import {date.today().isoformat()}"])
            m = re.search(r"ID:\s*([0-9a-f\-]{36})", out)
            if not m:
                sys.exit(f"ERROR creating notebook:\n{out}\n{err}")
            notebook = m.group(1)
            created = True
            print(f"notebook: {notebook}")

        # Add all sources at once, capture titles + ids in order.
        add_cmd = [nlm, "source", "add", notebook]
        for u in urls:
            add_cmd += ["--youtube", u]
        add_cmd += ["--wait", "--wait-timeout", "600"]
        rc, out, err = run(add_cmd, timeout=900)
        titles = re.findall(r"Added source:\s*(.+?)\s*\(ready\)", out)
        sids = re.findall(r"Source ID:\s*([0-9a-f\-]{36})", out)
        if not sids:
            sys.exit(f"ERROR adding sources:\n{out}\n{err}")

        # If the listener attached notes, add them as a separate text source so
        # NotebookLM grounds on both the episode and the notes.
        notes_sid = None
        if notes.strip():
            rc, nout, nerr = run([nlm, "source", "add", notebook, "--text", notes,
                                  "--title", "Listener notes", "--wait", "--wait-timeout", "300"],
                                 timeout=360)
            nm = re.findall(r"Source ID:\s*([0-9a-f\-]{36})", nout)
            notes_sid = nm[0] if nm else None
            if notes_sid:
                print(f"notes source: {notes_sid}")

        prompt = SUMMARY_PROMPT + (NOTES_CLAUSE if notes_sid else "")
        for idx, (sid, url) in enumerate(zip(sids, urls)):
            # Prefer YouTube's authoritative title; fall back to nlm's parsed
            # title (works for non-YouTube sources), then a generic label.
            nlm_title = titles[idx] if idx < len(titles) else ""
            title = youtube_title(url) or nlm_title or f"Episode {idx+1}"
            source_ids = sid if not notes_sid else f"{sid},{notes_sid}"
            rc, out, err = run([nlm, "query", "notebook", notebook, prompt,
                                "--source-ids", source_ids, "--json", "--timeout", "180"], timeout=240)
            try:
                answer = find_answer(json.loads(out))
            except json.JSONDecodeError:
                answer = out
            if not answer:
                print(f"!! no summary for {title} ({url})")
                continue
            s = parse_summary(answer)
            topics = [t for t in re.split(r"[,;]", s["TOPICS"]) if t.strip()] if s["TOPICS"] else []
            if dry_run:
                print("\n=== " + title + " ===")
                print("TLDR:", s["TLDR"])
                print("TOPICS:", topics)
                print(f"KEYPOINTS: {len(s['KEYPOINTS'])}  TAKEAWAYS: {len(s['TAKEAWAYS'])}  "
                      f"ACTIONS: {len(s['ACTIONS'])}  MYNOTES: {len(s['MYNOTES'])}")
                for a in s["ACTIONS"]:
                    print("  -> " + a)
                continue
            props = make_properties(title, s["SHOW"] or "", url, topics or ["Unsorted"],
                                    s["TLDR"] or title, s["LENGTH"] or "Not stated")
            page = notion_create_page(token, db_id, props, build_children(s, notes))
            print(f"added: {title}\n  {page.get('url','(no url)')}")

        print("done.")
    finally:
        # Keep NotebookLM tidy: delete the notebook this run created so imports
        # don't pile up. Only ever delete a notebook we created — a notebook
        # passed via --notebook is the user's and is left untouched. The finally
        # guarantees cleanup even if a step above failed partway. Opt out with
        # --keep-notebook.
        if created and not keep_notebook:
            delete_notebook(nlm, notebook)


if __name__ == "__main__":
    main()
