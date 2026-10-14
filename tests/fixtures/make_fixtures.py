"""Generate the test fixtures under tests/fixtures/generated/ (SPEC §18).

Dev-only: needs ffmpeg (from the FFMPEG environment variable, PATH, or the known install folder). The generated
files are committed, so running the tests never needs ffmpeg. The output is deterministic (ffmpeg bitexact flags,
fixed tags), and re-running overwrites the script's own files; nothing else is touched.

Tags are written with mutagen after encoding, directly per tag family, so this script doesn't depend on the app
code it is meant to test.

    uv run python tests/fixtures/make_fixtures.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.apev2 import APEv2File
from mutagen.asf import ASF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack

OUT = Path(__file__).resolve().parent / "generated"
DEFAULT_FFMPEG = Path(r"C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe")

# Extension -> ffmpeg output arguments. Speex is missing: this ffmpeg build has no libspeex encoder.
ENCODERS: dict[str, list[str]] = {
    "tone.flac": ["-c:a", "flac"],
    "tone.ogg": ["-c:a", "libvorbis", "-q:a", "2"],
    "tone.oga": ["-c:a", "flac", "-f", "ogg"],
    "tone.opus": ["-c:a", "libopus", "-b:a", "32k"],
    "tone.mp3": ["-c:a", "libmp3lame", "-b:a", "64k", "-id3v2_version", "0", "-write_id3v1", "0"],
    "tone.mp2": ["-c:a", "mp2", "-b:a", "64k"],
    "tone.wav": ["-c:a", "pcm_s16le"],
    "tone.aiff": ["-c:a", "pcm_s16be"],
    "tone_aac.m4a": ["-c:a", "aac", "-b:a", "64k"],
    "tone_alac.m4a": ["-c:a", "alac"],
    "tone.m4b": ["-c:a", "aac", "-b:a", "64k"],
    "tone.wv": ["-c:a", "wavpack"],
    "tone.tta": ["-c:a", "tta"],
    "tone.wma": ["-c:a", "wmav2", "-b:a", "64k"],
    "tone.aac": ["-c:a", "aac", "-b:a", "64k", "-f", "adts"],
}


def find_ffmpeg() -> str:
    for candidate in (os.environ.get("FFMPEG"), shutil.which("ffmpeg"), DEFAULT_FFMPEG):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    sys.exit("ffmpeg not found: set FFMPEG or put it on PATH")


FFMPEG = ""


def encode(target: Path, codec_args: list[str], seconds: float = 1.0, frequency: int = 440,
           source: str | None = None) -> None:
    """Encode a 1/8-amplitude sine, or `source` (an aevalsrc expression of t) when given."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        lavfi = f"sine=frequency={frequency}:duration={seconds}:sample_rate=44100"
    else:
        lavfi = f"aevalsrc=exprs={source.replace(',', chr(92) + ',')}:d={seconds}:s=44100"
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", lavfi,
         "-ac", "1", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
         *codec_args, str(target)],
        check=True,
    )


def make_jpeg(target: Path, color: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=16x16",
         "-frames:v", "1", "-fflags", "+bitexact", "-flags:v", "+bitexact", str(target)],
        check=True,
    )


# ---------------------------------------------------------------------------
# Tag writers, one per family. `tags` uses neutral keys: title artist albumartist album track track_total disc
# disc_total year genre comment lyrics, plus rg (dict of lowercase REPLAYGAIN_* key -> value).
# ---------------------------------------------------------------------------

def write_vorbis(audio, tags: dict) -> None:
    keys = {"title": "TITLE", "artist": "ARTIST", "albumartist": "ALBUMARTIST", "album": "ALBUM",
            "track": "TRACKNUMBER", "track_total": "TRACKTOTAL", "disc": "DISCNUMBER", "disc_total": "DISCTOTAL",
            "year": "DATE", "genre": "GENRE", "comment": "COMMENT", "lyrics": "LYRICS"}
    for name, key in keys.items():
        if name in tags:
            audio[key] = str(tags[name])
    for key, value in tags.get("rg", {}).items():
        audio[key.upper()] = value
    audio.save()


