#!/usr/bin/env python3
"""create_persona.py — build a NotebookLM "persona" from a person's best videos.

Given a person, podcast, YouTube channel, topic, or YouTube link, this script:

1. Discovers candidate YouTube videos with ``yt-dlp`` (real like/view counts).
2. Filters out Shorts, clips, trailers, highlights, and unavailable videos.
3. Ranks the survivors ("most liked" first, per the skill spec).
4. Cleans every URL to the canonical ``https://www.youtube.com/watch?v=ID`` form.
5. Adds the top N links as sources to a named NotebookLM notebook
   (reuse-or-create by name), skipping links already present.

The resulting notebook becomes a knowledge base you can chat with in NotebookLM
as that person's "persona". The clean raw links are also printed to stdout.

Self-contained: only Python stdlib + the ``yt-dlp`` and ``nlm`` CLIs are needed.

Usage:
    python create_persona.py "<target>" --notebook "<name>" [--count N]
    python create_persona.py "Tony Fernandes" --notebook "Tony Fernandes Persona"
    python create_persona.py "https://www.youtube.com/@LennysPodcast" -n "Lenny" -c 20
    python create_persona.py "Naval Ravikant" --dry-run          # find + print only
    python create_persona.py "Charlie Munger" -n Munger --ranking # show like counts

Flags:
    --notebook, -n  NAME   Target NotebookLM notebook (reuse-or-create by name).
    --count, -c     N      Number of links to keep/add (default 10).
    --titles               Output mode: "N. Title - url".
    --ranking              Output mode: "N. Title - <likes> likes - url".
    --dry-run / --no-push  Discover + print only; do not touch NotebookLM.
    --channel              Force treating the target URL as a channel.
    --playlist             Force treating the target URL as a playlist.
    --pool          N      Size of the candidate pool to enrich (advanced).

Prereqs (one-time):
    uv tool install yt-dlp               # installs `yt-dlp`
    uv tool install notebooklm-mcp-cli   # installs `nlm`
    nlm login                            # Google login for NotebookLM
"""
import json
import os
import re
import shutil
import subprocess
import sys

# Windows stdout/stderr default to cp1252, which crashes when printing video
# titles that contain CJK/accented characters or emoji. Force UTF-8 so no print
# can ever take the script down.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Breadth of the cheap "flat" enumeration for a person/topic search
# (``ytsearchN``). Flat listing is a single fast request, so we can scan wide
# before enriching. Channels/playlists are always enumerated in full.
DEFAULT_SEARCH_BREADTH = 40

# We only fully extract (to read like counts) this many of the most-viewed
# candidates. Full extraction is the slow, throttling-prone step, so it is kept
# small — enough to survive post-filtering but bounded so runtime stays low.
ENRICH_MAXIMUM = 20

# Minimum duration (seconds) for a video to count as a full episode rather than
# a Short or micro-clip.
MINIMUM_FULL_EPISODE_SECONDS = 90

# Title keywords that mark a video as a clip/trailer rather than a full episode.
CLIP_TITLE_PATTERN = re.compile(
    r"\b(clip|clips|trailer|teaser|preview|sneak\s*peek|#shorts?|short)\b",
    re.IGNORECASE,
)
# "highlight(s)" is only treated as a clip when the video is also fairly short,
# because full episodes are sometimes titled "... highlights".
HIGHLIGHT_TITLE_PATTERN = re.compile(r"\bhighlights?\b", re.IGNORECASE)
HIGHLIGHT_MAX_SECONDS = 20 * 60

# NotebookLM free tier allows roughly this many sources per notebook.
NOTEBOOK_SOURCE_SOFT_CAP = 50

# A YouTube video id is exactly 11 URL-safe characters.
VIDEO_ID_PATTERN = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #

# On Windows, launching a console app flashes a console window. These flags run
# the child process with no window. They are ignored on non-Windows platforms.
_NO_WINDOW_KWARGS = {}
if os.name == "nt":
    _startup_info = subprocess.STARTUPINFO()
    _startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _startup_info.wShowWindow = subprocess.SW_HIDE
    _NO_WINDOW_KWARGS = {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": _startup_info,
    }


