# 27. Legacy text encodings in tags
Specified with Hugo on 2026-10-17 (M16).

**The need** (Hugo): tags with Japanese or Chinese text must read correctly. Read tags in the most accepting way
possible.

## Where text goes wrong
- Vorbis comments, APEv2 and MP4 store text as UTF-8, and ASF as UTF-16. mutagen decodes them, and Japanese and
  Chinese text already reads correctly.
- **ID3** (MP3, and the ID3 chunks of WAV and AIFF) gives each text frame an encoding byte. Frames marked UTF-16
  or UTF-8 are fine. A frame marked **Latin-1**, and every ID3v1 tag (always Latin-1), often hold other bytes:
  Shift-JIS, GBK, Big5 or EUC-KR written by Windows taggers in the system code page, or UTF-8 written by taggers
  that ignore the marker. Read as Latin-1, they show as garbage ("‚±‚ñ‚É‚¿‚Í" for こんにちは).

## The rule: guess per value (Hugo, 2026-10-17)
For each text value read from an ID3 frame marked Latin-1 (and from ID3v1, which mutagen turns into such frames):
1. Pure ASCII text is kept as it is.
2. Otherwise the original bytes are tried as **UTF-8**. Valid UTF-8 wins.
3. Otherwise they are tried as **Shift-JIS (cp932)**, **GBK** and **Big5**. A guess is kept only when
   it decodes strictly and gives clean text: ASCII plus Japanese or Chinese characters and their
   punctuation, with at least one such character. When several codecs pass, the most plausible one wins (see "As
   built" below).
4. Otherwise the value stays **Latin-1**, as today, so Western text with accents is unchanged.

- There is no setting and no menu. The risk Hugo accepted: very rarely, Western text with two accented letters in a
  row could be read as one CJK character.
- It applies wherever tags are read: the index (so search and the file list show the right text), the Metadata
  tab, the rename masks, the LRCLIB lookups, and the copies the split and the trim tag.
- Writing is unchanged: a field the user edits is saved as UTF-8 (encoding 3), as before, so a saved file reads
  correctly in every app. Fields left alone are not rewritten.
- **The index re-reads ID3 files once** (schema v4): the migration clears `read_mtime` of the tracks whose tag
  family is ID3, so the next scan reads their tags again. An index at v4 is refused by an older app, as before.

## As built
Built in M16 task 5 (0.5.9).
- `core/tags.py` `decode_latin1_text(text)`, called by the ID3 adapter's read for every frame whose encoding is
  `Encoding.LATIN1` (text frames, the genre, the comment, USLT). mutagen's ID3v1 frames are marked Latin-1 too.
- A guess must decode strictly, hold only "clean" characters (ASCII, kana, ideographs, CJK and full-width
  punctuation, a few symbols such as · × ° ★ ♪ ①), and at least one kana or ideograph; beside ASCII letters it
  needs two (one CJK character next to Latin letters is how "Àgua" or "Ärzte" misread as Big5).
- **Plausibility**: the share of its CJK characters that are common in that codec (kana and JIS level 1 kanji for
  Shift-JIS, GB2312 level 1 for GBK, Big5's frequent characters A440–C67E). It must be at least 0.8 (real text
  is nearly all common characters; Western accents misread land in the rare areas), and the highest share wins;
  ties go to Shift-JIS, then GBK, then Big5. Half-width katakana never count as common: Latin-1 accents become
  them in Shift-JIS.
- **ID3v1 cut**: a value of exactly 30 bytes (an ID3v1 field) that fails to decode is tried again without its
  last byte, which may be half a character.
- **EUC-KR is left out** (measured while building): Korean hangul bytes are also valid, common GB2312 hanzi, so
  Korean can't be told from Chinese and would read as Chinese. Legacy Korean stays Latin-1; Korean in UTF-8 or
  UTF-16 frames reads fine.
- Known limits: a value of only upper-case accented letters in a row ("ÁÉÍÓÚ" style, no ASCII) can read as
  Chinese; a GBK title whose bytes are also frequent Big5 reads as GBK.
- Schema v4 (`core/index/schema.py`): `UPDATE tracks SET read_mtime = NULL WHERE tag_family = 'ID3'`; every root
  is rescanned at startup (§4), so the next start re-reads them.

## Tests
- Latin-1 frames holding Shift-JIS, GBK, Big5 and UTF-8 bytes read as the right text; an ID3v1 tag in
  Shift-JIS and Big5 too, and an ID3v1 value cut mid-character (`tests/core/test_tag_encodings.py`).
- Western Latin-1 text (accents, "Beyoncé", "Sigur Rós", "Mötley Crüe") stays unchanged; ASCII is untouched;
  UTF-16 and UTF-8 frames are unchanged.
- The migration to v4 clears `read_mtime` for ID3 tracks only, and a scan reads them again.