def number_pair(tags: dict, number: str, total: str) -> str:
    return f"{tags[number]}/{tags[total]}" if total in tags else str(tags[number])


def write_id3(audio, tags: dict, v2_version: int = 4, lowercase_rg: bool = False) -> None:
    if audio.tags is None:
        audio.add_tags()
    id3: ID3 = audio.tags
    frames = {"title": TIT2, "artist": TPE1, "albumartist": TPE2, "album": TALB, "year": TDRC, "genre": TCON}
    for name, frame in frames.items():
        if name in tags:
            id3.add(frame(encoding=3, text=str(tags[name])))
    if "track" in tags:
        id3.add(TRCK(encoding=3, text=number_pair(tags, "track", "track_total")))
    if "disc" in tags:
        id3.add(TPOS(encoding=3, text=number_pair(tags, "disc", "disc_total")))
    if "comment" in tags:
        id3.add(COMM(encoding=3, lang="eng", desc="", text=tags["comment"]))
    if "lyrics" in tags:
        id3.add(USLT(encoding=3, lang="eng", desc="", text=tags["lyrics"]))
    for key, value in tags.get("rg", {}).items():
        id3.add(TXXX(encoding=3, desc=key.lower() if lowercase_rg else key.upper(), text=value))
    audio.save(v2_version=v2_version)


def write_mp4(audio: MP4, tags: dict) -> None:
    keys = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "year": "\xa9day",
            "genre": "\xa9gen", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
    if audio.tags is None:
        audio.add_tags()
    for name, key in keys.items():
        if name in tags:
            audio.tags[key] = [str(tags[name])]
    if "track" in tags:
        audio.tags["trkn"] = [(tags["track"], tags.get("track_total", 0))]
    if "disc" in tags:
        audio.tags["disk"] = [(tags["disc"], tags.get("disc_total", 0))]
    audio.save()


def write_ape(audio, tags: dict) -> None:
    keys = {"title": "Title", "artist": "Artist", "albumartist": "Album Artist", "album": "Album", "year": "Year",
            "genre": "Genre", "comment": "Comment", "lyrics": "Lyrics"}
    if audio.tags is None:
        audio.add_tags()
    for name, key in keys.items():
        if name in tags:
            audio.tags[key] = str(tags[name])
    if "track" in tags:
        audio.tags["Track"] = number_pair(tags, "track", "track_total")
    if "disc" in tags:
        audio.tags["Disc"] = number_pair(tags, "disc", "disc_total")
    audio.save()


def write_asf(audio: ASF, tags: dict) -> None:
    keys = {"title": "Title", "artist": "Author", "albumartist": "WM/AlbumArtist", "album": "WM/AlbumTitle",
            "year": "WM/Year", "genre": "WM/Genre", "comment": "Description", "lyrics": "WM/Lyrics"}
    for name, key in keys.items():
        if name in tags:
            audio[key] = [str(tags[name])]
    if "track" in tags:
        audio["WM/TrackNumber"] = [str(tags["track"])]
    if "disc" in tags:
        audio["WM/PartOfSet"] = [number_pair(tags, "disc", "disc_total")]
    audio.save()


def tag_file(path: Path, tags: dict, **id3_options) -> None:
    """Write `tags` with the family writer matching the file."""
    name = path.name.lower()
    if name.endswith(".flac"):
        write_vorbis(FLAC(path), tags)
    elif name.endswith(".ogg"):
        write_vorbis(OggVorbis(path), tags)
    elif name.endswith(".oga"):
        write_vorbis(OggFLAC(path), tags)
    elif name.endswith(".opus"):
        write_vorbis(OggOpus(path), tags)
    elif name.endswith((".mp3", ".mp2")):
        write_id3(MP3(path), tags, **id3_options)
    elif name.endswith(".wav"):
        write_id3(WAVE(path), tags, **id3_options)
    elif name.endswith(".aiff"):
        write_id3(AIFF(path), tags, **id3_options)
    elif name.endswith(".tta"):
        write_id3(TrueAudio(path), tags, **id3_options)
    elif name.endswith((".m4a", ".m4b")):
        write_mp4(MP4(path), tags)
    elif name.endswith(".wv"):
        write_ape(WavPack(path), tags)
    elif name.endswith(".ape"):
        write_ape(APEv2File(path), tags)
    elif name.endswith(".wma"):
        write_asf(ASF(path), tags)
    elif name.endswith(".aac"):
        pass  # raw ADTS has no tag standard
    else:
        raise ValueError(f"no tag writer for {path.name}")