def run_command(command, timeout=600):
    """Run a subprocess and capture its output as UTF-8 text.

    Args:
        command: Argument list to execute.
        timeout: Maximum seconds to wait before raising ``TimeoutExpired``.

    Returns:
        Tuple ``(return_code, stdout, stderr)``. Decoding never raises because
        undecodable bytes are replaced.
    """
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", **_NO_WINDOW_KWARGS,
    )
    return result.returncode, result.stdout, result.stderr


def find_executable(name, install_hint):
    """Locate a CLI executable, exiting with an install hint if it is missing.

    Args:
        name: Executable base name (e.g. ``"nlm"`` or ``"yt-dlp"``).
        install_hint: One-line instruction shown when the executable is absent.

    Returns:
        Absolute path to the executable.
    """
    override = os.environ.get(f"{name.upper().replace('-', '_')}_BIN")
    if override and os.path.exists(override):
        return override
    found = shutil.which(name)
    if found:
        return found
    for guess in (
        os.path.expanduser(f"~/.local/bin/{name}"),
        os.path.expanduser(f"~/.local/bin/{name}.exe"),
    ):
        if os.path.exists(guess):
            return guess
    sys.exit(f"ERROR: `{name}` not found. Install with: {install_hint}")


# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #

def extract_video_id(url):
    """Return the 11-character YouTube video id in ``url``, or ``""``."""
    match = VIDEO_ID_PATTERN.search(url or "")
    return match.group(1) if match else ""


def clean_youtube_url(video):
    """Return the canonical watch URL for a discovered video.

    Prefers the video ``id``; falls back to parsing ``webpage_url``. Strips all
    tracking, playlist, and timestamp parameters by reconstructing the URL from
    the id alone.
    """
    video_id = video.get("id") or extract_video_id(video.get("webpage_url", ""))
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


# --------------------------------------------------------------------------- #
# Target classification
# --------------------------------------------------------------------------- #

def classify_target(target, force_channel=False, force_playlist=False):
    """Classify a user-supplied target into a discovery mode.

    Args:
        target: A URL, or free text such as a person/podcast/topic name.
        force_channel: Treat a URL as a channel regardless of its shape.
        force_playlist: Treat a URL as a playlist regardless of its shape.

    Returns:
        Tuple ``(kind, value)`` where ``kind`` is one of ``"video"``,
        ``"playlist"``, ``"channel"`` or ``"search"`` and ``value`` is the URL
        or query text to feed the discovery step.
    """
    is_url = target.startswith("http://") or target.startswith("https://")
    if not is_url:
        return "search", target
    if force_playlist:
        return "playlist", target
    if force_channel:
        return "channel", channel_videos_url(target)
    if "list=" in target or "/playlist" in target:
        return "playlist", target
    if re.search(r"/(channel/|@|c/|user/)", target):
        return "channel", channel_videos_url(target)
    if "watch?v=" in target or "youtu.be/" in target or "/shorts/" in target:
        return "video", target
    # Unknown YouTube URL shape: let yt-dlp decide by handing it over directly.
    return "playlist", target


def channel_videos_url(url):
    """Point a channel URL at its uploads tab so yt-dlp lists full videos.

    Leaves the URL unchanged if it already targets a specific tab.
    """
    if re.search(r"/(videos|streams|shorts|playlists|featured)/?$", url):
        return url
    return url.rstrip("/") + "/videos"


# --------------------------------------------------------------------------- #
# Discovery (yt-dlp)
# --------------------------------------------------------------------------- #

def run_ytdlp_json(ytdlp, extra_args, timeout=600):
    """Run yt-dlp in JSON mode and return a list of parsed video dictionaries.

    yt-dlp with ``-j`` prints one JSON object per line. Unparseable lines
    (warnings, blank lines) are skipped so a single bad entry never aborts
    discovery.
    """
    command = [ytdlp, "-j", "--ignore-errors", "--no-warnings",
               "--extractor-retries", "1", "--socket-timeout", "15"] + extra_args
    _, stdout, _ = run_command(command, timeout=timeout)
    videos = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            videos.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return videos


def run_ytdlp_flat(ytdlp, url, timeout=120):
    """List a channel/playlist/search cheaply without extracting each video.

    Flat listing returns ids, titles, durations, and (usually) view counts for
    many videos in a single request, so we can pre-filter and pre-rank a wide
    pool before the slow full extraction that yields like counts.
    """
    return run_ytdlp_json(ytdlp, ["--flat-playlist", url], timeout=timeout)


