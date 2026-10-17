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
3. Otherwise they are tried as **Shift-JIS (cp932)**, **GBK**, **Big5** and **EUC-KR**. A guess is kept only when
   it decodes strictly and gives clean text: ASCII plus Japanese, Chinese or Korean characters and their
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
(Filled in by M16 task 5.)

## Tests
- Latin-1 frames holding Shift-JIS, GBK, Big5, EUC-KR and UTF-8 bytes read as the right text; an ID3v1 tag in
  Shift-JIS too.
- Western Latin-1 text (accents, "Beyoncé", "Sigur Rós", "Mötley Crüe") stays unchanged; ASCII is untouched;
  UTF-16 and UTF-8 frames are unchanged.
- The migration to v4 clears `read_mtime` for ID3 tracks only, and a scan reads them again.