ALBUM_TAGS = {"artist": "Fixture Artist", "albumartist": "Fixture Artist", "album": "Fixture Album",
              "year": "2024", "genre": "Test", "track_total": 6, "disc": 1, "disc_total": 2}


def track(path: Path, **tags) -> None:
    """A 1-second FLAC or MP3 album track with the album tags plus `tags` (None removes a key)."""
    codec = ENCODERS["tone.mp3"] if path.suffix.lower() == ".mp3" else ENCODERS["tone.flac"]
    id3_options = {k: tags.pop(k) for k in ("v2_version", "lowercase_rg") if k in tags}
    # A different pitch per track, so ReplayGain values differ between tracks.
    encode(path, codec, frequency=220 + 110 * int(tags.get("track") or 0))
    merged = {k: v for k, v in (ALBUM_TAGS | tags).items() if v is not None}
    tag_file(path, merged, **id3_options)


# ---------------------------------------------------------------------------
# Fixture sets
# ---------------------------------------------------------------------------

def make_formats() -> None:
    """formats/: one tagged 1-second tone per format ffmpeg can encode."""
    folder = OUT / "formats"
    for name, codec in ENCODERS.items():
        path = folder / name
        encode(path, codec)
        tag_file(path, {"title": f"Tone {name}", "artist": "Fixture Artist", "albumartist": "Fixture Artist",
                        "album": "Fixture Formats", "track": 1, "track_total": 1, "disc": 1, "disc_total": 1,
                        "year": "2024", "genre": "Test"})


def make_album() -> None:
    """album/: rename cases (unchanged, rename, clash, missing title, fallback mask, invalid characters, uppercase
    extension, ID3v2.3, lowercase TXXX ReplayGain) plus lite/bonus/CD2 subfolders for the ReplayGain step."""
    album = OUT / "album" / "Fixture Artist - Fixture Album"
    track(album / "01 First.flac", track=1, title="First")                  # already named: unchanged
    track(album / "track2.flac", track=2, title="Second")                   # -> 02 Second.flac
    track(album / "clash.flac", track=1, title="First")                     # -> 01 First.flac: name taken
    track(album / "missing_title.flac", track=3)                            # no title: missing tags
    track(album / "no_track.mp3", title="Loose", v2_version=3)              # fallback mask, ID3v2.3
    track(album / "lowercase_rg.mp3", track=4, title="Fourth", lowercase_rg=True,
          rg={"replaygain_track_gain": "-1.00 dB", "replaygain_track_peak": "0.500000"})
    track(album / "bad_chars.flac", track=5, title="Five: A/B?")            # -> 05 Five- A-B-.flac
    track(album / "track6.FLAC", track=6, title="Sixth")                    # -> 06 Sixth.flac
    track(album / "lite" / "01 First.flac", track=1, title="First")         # track data copied by name
    track(album / "lite" / "02 Second.flac", track=2, title="Second")
    track(album / "bonus" / "01 Bonus.flac", track=1, title="Bonus")
    track(album / "CD2" / "01 Disc Two.flac", track=1, title="Disc Two", disc=2)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def cue_sheet(files: list[tuple[str, list[int]]]) -> str:
    """A cue sheet; each entry is (FILE name, track numbers in that file)."""
    lines = ['PERFORMER "Fixture Artist"', 'TITLE "Fixture Album"']
    for name, numbers in files:
        lines.append(f'FILE "{name}" WAVE')
        for offset, number in enumerate(numbers):
            lines += [f"  TRACK {number:02} AUDIO", f'    TITLE "Track {number}"', f"    INDEX 01 00:{offset:02}:00"]
    return "\n".join(lines) + "\n"


