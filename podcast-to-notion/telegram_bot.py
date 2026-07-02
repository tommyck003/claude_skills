#!/usr/bin/env python3
"""
telegram_bot.py — phone trigger for the podcast-to-notion pipeline.

Listens (long-poll) for Telegram messages containing a YouTube/podcast link,
runs podcast_to_notion.py on your PC, and replies with the Notion page link(s).

No third-party packages: Python stdlib + the `nlm` CLI + the sibling
podcast_to_notion.py only.

Config — read from a `.env` file next to this script, or from the environment:
    TELEGRAM_BOT_TOKEN        (required) token from @BotFather
    TELEGRAM_ALLOWED_CHAT_IDS (recommended) comma-separated chat IDs allowed to use the bot.
                              If unset, the bot replies to any chat with its chat ID so you
                              can lock it down, but will NOT process links until set.
    NOTION_TOKEN              (required) passed through to podcast_to_notion.py
    NOTION_DB_ID              (optional) override target database
    NLM_BIN                   (optional) path to nlm

Run:  python telegram_bot.py     (or pythonw on Windows to run hidden)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "podcast_to_notion.py")
OFFSET_FILE = os.path.join(HERE, ".tg_offset")
LOG_FILE = os.path.join(HERE, "bot.log")
URL_RE = re.compile(r"https?://[^\s]+")


def log(msg):
    """Append a timestamped line to bot.log (pythonw has no console)."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def load_env():
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()}
    except Exception as e:  # noqa: BLE001 (network hiccup; loop will retry)
        return {"ok": False, "error": str(e)}


def send(token, chat_id, text):
    api(token, "sendMessage", chat_id=chat_id, text=text,
        disable_web_page_preview=True)


def read_offset():
    try:
        return int(open(OFFSET_FILE).read().strip())
    except Exception:
        return 0


def write_offset(v):
    try:
        open(OFFSET_FILE, "w").write(str(v))
    except Exception:
        pass


def extract_notes(text, urls):
    """Pull the listener's own notes out of a message, ignoring the link(s)
    and YouTube share boilerplate. Returns '' when there are no real notes."""
    rest = text
    for u in urls:
        rest = rest.replace(u, " ")
    rest = rest.strip()
    # Explicit marker wins: everything after "notes:" / "note:".
    mk = re.search(r"\bnotes?\s*:\s*", rest, re.IGNORECASE)
    if mk:
        return rest[mk.end():].strip()
    # A plain Share from the YouTube app -> no notes.
    if re.search(r"\bon YouTube\b", rest, re.IGNORECASE) or rest.lower().startswith("watch "):
        return ""
    rest = rest.strip(' "“”')
    return rest if len(rest) >= 10 else ""


def process_links(urls, env, notes=""):
    """Run the pipeline once for all urls; return a reply string."""
    cmd = [sys.executable, PIPELINE] + urls
    if notes.strip():
        cmd += ["--notes", notes]
    log(f"RUN: {urls} notes={bool(notes.strip())}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=1800, env=env,
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        log("TIMEOUT after 1800s")
        return "Timed out processing that link (over 30 min)."
    except Exception as e:  # noqa: BLE001 — launching the pipeline itself failed
        log(f"LAUNCH ERROR: {e}")
        return f"Could not start the pipeline: {e}"
    out = res.stdout or ""
    pages = re.findall(r"https://www\.notion\.so/\S+", out)
    if pages:
        log(f"OK rc={res.returncode} pages={pages}")
        return "Added to Notion:\n" + "\n".join(pages)
    # Failure: log the FULL output so we can diagnose; reply with the tail.
    log(f"FAIL rc={res.returncode}\n--STDOUT--\n{out}\n--STDERR--\n{res.stderr}")
    err = (res.stderr or "").strip() or out.strip() or "Unknown error."
    return "Could not add it. " + err[-500:]


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("ERROR: TELEGRAM_BOT_TOKEN not set (put it in .env or the environment).")
    if not os.environ.get("NOTION_TOKEN"):
        sys.exit("ERROR: NOTION_TOKEN not set.")
    allowed = {c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if c.strip()}
    child_env = os.environ.copy()

    log("podcast-to-notion Telegram bot running. Waiting for messages...")
    offset = read_offset()
    while True:
        try:
            resp = api(token, "getUpdates", offset=offset + 1, timeout=50)
        except Exception as e:  # noqa: BLE001 — never let polling kill the loop
            log(f"poll error: {e}")
            time.sleep(5)
            continue
        if not resp.get("ok"):
            time.sleep(5)
            continue
        for upd in resp.get("result", []):
            # Advance the offset FIRST and persist it, so a message that
            # makes us crash isn't replayed forever on the next restart.
            offset = upd["update_id"]
            write_offset(offset)
            try:
                handle_update(upd, token, allowed, child_env)
            except Exception as e:  # noqa: BLE001 — one bad update must not stop the bot
                log(f"update {offset} failed: {e!r}")


def handle_update(upd, token, allowed, child_env):
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "") or ""
    if allowed and chat_id not in allowed:
        send(token, chat_id, f"Not authorized. Your chat ID is {chat_id} — "
                             "add it to TELEGRAM_ALLOWED_CHAT_IDS to enable.")
        return
    if not allowed:
        send(token, chat_id, f"Bot is not locked down yet. Your chat ID is {chat_id}. "
                             "Add it to TELEGRAM_ALLOWED_CHAT_IDS in .env and restart.")
        return
    urls = URL_RE.findall(text)
    if not urls:
        send(token, chat_id, "Send me a YouTube/podcast link (optionally with your own notes "
                             "after it) and I'll summarize it into Notion.")
        return
    notes = extract_notes(text, urls)
    extra = " + your notes" if notes else ""
    send(token, chat_id, f"Working on {len(urls)} link(s){extra} via NotebookLM — "
                         "this takes a couple of minutes...")
    send(token, chat_id, process_links(urls, child_env, notes))


if __name__ == "__main__":
    # Outer guard: if main() ever falls over (truly unexpected), wait and
    # restart rather than leaving the process dead until the next login.
    while True:
        try:
            main()
        except SystemExit:
            raise  # config errors (missing token) should stay fatal
        except Exception as e:  # noqa: BLE001
            log(f"bot crashed, restarting in 10s: {e!r}")
            time.sleep(10)
