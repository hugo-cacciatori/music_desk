# 20. File preview
Specified with Hugo on 2026-10-16 (M9). Album folders hold rip logs, cue sheets, notes and scans. Selecting one
shows it in the side panel, read-only, instead of "<name> isn't an audio file."

**Which files**
- Text: `.txt .log .cue .nfo .sfv .md5 .ffp .accurip .m3u .m3u8 .lrc`
- Images: `.jpg .jpeg .png .gif .bmp .webp` (Qt's image plugins in PySide6-Essentials read them all; a GIF shows
  its first frame).
- PDF booklets can't be previewed: Qt's PDF module is in PySide6-Addons, which the app doesn't ship. They keep the
  "isn't an audio file" message, and double-click opens them.
- The list of types is one constant in core (`preview.PREVIEW_TYPES`), so adding a type is one line.

**When it shows**
- When exactly one previewable file is selected in the file list, the side panel shows a **Preview** page
  (`ui/side_panel/file_preview.py` `FilePreview`) instead of the Metadata/Lyrics tabs. It is the panel's third
  page, alongside `tabs` and the folder `summary`.
- Several items, or one file of another type, keep today's messages (§7). Audio files keep the tabs.

**The page**
- Header:
  - the file name;
  - a details line, for example "12 KB · modified 16/10/2026 14:02 · UTF-16 LE", or "1200 × 1200 px · 245 KB ·
    modified …";
  - an **Open** button (the default app, through the shell thread).
  - The encoding is named for text, so a garbled preview explains itself.
- **Text:**
  - a read-only `QPlainTextEdit` in the system's fixed-width font, with no word wrap (logs and cue sheets are
    column-aligned) and a horizontal scroll bar;
  - selecting and Ctrl+C work, and nothing can be typed.
  - Files over the cap (512 KB, measured below) show their first 512 KB, with "Showing the first 512 KB of
    12 MB. Open it to see the rest."
  - A file with lines over 300 characters is word-wrapped instead (measured below).
- **Images:** scaled to fit the panel's width (never upscaled), keeping the ratio, with EXIF orientation applied.
  - An image that can't be decoded shows "Can't show this image: <reason>" (the fixtures' 3-byte `cover.jpg`
    is such a case).
  - Files over 50 MB aren't decoded: "Too large to preview (80 MB). Open it instead."
- **Every `.cue`** also gets a **Split into tracks…** button, the only way into the cue split (§22; Hugo,
  2026-10-16). It opens the Split page in the centre area, where the sheet can be edited and saved.
- Always read-only: nothing on the page writes, renames or deletes (editing happens on the Split page).

**Reading** (Ground rule 2)
- On the TagIO worker: `TagIO.read_preview(path)` emits `previewRead(PreviewRead(token, …))`. A token per request
  drops stale results, as TagIO does for tags.
- Bytes are read through `fsops.open_shared` (so Explorer can still delete or rename the file meanwhile), up to the
  cap.
- Images are decoded from memory with `QImageReader` and a scaled size, as `TagIO.read_image` already does for
  covers.
- The core part (no Qt) is `core/preview.py` `read_text(path, limit) -> TextPreview(text, encoding, size,
  truncated)`.
- **Text decoding**, in order:
  1. a BOM: UTF-8, or UTF-16 LE/BE (EAC writes its `.log` files in UTF-16 LE with a BOM);
  2. UTF-8, if the bytes are valid;
  3. otherwise Windows-1252, or **CP437 for `.nfo`** (the DOS code page of NFO art), with undecodable bytes
     shown as U+FFFD, never an error.

  `metadata.decode_text` (the `.lrc` import, §7) gets the UTF-16 BOM rule too, so a UTF-16 `.lrc` imports
  correctly.
- A cut inside a multi-byte character is decoded with replacement, never an error.