def make_cleanup() -> None:
    """cleanup/: the junk protections of SPEC §10."""
    base = OUT / "cleanup"

    # A single-file album: its .cue is kept and its audio file is left out of the rename.
    single = base / "single_file_album"
    encode(single / "Fixture Album.flac", ENCODERS["tone.flac"], seconds=3)
    tag_file(single / "Fixture Album.flac", {"title": "Fixture Album", "artist": "Fixture Artist"})
    write_text(single / "Fixture Album.cue", cue_sheet([("Fixture Album.flac", [1, 2, 3])]))
    write_text(single / "replaygain.log", "Run 2024-01-01 00:00:00   target -15 LUFS\n")
    make_jpeg(single / "folder.jpg", "red")
    write_text(single / "playlist.m3u", "#EXTM3U\nFixture Album.flac\n")
    write_text(single / "info.nfo", "Fixture release info\n")
    write_text(single / "checksums.sfv", "Fixture Album.flac 00000000\n")
    write_text(single / "notes.txt", "Fixture notes\n")

    # Per-track .cue (junk) and a .cue pointing at a missing file (junk).
    per_track = base / "per_track_cue"
    track(per_track / "01 One.flac", track=1, title="One")
    track(per_track / "02 Two.flac", track=2, title="Two")
    write_text(per_track / "album.cue", cue_sheet([("01 One.flac", [1]), ("02 Two.flac", [2])]))
    write_text(per_track / "missing.cue", cue_sheet([("gone.flac", [1, 2])]))

    # .lrc sidecars: one whose lyrics are already in the tags (junk), one whose lyrics aren't (kept).
    lrc = base / "lrc"
    lyrics = "[00:00.00]Fixture lyrics line one\n[00:00.50]Fixture lyrics line two\n"
    track(lrc / "with_lyrics.flac", track=1, title="With Lyrics", lyrics=lyrics.replace("\n", "\r\n"))
    write_text(lrc / "with_lyrics.lrc", lyrics)
    track(lrc / "no_lyrics.flac", track=2, title="No Lyrics")
    write_text(lrc / "no_lyrics.lrc", lyrics)


def edge_picture(data: bytes, picture_type: int) -> Picture:
    picture = Picture()
    picture.type = picture_type
    picture.mime = "image/jpeg"
    picture.data = data
    return picture


def make_edge() -> None:
    """edge/: values that need normalizing ("03/12" in one field, full dates, padded or blank text, legacy
    fallback keys), an iTunes-style COMM with a description, a back cover stored before the front one, and the
    TXXX "comment" frame older versions of the script wrote."""
    from mutagen.id3 import APIC
    from mutagen.mp4 import MP4Cover

    folder = OUT / "edge"
    make_jpeg(folder / "back.jpg", "green")
    make_jpeg(folder / "front.jpg", "yellow")
    back, front = (folder / "back.jpg").read_bytes(), (folder / "front.jpg").read_bytes()

    flac_path = folder / "edge.flac"
    encode(flac_path, ENCODERS["tone.flac"])
    flac = FLAC(flac_path)
    flac["TITLE"] = "  Spaced Title  "
    flac["ARTIST"] = ""
    flac["ALBUM"] = "Edge"
    flac["TRACKNUMBER"] = "03/12"
    flac["DISCNUMBER"] = "1"
    flac["TOTALDISCS"] = "2"
    flac["DATE"] = "2024-05-01"
    flac["COMMENT"] = "edge comment"
    flac["UNSYNCEDLYRICS"] = "Legacy lyrics"
    flac.add_picture(edge_picture(back, 4))
    flac.add_picture(edge_picture(front, 3))
    flac.save()

    mp3_path = folder / "edge.mp3"
    encode(mp3_path, ENCODERS["tone.mp3"])
    mp3 = MP3(mp3_path)
    if mp3.tags is None:
        mp3.add_tags()
    mp3.tags.add(TIT2(encoding=3, text="  Spaced Title  "))
    mp3.tags.add(TPE1(encoding=3, text=""))
    mp3.tags.add(TALB(encoding=3, text="Edge"))
    mp3.tags.add(TRCK(encoding=3, text="03/12"))
    mp3.tags.add(TPOS(encoding=3, text="1/2"))
    mp3.tags.add(TDRC(encoding=3, text="2024-05-01"))
    mp3.tags.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=" 00000001 00000002"))
    mp3.tags.add(COMM(encoding=3, lang="eng", desc="", text="edge comment"))
    mp3.tags.add(TXXX(encoding=3, desc="comment", text="old comment"))
    mp3.tags.add(USLT(encoding=3, lang="eng", desc="", text="Edge lyrics"))
    mp3.tags.add(APIC(encoding=3, mime="image/jpeg", type=4, desc="back", data=back))
    mp3.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=front))
    mp3.save(v2_version=4)

    m4a_path = folder / "edge.m4a"
    encode(m4a_path, ENCODERS["tone_aac.m4a"])
    m4a = MP4(m4a_path)
    if m4a.tags is None:
        m4a.add_tags()
    m4a.tags["\xa9nam"] = ["  Spaced Title  "]
    m4a.tags["\xa9ART"] = [""]
    m4a.tags["\xa9alb"] = ["Edge"]
    m4a.tags["trkn"] = [(3, 12)]
    m4a.tags["disk"] = [(1, 0)]
    m4a.tags["\xa9day"] = ["2024-05-01"]
    m4a.tags["covr"] = [MP4Cover(front, imageformat=MP4Cover.FORMAT_JPEG)]
    m4a.save()


