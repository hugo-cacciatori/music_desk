#!/usr/bin/env python3
"""
Seek Lyrics
===========
Finds the lyrics of every song in a music library and shows them in one
self-contained, searchable HTML page (songs.html).

For each .flac / .mp3 / .wav file (recursively):
  1. lyrics already stored in the file's tags are used as they are;
  2. otherwise they are fetched from LRCLIB (https://lrclib.net), a free lyrics
     API that needs no key and also has time-synced lyrics;
  3. songs that LRCLIB doesn't know get Genius and Google search links instead.

Results are cached in lyrics_cache.json in the scanned folder, so a rerun only
looks up new songs (songs not found are retried after 30 days).

Usage:
    python seek_lyrics.py [path] [-o songs.html] [--offline] [--embed] [--no-open]

    --offline  don't contact LRCLIB: use the file tags and the cache only
    --embed    also write the lyrics found online into the files' tags
               (synced lyrics when available, stored as LRC text), so music
               players show them too. Only the tags are rewritten.

Only the artist, title, album and duration of each song are sent to LRCLIB.

Requires:
    pip install mutagen
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, USLT
except ImportError:
    sys.exit("This script needs the 'mutagen' library to read audio tags.\nInstall it with:  pip install mutagen")

AUDIO_EXTS = {".flac", ".mp3", ".wav"}
LRCLIB_API = "https://lrclib.net/api"
USER_AGENT = "SeekLyrics/2.0 (personal music library script)"
CACHE_NAME = "lyrics_cache.json"
NOT_FOUND_RETRY_DAYS = 30
DURATION_TOLERANCE = 3   # seconds between a file and an LRCLIB search result
LOOKUP_THREADS = 4
REQUEST_TIMEOUT = 30     # seconds
MAX_ATTEMPTS = 4         # per request on network errors, with a growing pause between attempts
MAX_OVERLOAD_RETRIES = 10  # LRCLIB often answers "server busy, retry in a moment" (503/429)
MAX_CONSECUTIVE_FAILURES = 10  # stop looking up when LRCLIB seems unreachable
CACHE_SAVE_EVERY = 100   # songs, so an interrupted run keeps its progress

LRC_TIMESTAMP = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", re.M)


# --------------------------------------------------------------------------
# Reading the library
# --------------------------------------------------------------------------

def tag_text(tags, *keys):
    """First non-empty value among tag keys (Vorbis names or ID3 frame ids)."""
    if not tags:
        return None
    for key in keys:
        value = tags.get(key)
        if value is None:
            continue
        if hasattr(value, "text"):          # ID3 frame
            value = value.text
        if isinstance(value, list):
            value = value[0] if value else None
        value = str(value).strip() if value is not None else ""
        if value:
            return value
    return None


def embedded_lyrics(audio):
    """Lyrics stored in the file's tags, or None."""
    tags = audio.tags
    if not tags:
        return None
    if isinstance(tags, ID3):
        for frame in tags.getall("USLT"):
            if str(frame.text).strip():
                return str(frame.text).strip()
        return None
    return tag_text(tags, "lyrics", "unsyncedlyrics")


