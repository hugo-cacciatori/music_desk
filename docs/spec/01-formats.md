# 1. Formats
`core/formats.py` is the single source of truth for this table. A ✓ is the target. At M1, a round-trip test checks
each format, and a row is downgraded (for example to read-only) if mutagen can't do it.

| Extensions        | Container / tag family          | mutagen class        | Tags, lyrics, cover | ReplayGain tool |
|-------------------|---------------------------------|----------------------|---------------------|-----------------|
| .flac             | FLAC / Vorbis comments          | FLAC                 | ✓ (picture block)   | ✓ |
| .ogg .oga         | Ogg Vorbis, Ogg FLAC / Vorbis   | OggVorbis, OggFLAC   | ✓ (METADATA_BLOCK_PICTURE) | ✓ |
| .opus             | Ogg Opus / Vorbis               | OggOpus              | ✓ (METADATA_BLOCK_PICTURE) | ✓ |
| .spx              | Ogg Speex / Vorbis              | OggSpeex             | ✓                   | ✓ |
| .mp3 .mp2         | ID3v2                           | MP3                  | ✓ (APIC)            | ✓ |
| .wav              | RIFF + ID3 chunk                | WAVE                 | ✓ (APIC)            | ✓ |
| .aif .aiff .aifc  | AIFF + ID3 chunk                | AIFF                 | ✓ (APIC)            | ✓ |
| .m4a .mp4         | MP4 atoms (AAC, ALAC)           | MP4                  | ✓ (covr)            | ✓ |
| .m4b              | MP4 atoms                       | MP4                  | ✓ (covr)            | ✗ |
| .ape              | APEv2                           | MonkeysAudio         | ✓ (Cover Art (Front)) | ✓ |
| .wv               | APEv2                           | WavPack              | ✓                   | ✓ |
| .mpc              | APEv2                           | Musepack             | ✓                   | ✓ |
| .tak              | APEv2                           | TAK                  | ✓                   | ✓ |
| .ofr .ofs         | APEv2                           | OptimFROG            | ✓                   | ✗ |
| .tta              | ID3v2 / APEv2                   | TrueAudio            | ✓                   | ✗ |
| .wma              | ASF attributes                  | ASF                  | ✓ (WM/Picture)      | ✓ |
| .dsf              | ID3v2                           | DSF                  | ✓ (APIC)            | ✗ |
| .dff              | DSDIFF + ID3 chunk              | DSDIFF               | ✓                   | ✗ |
| .aac              | raw ADTS (no tag standard)      | AAC                  | stream info only    | ✗ |

- Every extension in the table counts as audio everywhere: browsing, columns, indexing, lyrics and the Rename tool.
  When the ReplayGain tool meets a format rsgain doesn't support, its report lists those files as
  "skipped: format not supported by rsgain".
- `core/tags.py` has one adapter per tag family: Vorbis, ID3, MP4, APEv2, ASF. Keys always match ignoring case.
- Opus: keep rsgain's default `-o d`, which writes standard REPLAYGAIN_* tags and clears the header gain, so the
  post-processing works the same as for FLAC.
- ID3 formats: rsgain's default `-I keep` keeps the file's ID3v2 version, and the app's own writes do the same.
- ReplayGain keys the bundled rsgain 3.6 writes, verified on the fixtures (2026-10-15,
  `tests/core/test_rsgain_keys.py`, `slow`):
  - Vorbis (FLAC, Ogg Vorbis, Ogg FLAC, Opus): `REPLAYGAIN_TRACK_GAIN`/`_PEAK` comments (mutagen lists Vorbis keys
    in lowercase; match ignoring case). Opus gets no R128 tags with the default `-o d`.
  - ID3 (MP3, MP2, WAV, AIFF): `TXXX:REPLAYGAIN_TRACK_GAIN`/`_PEAK`, uppercase descriptions.
  - MP4 (AAC and ALAC .m4a): freeform `----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN`/`_PEAK`, UTF-8 bytes.
  - APEv2 (WavPack) and ASF (WMA): `REPLAYGAIN_TRACK_GAIN`/`_PEAK`.
  - Values: gain `"6.75 dB"` (2 decimals), peak `"0.124969"` (6 decimals).
  - rsgain refuses .m4b, .tta and raw .aac ("not of a supported type"), which confirms the ✗ marks above.
    APE, Musepack, TAK and Speex are still unverified (no fixture yet).

Field mapping:

| Field        | Vorbis                                     | ID3                 | MP4      | APEv2              | ASF               |
|--------------|--------------------------------------------|---------------------|----------|--------------------|-------------------|
| Title        | TITLE                                      | TIT2                | ©nam     | Title              | Title             |
| Artist       | ARTIST                                     | TPE1                | ©ART     | Artist             | Author            |
| Album artist | ALBUMARTIST                                | TPE2                | aART     | Album Artist       | WM/AlbumArtist    |
| Album        | ALBUM                                      | TALB                | ©alb     | Album              | WM/AlbumTitle     |
| Track/total  | TRACKNUMBER, TRACKTOTAL (read TOTALTRACKS) | TRCK "n/N"          | trkn     | Track "n/N"        | WM/TrackNumber (no total) |
| Disc/total   | DISCNUMBER, DISCTOTAL (read TOTALDISCS)    | TPOS "n/N"          | disk     | Disc "n/N"         | WM/PartOfSet "n/N" |
| Year         | DATE                                       | TDRC                | ©day     | Year               | WM/Year           |
| Genre        | GENRE                                      | TCON                | ©gen     | Genre              | WM/Genre          |
| Composer     | COMPOSER                                   | TCOM                | ©wrt     | Composer           | WM/Composer       |
| Comment      | COMMENT                                    | COMM (eng, desc "") | ©cmt     | Comment            | Description       |
| Lyrics       | LYRICS (read UNSYNCEDLYRICS)               | USLT (eng, desc "") | ©lyr     | Lyrics             | WM/Lyrics         |
| Cover        | picture block / METADATA_BLOCK_PICTURE     | APIC type 3         | covr     | Cover Art (Front)  | WM/Picture        |
| ReplayGain   | REPLAYGAIN_*                               | TXXX:REPLAYGAIN_*   | ----:com.apple.iTunes:REPLAYGAIN_* | REPLAYGAIN_* | REPLAYGAIN_* |

WMA has no standard track total, so that field is disabled for .wma, with a tooltip explaining why (as built in
0.2.1: also when a multi-file selection holds a .wma file).