# A 10 ms full-scale burst, then silence: quiet overall, so rsgain's gain pushes the peak over full scale.
SPIKE = "if(lt(t,0.01),0.99*sin(2*PI*1000*t),0)"
LOUD = "0.9*sin(2*PI*440*t)"


def rg_track(path: Path, source: str | None = None, **tags) -> None:
    codec = ENCODERS["tone.mp3"] if path.suffix.lower() == ".mp3" else ENCODERS["tone.flac"]
    id3_options = {k: tags.pop(k) for k in ("v2_version",) if k in tags}
    encode(path, codec, source=source)
    tag_file(path, {"artist": "Fixture Artist", "albumartist": "Fixture Artist", "year": "2024", **tags},
             **id3_options)


def make_replaygain() -> None:
    """replaygain/Fixture Artist/: two albums for the ReplayGain step and discography leveling.
    Album A has tracks that clip after gain, and lite/bonus/CD2 subfolders; Album B is louder, with an
    ID3v2.3 MP3 and a subfolder without audio."""
    a = OUT / "replaygain" / "Fixture Artist" / "Album A"
    rg_track(a / "01 Tone.flac", album="Album A", track=1, title="Tone")
    rg_track(a / "02 Spike.flac", SPIKE, album="Album A", track=2, title="Spike")
    rg_track(a / "03 Spike.mp3", SPIKE, album="Album A", track=3, title="Spike")
    rg_track(a / "lite" / "01 Tone.flac", album="Album A", track=1, title="Tone")
    rg_track(a / "lite" / "02 Spike.flac", SPIKE, album="Album A", track=2, title="Spike")
    rg_track(a / "bonus" / "01 Tone.flac", album="Album A", track=1, title="Tone")
    rg_track(a / "CD2" / "01 Disc Two.flac", album="Album A", track=1, title="Disc Two", disc=2)

    b = OUT / "replaygain" / "Fixture Artist" / "Album B"
    rg_track(b / "01 Loud.flac", LOUD, album="Album B", track=1, title="Loud")
    rg_track(b / "02 Quiet.mp3", album="Album B", track=2, title="Quiet", v2_version=3)
    make_jpeg(b / "Scans" / "back.jpg", "gray")


def main() -> None:
    global FFMPEG
    FFMPEG = find_ffmpeg()
    make_jpeg(OUT / "cover.jpg", "blue")
    make_formats()
    make_album()
    make_cleanup()
    make_edge()
    make_replaygain()
    count = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"{count} fixture files in {OUT}")


if __name__ == "__main__":
    main()