def parse_from_filename(name):
    """Best-effort 'Artist - Title' fallback when tags are missing."""
    stem = re.sub(r"^\d{1,3}[\s\-._]+", "", Path(name).stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip() or None, title.strip()
    return None, stem.strip()


def read_song(path, root):
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("unrecognized or corrupt audio file")
    tags = audio.tags
    artist = tag_text(tags, "artist", "TPE1", "albumartist", "TPE2")
    title = tag_text(tags, "title", "TIT2")
    album = tag_text(tags, "album", "TALB")
    if not artist or not title:
        file_artist, file_title = parse_from_filename(path.name)
        artist, title = artist or file_artist, title or file_title
    return {
        "path": path.relative_to(root).as_posix(),
        "artist": artist,
        "title": title,
        "album": album,
        "duration": round(getattr(audio.info, "length", 0) or 0),
        "embedded": embedded_lyrics(audio),
    }


def scan(root):
    songs, errors = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if Path(name).suffix.lower() not in AUDIO_EXTS:
                continue
            path = Path(dirpath) / name
            try:
                songs.append(read_song(path, root))
            except Exception as e:  # noqa: BLE001 - keep scanning on bad files
                errors.append(f"{path.relative_to(root).as_posix()}: {e}")
    return songs, errors


# --------------------------------------------------------------------------
# LRCLIB
# --------------------------------------------------------------------------

def lrclib_get(endpoint, **params):
    """GET an LRCLIB endpoint; returns the decoded JSON, or None for an unknown song.

    "Server busy" answers (503/429, frequent on LRCLIB) are retried up to
    MAX_OVERLOAD_RETRIES times after the pause the server asks for; network errors
    and other server errors up to MAX_ATTEMPTS times with a growing pause. The
    error is raised only when the retries are used up.
    """
    url = f"{LRCLIB_API}/{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    overloads = failures = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # unknown song, or a query LRCLIB refuses
                return None
            if e.code in (429, 503):
                overloads += 1
                if overloads > MAX_OVERLOAD_RETRIES:
                    raise
                delay = retry_after(e) or min(10, overloads)
            elif e.code in (500, 502, 504):
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise
                delay = 2 ** failures
            else:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            failures += 1
            if failures >= MAX_ATTEMPTS:
                raise
            delay = 2 ** failures
        # a little randomness so parallel lookups don't all retry at the same instant
        time.sleep(delay + random.uniform(0, 0.5))


def retry_after(error):
    """Seconds asked by the server's Retry-After header (capped at 60), or None."""
    try:
        return min(60, int(error.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return None


def normalize(text):
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def matches(song, record):
    """Whether an LRCLIB search result is the same recording as the file."""
    if song["duration"] and record.get("duration") and \
            abs(song["duration"] - record["duration"]) > DURATION_TOLERANCE:
        return False
    wanted, found = normalize(song["artist"]), normalize(record.get("artistName"))
    return bool(wanted and found) and (wanted in found or found in wanted)


def fetch_lyrics(song):
    """Look a song up on LRCLIB: exact match first, then a search. Returns a cache entry."""
    record = None
    if song["album"] and song["duration"]:
        record = lrclib_get("get", artist_name=song["artist"], track_name=song["title"],
                            album_name=song["album"], duration=song["duration"])
    if not record:
        results = lrclib_get("search", track_name=song["title"], artist_name=song["artist"]) or []
        candidates = [r for r in results if matches(song, r)]
        # prefer results that actually carry lyrics, synced ones first
        candidates.sort(key=lambda r: (not r.get("syncedLyrics"), not r.get("plainLyrics")))
        record = candidates[0] if candidates else None

    today = date.today().isoformat()
    if not record:
        return {"status": "not_found", "checked": today}
    if record.get("instrumental"):
        return {"status": "instrumental", "lrclib_id": record.get("id"), "checked": today}
    if not (record.get("plainLyrics") or record.get("syncedLyrics")):
        return {"status": "not_found", "checked": today}
    return {
        "status": "found",
        "source": "lrclib",
        "lrclib_id": record.get("id"),
        "plain": record.get("plainLyrics") or strip_timestamps(record["syncedLyrics"]),
        "synced": record.get("syncedLyrics"),
        "checked": today,
    }


def strip_timestamps(lrc):
    return "\n".join(LRC_TIMESTAMP.sub("", line).strip() for line in lrc.splitlines()).strip()


# --------------------------------------------------------------------------
# Lyrics for every song: tags > cache > LRCLIB
# --------------------------------------------------------------------------

def cache_key(song):
    return f"{song['artist']}\x1f{song['title']}".casefold()


def from_tags(text):
    synced = text if LRC_TIMESTAMP.search(text) else None
    return {"status": "found", "source": "tags", "plain": strip_timestamps(text) if synced else text,
            "synced": synced}


def find_all_lyrics(songs, root, offline):
    """Attach a 'lyrics' entry to every song. Returns the number of LRCLIB lookups made."""
    cache_path = root / CACHE_NAME
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cache = {}
    except (OSError, ValueError) as e:
        print(f"  warning: could not read {CACHE_NAME} ({e}), starting a new one")
        cache = {}
    retry_before = (date.today() - timedelta(days=NOT_FOUND_RETRY_DAYS)).isoformat()

    to_fetch = {}
    for song in songs:
        if song["embedded"]:
            song["lyrics"] = from_tags(song["embedded"])
        elif not (song["artist"] and song["title"]):
            song["lyrics"] = {"status": "missing_tags"}
        else:
            cached = cache.get(cache_key(song))
            if cached and (cached["status"] != "not_found" or cached["checked"] >= retry_before):
                song["lyrics"] = cached
            elif offline:
                song["lyrics"] = {"status": "not_looked_up"}
            else:
                to_fetch.setdefault(cache_key(song), []).append(song)

    if to_fetch:
        print(f"  looking up {len(to_fetch)} song(s) on LRCLIB...")
        # LRCLIB is considered unreachable after too many failures in a row: the remaining songs are skipped
        breaker = {"consecutive": 0, "stopped": False}
        lock = threading.Lock()

        def lookup(key):
            if breaker["stopped"]:
                return key, None
            try:
                entry = fetch_lyrics(to_fetch[key][0])
                with lock:
                    breaker["consecutive"] = 0
            except Exception as e:  # noqa: BLE001 - one failing song must not stop the others
                entry = {"status": "error", "error": f"{type(e).__name__}: {e}"}
                with lock:
                    breaker["consecutive"] += 1
                    if breaker["consecutive"] >= MAX_CONSECUTIVE_FAILURES:
                        breaker["stopped"] = True
            return key, entry

        done, failures = 0, Counter()
        try:
            with ThreadPoolExecutor(LOOKUP_THREADS) as pool:
                for key, entry in pool.map(lookup, list(to_fetch)):
                    done += 1
                    sys.stderr.write(f"\r  {done}/{len(to_fetch)}")
                    if entry is None:
                        entry = {"status": "not_looked_up"}
                    elif entry["status"] == "error":  # not cached: retried on the next run
                        failures[entry["error"]] += 1
                    else:
                        cache[key] = entry
                    for song in to_fetch[key]:
                        song["lyrics"] = entry
                    if done % CACHE_SAVE_EVERY == 0:
                        save_cache(cache_path, cache)
        finally:
            sys.stderr.write("\n")
            save_cache(cache_path, cache)

        if failures:
            print(f"  {sum(failures.values())} song(s) could not be checked (they will be retried next run):")
            for reason, count in failures.most_common(3):
                print(f"    {count} x {reason}")
        if breaker["stopped"]:
            skipped = sum(1 for songs_ in to_fetch.values() if songs_[0]["lyrics"]["status"] == "not_looked_up")
            print(f"  LRCLIB seems unreachable ({MAX_CONSECUTIVE_FAILURES} failures in a row), "
                  f"so {skipped} song(s) were skipped. Run the script again later to finish.")
    return len(to_fetch)


def save_cache(cache_path, cache):
    try:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print(f"  warning: could not save {CACHE_NAME} ({e})")


# --------------------------------------------------------------------------
# Writing lyrics into the files (--embed)
# --------------------------------------------------------------------------

def embed_lyrics(songs, root):
    """Write the lyrics found online into the files' tags. Returns (written, errors)."""
    written, errors = 0, []
    for song in songs:
        lyrics = song["lyrics"]
        if lyrics.get("status") != "found" or lyrics.get("source") != "lrclib":
            continue
        text = lyrics.get("synced") or lyrics.get("plain")
        path = root / song["path"]
        try:
            audio = mutagen.File(path)
            if isinstance(audio, FLAC):
                audio["LYRICS"] = text
            else:
                if audio.tags is None:
                    audio.add_tags()
                audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
            audio.save()
            written += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{song['path']}: {e}")
    return written, errors


# --------------------------------------------------------------------------
# HTML page
# --------------------------------------------------------------------------

def render_html(songs, root, errors):
    payload = {
        "root": str(root),
        "generated": date.today().isoformat(),
        "errors": errors,
        "songs": [{
            "artist": s["artist"] or "Unknown",
            "title": s["title"] or Path(s["path"]).stem,
            "album": s["album"],
            "path": s["path"],
            "duration": s["duration"],
            "status": s["lyrics"]["status"],
            "source": s["lyrics"].get("source"),
            "plain": s["lyrics"].get("plain"),
            "synced": s["lyrics"].get("synced"),
            "error": s["lyrics"].get("error"),
        } for s in sorted(songs, key=lambda s: ((s["artist"] or "").casefold(), (s["title"] or "").casefold()))],
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return PAGE_TEMPLATE.replace("__LYRICS_DATA__", data)


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Song Lyrics</title>
<style>
  :root{
    --bg:#14161b; --panel:#1c1f26; --raised:#262a33; --line:#2e323c;
    --text:#f1ede4; --muted:#8b909b; --dim:#5b6068;
    --amber:#e3a857; --amber-dim:#6b5432; --cyan:#6ee7d0; --cyan-dim:#2f5c53; --coral:#e8685c; --coral-dim:#5c332f;
    --mono:ui-monospace,'JetBrains Mono',Consolas,monospace;
  }
  *{ box-sizing:border-box; }
  html,body{ margin:0; height:100%; }
  body{ background:var(--bg); color:var(--text); font:15px/1.5 system-ui,'Segoe UI',sans-serif; display:flex; flex-direction:column; }
  a{ color:var(--cyan); }
  header{ padding:18px 24px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:12px 28px; align-items:baseline; justify-content:space-between; }
  h1{ font-family:var(--mono); font-size:22px; margin:0; }
  .summary{ display:flex; gap:18px; flex-wrap:wrap; font-family:var(--mono); font-size:12px; color:var(--muted); }
  .summary b{ color:var(--text); font-size:15px; margin-right:4px; }
  main{ flex:1; min-height:0; display:grid; grid-template-columns:230px minmax(260px,1fr) minmax(320px,1.4fr); }
  aside, .songs, .reader{ min-height:0; overflow-y:auto; }
  aside{ border-right:1px solid var(--line); padding:16px; }
  aside h2, .reader-meta{ font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); margin:0 0 10px; font-weight:600; }
  .artist{ display:flex; align-items:center; gap:8px; padding:4px 6px; border-radius:6px; cursor:pointer; font-size:13.5px; color:var(--muted); }
  .artist:hover{ background:var(--raised); color:var(--text); }
  .artist input{ accent-color:var(--amber); margin:0; }
  .artist .n{ margin-left:auto; font-family:var(--mono); font-size:11px; color:var(--dim); }
  .clear{ background:none; border:none; color:var(--cyan); cursor:pointer; font-size:12px; padding:0; margin-bottom:8px; }
  .songs{ border-right:1px solid var(--line); display:flex; flex-direction:column; }
  .toolbar{ position:sticky; top:0; background:var(--bg); padding:14px 16px 10px; border-bottom:1px solid var(--line); z-index:1; }
  .search{ position:relative; }
  #search{ width:100%; background:var(--raised); border:1px solid var(--line); color:var(--text); padding:9px 34px 9px 12px; border-radius:7px; font-size:14px; outline:none; }
  #search:focus{ border-color:var(--amber); }
  #reset{ position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--muted); font-size:18px; cursor:pointer; display:none; }
  .filters{ display:flex; gap:6px; margin-top:10px; flex-wrap:wrap; }
  .chip{ font-family:var(--mono); font-size:11px; background:var(--raised); color:var(--muted); border:1px solid var(--line); padding:4px 9px; border-radius:6px; cursor:pointer; }
  .chip.active{ background:var(--amber-dim); color:var(--amber); border-color:var(--amber); }
  #count{ font-family:var(--mono); font-size:11px; color:var(--dim); margin-top:8px; }
  .song{ padding:10px 16px; border-bottom:1px solid var(--line); cursor:pointer; display:flex; gap:10px; align-items:center; }
  .song:hover{ background:var(--panel); }
  .song.selected{ background:var(--raised); box-shadow:inset 3px 0 var(--amber); }
  .song .main{ flex:1; min-width:0; }
  .song .t{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .song .a{ font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .song .snippet{ font-size:12px; color:var(--dim); font-style:italic; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  mark{ background:var(--amber-dim); color:var(--amber); border-radius:2px; }
  .badge{ flex:none; font-family:var(--mono); font-size:10.5px; padding:2px 7px; border-radius:5px; }
  .b-found{ background:var(--cyan-dim); color:var(--cyan); }
  .b-instrumental{ background:var(--amber-dim); color:var(--amber); }
  .b-missing{ background:var(--coral-dim); color:var(--coral); }
  .reader{ padding:24px 32px 60px; }
  .reader h2{ font-size:24px; margin:0 0 2px; }
  .reader .by{ color:var(--muted); margin-bottom:6px; }
  .reader-meta{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:12px 0 18px; }
  .lyrics{ white-space:pre-wrap; font-size:16px; line-height:1.75; }
  .lyrics .ts{ font-family:var(--mono); font-size:11.5px; color:var(--dim); margin-right:10px; }
  .links{ display:flex; gap:10px; flex-wrap:wrap; margin-top:20px; }
  .links a{ font-family:var(--mono); font-size:12px; text-decoration:none; background:var(--raised); border:1px solid var(--line); padding:6px 11px; border-radius:7px; }
  .links a:hover{ border-color:var(--cyan); }
  .empty{ color:var(--dim); padding:40px 16px; text-align:center; }
  @media (max-width:900px){
    body{ display:block; height:auto; }
    main{ display:block; }
    aside, .songs, .reader{ overflow:visible; border-right:none; }
    aside{ max-height:220px; overflow-y:auto; border-bottom:1px solid var(--line); }
    .songs{ max-height:60vh; overflow-y:auto; border-bottom:1px solid var(--line); }
    .reader{ padding:20px 16px 40px; }
  }
</style>
</head>
<body>
<header>
  <h1>Song lyrics</h1>
  <div class="summary" id="summary"></div>
</header>
<main>
  <aside>
    <h2>Artists</h2>
    <button class="clear" id="clear-artists" hidden>clear selection</button>
    <div id="artists"></div>
  </aside>
  <section class="songs">
    <div class="toolbar">
      <div class="search">
        <input id="search" type="text" placeholder="Search titles, artists and lyrics..." autocomplete="off">
        <button id="reset" title="Clear search">&times;</button>
      </div>
      <div class="filters" id="filters"></div>
      <div id="count"></div>
    </div>
    <div id="song-list"></div>
  </section>
  <section class="reader" id="reader"><div class="empty">Select a song to read its lyrics</div></section>
</main>

<script id="data" type="application/json">__LYRICS_DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const songs = DATA.songs.map((s, i) => ({...s, id:i, haystack:[s.title, s.artist, s.album, s.plain].join('\n').toLowerCase()}));
const STATUS = {
  found:        {label:'lyrics',       cls:'b-found'},
  instrumental: {label:'instrumental', cls:'b-instrumental'},
  not_found:    {label:'not found',    cls:'b-missing'},
  missing_tags: {label:'no tags',      cls:'b-missing'},
  not_looked_up:{label:'not checked',  cls:'b-missing'},
  error:        {label:'lookup failed',cls:'b-missing'},
};
const selectedArtists = new Set();
let statusFilter = 'all', selectedId = null, showSynced = true;

const el = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* summary */
const counts = songs.reduce((acc, s) => (acc[s.status] = (acc[s.status]||0)+1, acc), {});
el('summary').innerHTML = [
  ['songs', songs.length], ['with lyrics', counts.found||0], ['instrumental', counts.instrumental||0],
  ['not found', (counts.not_found||0) + (counts.missing_tags||0)],
  ['not checked', (counts.not_looked_up||0) + (counts.error||0)],
].filter(([k,v]) => v || k!=='not checked').map(([k,v]) => `<span><b>${v}</b>${k}</span>`).join('');

/* artists */
const artistCounts = new Map();
songs.forEach(s => artistCounts.set(s.artist, (artistCounts.get(s.artist)||0)+1));
el('artists').innerHTML = [...artistCounts].sort((a,b) => a[0].localeCompare(b[0]))
  .map(([a,n]) => `<label class="artist"><input type="checkbox" value="${esc(a)}"><span>${esc(a)}</span><span class="n">${n}</span></label>`).join('');
el('artists').addEventListener('change', e => {
  e.target.checked ? selectedArtists.add(e.target.value) : selectedArtists.delete(e.target.value);
  el('clear-artists').hidden = !selectedArtists.size;
  render();
});
el('clear-artists').addEventListener('click', () => {
  selectedArtists.clear();
  el('artists').querySelectorAll('input').forEach(i => i.checked = false);
  el('clear-artists').hidden = true;
  render();
});

/* status filter */
[['all','All'], ['found','With lyrics'], ['missing','Missing']].forEach(([key,label]) => {
  const b = document.createElement('button');
  b.className = 'chip' + (key===statusFilter ? ' active' : '');
  b.textContent = label; b.dataset.key = key;
  b.addEventListener('click', () => {
    statusFilter = key;
    el('filters').querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.key===key));
    render();
  });
  el('filters').appendChild(b);
});

/* search */
el('search').addEventListener('input', () => { el('reset').style.display = el('search').value ? 'block' : 'none'; render(); });
el('reset').addEventListener('click', () => { el('search').value = ''; el('reset').style.display = 'none'; render(); el('search').focus(); });

function highlight(text, q){
  if(!q) return esc(text);
  const i = text.toLowerCase().indexOf(q);
  return i < 0 ? esc(text) : esc(text.slice(0,i)) + '<mark>' + esc(text.slice(i, i+q.length)) + '</mark>' + esc(text.slice(i+q.length));
}
function snippet(s, q){
  if(!q || !s.plain) return '';
  if((s.title + ' ' + s.artist).toLowerCase().includes(q)) return '';
  const line = s.plain.split('\n').find(l => l.toLowerCase().includes(q));
  return line ? `<div class="snippet">“${highlight(line.trim(), q)}”</div>` : '';
}

function render(){
  const q = el('search').value.trim().toLowerCase();
  const visible = songs.filter(s =>
    (!selectedArtists.size || selectedArtists.has(s.artist)) &&
    (statusFilter==='all' || (statusFilter==='found') === (s.status==='found' || s.status==='instrumental')) &&
    (!q || s.haystack.includes(q)));
  el('count').textContent = `${visible.length} of ${songs.length} songs`;
  el('song-list').innerHTML = visible.length ? visible.map(s => `
    <div class="song${s.id===selectedId?' selected':''}" data-id="${s.id}">
      <div class="main">
        <div class="t">${highlight(s.title, q)}</div>
        <div class="a">${highlight(s.artist, q)}${s.album ? ' · ' + esc(s.album) : ''}</div>
        ${snippet(s, q)}
      </div>
      <span class="badge ${STATUS[s.status].cls}">${STATUS[s.status].label}</span>
    </div>`).join('') : '<div class="empty">No song matches</div>';
}
el('song-list').addEventListener('click', e => {
  const row = e.target.closest('.song');
  if(!row) return;
  selectedId = +row.dataset.id;
  el('song-list').querySelectorAll('.song').forEach(r => r.classList.toggle('selected', r===row));
  showSong(songs[selectedId]);
});

function lyricsHtml(s){
  if(showSynced && s.synced){
    return s.synced.split('\n').map(line => {
      const m = line.match(/^\s*\[(\d{1,2}:\d{2})(?:[.:]\d{1,3})?\]\s?(.*)$/);
      return m ? `<span class="ts">${m[1]}</span>${esc(m[2])}` : esc(line);
    }).join('\n');
  }
  return esc(s.plain);
}
function showSong(s){
  const query = encodeURIComponent(`${s.artist} ${s.title}`);
  const links = `<div class="links">
      <a href="https://genius.com/search?q=${query}" target="_blank" rel="noopener">Search on Genius</a>
      <a href="https://www.google.com/search?q=${query}+lyrics" target="_blank" rel="noopener">Search on Google</a>
    </div>`;
  const meta = [];
  if(s.status==='found') meta.push(s.source==='tags' ? 'from the file tags' : 'from LRCLIB');
  if(s.synced) meta.push(`<button class="chip${showSynced?' active':''}" id="sync-toggle">${showSynced?'hide':'show'} timestamps</button>`);
  let body;
  if(s.status==='found') body = `<div class="lyrics">${lyricsHtml(s)}</div>`;
  else if(s.status==='instrumental') body = '<p class="by">LRCLIB lists this track as instrumental.</p>';
  else if(s.status==='missing_tags') body = '<p class="by">This file has no artist/title tags, so it could not be looked up.</p>';
  else if(s.status==='not_looked_up') body = '<p class="by">Not checked yet (offline run, or LRCLIB was unreachable). Run the script again online.</p>';
  else if(s.status==='error') body = `<p class="by">The LRCLIB lookup failed (${esc(s.error)}). It will be retried on the next run.</p>`;
  else body = '<p class="by">LRCLIB has no lyrics for this song.</p>';
  el('reader').innerHTML = `
    <h2>${esc(s.title)}</h2>
    <div class="by">${esc(s.artist)}${s.album ? ' · ' + esc(s.album) : ''}</div>
    <div class="reader-meta">${meta.join('')}</div>
    ${body}${links}`;
  el('reader').scrollTop = 0;
  const toggle = el('sync-toggle');
  if(toggle) toggle.addEventListener('click', () => { showSynced = !showSynced; showSong(s); });
}

render();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find the lyrics of a music library and show them in an HTML page.")
    parser.add_argument("path", nargs="?", default=".", help="Root folder to scan (default: current directory)")
    parser.add_argument("-o", "--output", default="songs.html", help="Output HTML file (default: songs.html)")
    parser.add_argument("--offline", action="store_true", help="Use the file tags and the cache only")
    parser.add_argument("--embed", action="store_true", help="Write the lyrics found online into the files' tags")
    parser.add_argument("--no-open", action="store_true", help="Don't open the page in the browser")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    print(f"Scanning {root} ...")
    songs, errors = scan(root)
    if not songs:
        sys.exit("No .flac / .mp3 / .wav files found.")

    lookups = find_all_lyrics(songs, root, args.offline)

    if args.embed:
        written, embed_errors = embed_lyrics(songs, root)
        print(f"  lyrics written into {written} file(s)")
        errors += [f"could not write lyrics: {e}" for e in embed_errors]

    out_path = Path(args.output).expanduser().resolve()
    out_path.write_text(render_html(songs, root, errors), encoding="utf-8")

    statuses = Counter(s["lyrics"]["status"] for s in songs)
    not_checked = statuses["not_looked_up"] + statuses["error"]
    print(f"\n{len(songs)} songs: {statuses['found']} with lyrics, {statuses['instrumental']} instrumental, "
          f"{statuses['not_found'] + statuses['missing_tags']} not found"
          + (f", {not_checked} not checked" if not_checked else "")
          + f" ({lookups} looked up on LRCLIB this run)")
    for error in errors:
        print(f"  ! {error}")
    print(f"Page written to: {out_path}")
    if not args.no_open:
        webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
