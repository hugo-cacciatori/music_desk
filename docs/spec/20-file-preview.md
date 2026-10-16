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
  - Files over 1 MB show their first 1 MB, with "Showing the first 1 MB of 12 MB. Open it to see the rest."
- **Images:** scaled to fit the panel's width (never upscaled), keeping the ratio, with EXIF orientation applied.
  - An image that can't be decoded shows "Can't show this image: <reason>" (the fixtures' 3-byte `cover.jpg`
    is such a case).
  - Files over 50 MB aren't decoded: "Too large to preview (80 MB). Open it instead."
- A **single-file album's `.cue`** (the rule of §10 A) also gets **Split into tracks…** (§22). It is added with
  M10, not before.
- Always read-only: nothing on the page writes, renames or deletes.

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

**Refresh**
- When the indexer reports the file's folder updated and the file's size or modified time changed (a log being
  written), the preview reloads, keeping the scroll position when it can.
- When the file disappears, the list's selection changes and the panel follows it.

**Responsiveness:** M9 measures the UI-thread cost of showing 1 MB of text with `StallWatch` (§18). If it is
over 50 ms, the cap is lowered until it isn't, and the number found is recorded here.

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