- **As built (M9 task 1)**, `core/preview.py`:
  - `kind(path)` returns "text", "image" or None, from `TEXT_TYPES` and `IMAGE_TYPES` (`PREVIEW_TYPES` is both).
    The caps are `TEXT_LIMIT` (1 MB) and `IMAGE_LIMIT` (50 MB).
  - `decode(data, legacy, cut)` returns the text and an encoding name: "UTF-8 (BOM)", "UTF-16 LE", "UTF-16 BE",
    "UTF-8", "Windows-1252" or "CP437 (DOS)".
  - A UTF-8 character split at the end counts as UTF-8 only when the cap cut the file (`cut`). A whole cp1252
    file ending in "é" (0xE9, a UTF-8 lead byte) would otherwise read as UTF-8; a test caught this.
  - `read_text` turns CRLF into LF. `metadata.decode_text` is `decode(data)[0]`.

**Refresh**
- When the indexer reports the file's folder updated and the file's size or modified time changed (a log being
  written), the preview reloads, keeping the scroll position when it can.
- When the file disappears, the list's selection changes and the panel follows it.

**Responsiveness:** M9 measures the UI-thread cost of showing 1 MB of text with `StallWatch` (§18). If it is
over 50 ms, the cap is lowered until it isn't, and the number found is recorded here.
- **Measured (M9 task 2, 2026-10-16, this machine):** the time of `FilePreview._read` plus the events after it
  (layout, paint), on a 360 × 700 page, worst of 5–7 runs.
  - **1 MB** of 84-character log lines took 38–52 ms, over the target. **512 KB** took 23–29 ms, so
    **`TEXT_LIMIT` is 512 KB**.
  - **Without word wrap**, Qt lays out every line to find the widest, so line length matters more than size.
    At 512 KB: lines of 200 took ≤ 28 ms, 300 ≤ 32 ms, 500 ≤ 51 ms, 2000 ≤ 162 ms, and 5000 ≤ 370 ms (even at
    256 KB).
  - **With word wrap**, only the visible lines are laid out: lines of 500 to 5000 took 11–25 ms. But **one line**
    of 200,000 characters took 5.7–8.3 s, because a single block is always laid out whole.
  - So (`core/preview.py`): **no wrap** while every line is at most `WRAP_FROM` = 300 characters (logs and cue
    sheets keep their columns). Otherwise **word wrap**, with lines over `BREAK_AT` = 2000 characters cut into
    pieces.
  - **Final check** through `read_text`, with 2 MB files: an EAC-like UTF-16 log 18 ms, UTF-8 log lines 26 ms,
    lines of 300 32 ms, lines of 301 14 ms, one 2 MB line 15 ms, prose lines of 3000 13 ms. **Worst 32 ms.**
- **As built (M9 task 2):**
  - `TagIO.read_preview(path)` emits `previewRead(PreviewRead)` with the text, or the image decoded at most
    1200 px wide (`PREVIEW_WIDTH`, EXIF orientation applied), or an error. `FilePreview` (`ui/side_panel/
    file_preview.py`) is the panel's third page, and fits the image to its width on resize.
  - The details line uses the file list's date format (`2026-10-16 14:02`).
  - The reload compares the index's (size, mtime) for the entry (`SidePanel._stamp`), so no file is touched on
    the UI thread. It happens when the open folder is refreshed (`folder_updated`), keeping the scroll position.
  - Open goes through the window's shell thread, like Open in the menus.
  - Qt reads GIF and WebP through its image plugins, and the frozen build ships `qgif`, `qjpeg` and `qwebp`.
    Qt can't write GIF, so the GIF test image is written by hand.

**Tests**
- Each type.
- An EAC-style UTF-16 LE log, a UTF-8 BOM text, a cp1252 text with accents and a CP437 `.nfo`.
- A text over the cap (generated in `tmp_path`); an image scaled down; an image smaller than the panel (not
  upscaled); a broken image.
- The Split button only on a single-file album's `.cue` (once M10 exists).
- No editable widget on the page.
- A stale read is ignored.
- A reload after the file changes.
- Several items, and audio files, still show the tabs or messages of §7.