def discover_videos(ytdlp, kind, value, count, breadth, official_only=False):
    """Discover candidate videos while minimising slow full extractions.

    For every multi-video target the strategy is the same: enumerate cheaply
    with a flat pass, drop non-videos/Shorts/clips, pre-rank by view count, then
    fully extract only the most promising handful (bounded by
    :data:`ENRICH_MAXIMUM`) to read like counts. A single video URL is extracted
    directly.

    Channel/playlist flat listings do not expose view counts, so their
    candidates stay in newest-first order and ranking is effectively "most-liked
    among recent uploads". For all-time most-liked of a person, search their
    name (searches do carry view counts) with ``official_only=True`` to keep
    only their own channel.

    Args:
        ytdlp: Path to the ``yt-dlp`` executable.
        kind: Target kind from :func:`classify_target`.
        value: URL or search text for the target.
        count: Number of links the caller ultimately wants.
        breadth: How many search results to scan (searches only).
        official_only: For a search, keep only results whose channel name
            matches the query (i.e. the person's own uploads).

    Returns:
        List of yt-dlp video dictionaries with full metadata (incl. like_count).
    """
    if kind == "video":
        return run_ytdlp_json(ytdlp, [value], timeout=120)

    if kind == "search":
        flat = run_ytdlp_flat(ytdlp, f"ytsearch{breadth}:{value}")
        query = value
    else:  # channel or playlist: flat lists all uploads in one cheap request.
        flat = run_ytdlp_flat(ytdlp, value)
        query = ""

    # Keep only real videos (11-char ids; search can also return channels) that
    # survive a cheap pre-filter.
    candidates = [entry for entry in flat
                  if len(str(entry.get("id") or "")) == 11 and is_full_episode(entry)]
    # Restrict to the person's own channel before spending extractions on it.
    if official_only and query:
        candidates = [entry for entry in candidates if looks_official(entry, query)]
    # Pre-rank by popularity (search entries carry view counts; channel entries
    # do not, so they keep their newest-first order here).
    candidates.sort(key=lambda entry: entry.get("view_count") or 0, reverse=True)

    enrich_count = min(count + max(5, count // 2), ENRICH_MAXIMUM)
    chosen = candidates[:enrich_count]
    if not chosen:
        return []
    watch_urls = [f"https://www.youtube.com/watch?v={entry['id']}" for entry in chosen]
    return run_ytdlp_json(ytdlp, watch_urls, timeout=300)


# --------------------------------------------------------------------------- #
# Filtering and ranking
# --------------------------------------------------------------------------- #

def is_full_episode(video):
    """Return True if the video looks like a full episode worth keeping.

    Rejects Shorts, live/upcoming streams, unavailable videos, and titles that
    mark the video as a clip, trailer, or (short) highlight.
    """
    availability = video.get("availability")
    if availability not in (None, "public", "unlisted"):
        return False
    if video.get("live_status") in ("is_live", "is_upcoming"):
        return False

    duration = video.get("duration")
    if duration is not None and duration < MINIMUM_FULL_EPISODE_SECONDS:
        return False
    if "/shorts/" in (video.get("webpage_url") or ""):
        return False

    title = video.get("title") or ""
    if CLIP_TITLE_PATTERN.search(title):
        return False
    if HIGHLIGHT_TITLE_PATTERN.search(title):
        if duration is None or duration < HIGHLIGHT_MAX_SECONDS:
            return False
    return True


def looks_official(video, query):
    """Heuristically decide whether a video is an official/primary upload.

    For channel/playlist targets there is no query and every video is treated as
    official. For searches, a video counts as official when a meaningful token
    from the query appears in the uploader/channel name.
    """
    if not query:
        return True
    channel_name = (video.get("channel") or video.get("uploader") or "").lower()
    tokens = [token for token in re.split(r"\W+", query.lower()) if len(token) > 2]
    return any(token in channel_name for token in tokens)


def rank_videos(videos, query):
    """Sort videos by the skill's ranking rules, most preferred first.

    Order: like count, then view count, then official upload, then longer
    (full episode), then original discovery order as the final tiebreak.
    """
    def sort_key(indexed):
        index, video = indexed
        like_count = video.get("like_count")
        view_count = video.get("view_count")
        return (
            like_count if like_count is not None else -1,
            view_count if view_count is not None else -1,
            1 if looks_official(video, query) else 0,
            video.get("duration") or 0,
            -index,
        )

    ordered = sorted(enumerate(videos), key=sort_key, reverse=True)
    return [video for _, video in ordered]


def deduplicate_by_video_id(videos):
    """Return videos with duplicate video ids removed, keeping first seen."""
    seen = set()
    unique = []
    for video in videos:
        video_id = video.get("id")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        unique.append(video)
    return unique


# --------------------------------------------------------------------------- #
# NotebookLM (nlm)
# --------------------------------------------------------------------------- #

def find_or_create_notebook(nlm, name):
    """Reuse a notebook with the given name, or create one if none exists.

    Args:
        nlm: Path to the ``nlm`` executable.
        name: Notebook title to match (case-insensitive) or create.

    Returns:
        Tuple ``(notebook_id, was_created)``.
    """
    return_code, stdout, _ = run_command([nlm, "notebook", "list", "--json"])
    if return_code == 0:
        try:
            for notebook in json.loads(stdout):
                if (notebook.get("title") or "").strip().lower() == name.strip().lower():
                    return notebook["id"], False
        except json.JSONDecodeError:
            pass

    return_code, stdout, stderr = run_command([nlm, "notebook", "create", name])
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                      stdout)
    if not match:
        sys.exit(f"ERROR creating notebook '{name}':\n{stdout}\n{stderr}")
    return match.group(1), True


def normalize_title(title):
    """Lowercase, trim, and collapse whitespace so titles compare reliably."""
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def existing_sources(nlm, notebook_id):
    """Return video ids and normalized titles already present in the notebook.

    NotebookLM's source listing reports ``url: null`` for YouTube sources, so
    the title is the reliable dedup key; a video id is also collected whenever a
    url happens to be present. Best-effort: any failure yields empty sets so a
    listing hiccup never blocks adding new sources.

    Returns:
        Tuple ``(video_ids, normalized_titles)`` as sets.
    """
    return_code, stdout, _ = run_command([nlm, "source", "list", notebook_id, "--json"])
    video_ids, titles = set(), set()
    if return_code != 0:
        return video_ids, titles
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return video_ids, titles
    for source in data if isinstance(data, list) else []:
        if not isinstance(source, dict):
            continue
        video_id = extract_video_id(source.get("url") or "")
        if video_id:
            video_ids.add(video_id)
        normalized = normalize_title(source.get("title"))
        if normalized:
            titles.add(normalized)
    return video_ids, titles


def add_youtube_sources(nlm, notebook_id, urls, timeout=1200):
    """Add YouTube URLs to a notebook as sources, waiting for processing.

    Returns:
        Tuple ``(added_count, stdout, stderr)``.
    """
    command = [nlm, "source", "add", notebook_id]
    for url in urls:
        command += ["--youtube", url]
    command += ["--wait", "--wait-timeout", str(timeout - 60)]
    return_code, stdout, stderr = run_command(command, timeout=timeout)
    added = len(re.findall(r"Added source", stdout))
    return added, stdout, stderr


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def humanize_count(count):
    """Format a like/view count compactly (e.g. 10500 -> '10.5K')."""
    if count is None:
        return "?"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")
    if count >= 1_000:
        return f"{count / 1_000:.1f}K".replace(".0K", "K")
    return str(count)


def format_results(videos, urls, mode):
    """Render the chosen videos in the requested output mode.

    Args:
        videos: Ranked, trimmed list of video dictionaries.
        urls: Cleaned watch URLs aligned with ``videos``.
        mode: ``"raw"``, ``"titles"``, or ``"ranking"``.

    Returns:
        A string ready to print.
    """
    if mode == "raw":
        return "\n\n".join(urls)
    lines = []
    for position, (video, url) in enumerate(zip(videos, urls), start=1):
        title = video.get("title") or "Untitled"
        if mode == "ranking":
            likes = humanize_count(video.get("like_count"))
            lines.append(f"{position}. {title} — {likes} likes — {url}")
        else:
            lines.append(f"{position}. {title} — {url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Argument parsing and orchestration
# --------------------------------------------------------------------------- #

def parse_arguments(argv):
    """Parse the command line into a settings dictionary.

    Positional words form the target (a URL or free-text query). Flags are
    parsed manually to keep the script dependency-free.
    """
    settings = {
        "target_words": [], "notebook": None, "count": 10, "mode": "raw",
        "push": True, "force_channel": False, "force_playlist": False,
        "pool": None, "official_only": False,
    }
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in ("--notebook", "-n"):
            index += 1
            settings["notebook"] = argv[index]
        elif argument in ("--count", "-c"):
            index += 1
            settings["count"] = int(argv[index])
        elif argument == "--pool":
            index += 1
            settings["pool"] = int(argv[index])
        elif argument == "--titles":
            settings["mode"] = "titles"
        elif argument == "--ranking":
            settings["mode"] = "ranking"
        elif argument in ("--dry-run", "--no-push"):
            settings["push"] = False
        elif argument == "--official-only":
            settings["official_only"] = True
        elif argument == "--channel":
            settings["force_channel"] = True
        elif argument == "--playlist":
            settings["force_playlist"] = True
        elif argument.startswith("-"):
            sys.exit(f"Unknown flag: {argument}")
        else:
            settings["target_words"].append(argument)
        index += 1

    if not settings["target_words"]:
        sys.exit(__doc__)
    settings["target"] = " ".join(settings["target_words"])
    return settings


def main():
    """Discover, rank, print, and (optionally) push videos to NotebookLM."""
    settings = parse_arguments(sys.argv[1:])
    ytdlp = find_executable("yt-dlp", "uv tool install yt-dlp")

    kind, value = classify_target(
        settings["target"], settings["force_channel"], settings["force_playlist"])
    breadth = settings["pool"] or DEFAULT_SEARCH_BREADTH

    raw_videos = discover_videos(ytdlp, kind, value, settings["count"], breadth,
                                 official_only=settings["official_only"])
    full_episodes = [video for video in raw_videos if is_full_episode(video)]
    unique_videos = deduplicate_by_video_id(full_episodes)
    query = value if kind == "search" else ""
    ranked = rank_videos(unique_videos, query)
    chosen = ranked[: settings["count"]]

    if not chosen:
        sys.exit("No suitable full-episode videos found for that target.")

    pairs = [(video, clean_youtube_url(video)) for video in chosen]
    pairs = [(video, url) for video, url in pairs if url]
    chosen = [video for video, _ in pairs]
    urls = [url for _, url in pairs]

    print(format_results(chosen, urls, settings["mode"]))

    if not settings["push"]:
        return
    if not settings["notebook"]:
        print("\n(No --notebook given, so nothing was added to NotebookLM.)")
        return

    nlm = find_executable("nlm", "uv tool install notebooklm-mcp-cli")
    notebook_id, was_created = find_or_create_notebook(nlm, settings["notebook"])
    print(f"\nnotebook: {settings['notebook']} ({notebook_id})"
          f"{' [created]' if was_created else ' [reused]'}")

    existing_ids, existing_titles = existing_sources(nlm, notebook_id)
    new_urls = []
    for video, url in pairs:
        if extract_video_id(url) in existing_ids:
            continue
        if normalize_title(video.get("title")) in existing_titles:
            continue
        new_urls.append(url)
    skipped = len(urls) - len(new_urls)

    if not new_urls:
        print(f"added 0, skipped {skipped} (already present). Nothing to do.")
        return

    projected_total = len(existing_titles) + len(new_urls)
    if projected_total > NOTEBOOK_SOURCE_SOFT_CAP:
        print(f"!! warning: this would bring the notebook to ~{projected_total} "
              f"sources, above NotebookLM's ~{NOTEBOOK_SOURCE_SOFT_CAP} free-tier cap.")

    added, stdout, stderr = add_youtube_sources(nlm, notebook_id, new_urls)
    if added == 0:
        sys.exit(f"ERROR adding sources:\n{stdout}\n{stderr}")
    print(f"added {added}, skipped {skipped} (already present).")


if __name__ == "__main__":
    main()
