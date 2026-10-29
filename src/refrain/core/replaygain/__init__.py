"""The ReplayGain step (SPEC §10 B; port of music_tools' ReplayGain step, same order, values and report text): rsgain
album scan, clipping fix, album values, LUFS comment, subfolder copies, discography leveling, loose tracks and the
removal. One module per concern; this package exports what the rest of the app uses.

Changes from the script (SPEC §17): explicit folders instead of the cwd, every format rsgain supports, tags
through core/tags.py, a Reporter instead of print, cancellation, and a crash is recorded without re-raising.
"""

from refrain.core.replaygain.album import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    TRACK_GAIN,
    TRACK_PEAK,
    Scanner,
    adjust_gain_to_prevent_clipping,
    copy_replaygain_metadata,
    find_keyword_subfolders,
    image_names,
    list_audio_files,
    parse_replaygain,
    process_album,
    read_track_values,
    scan_album,
    update_album_gain_metadata,
)
from refrain.core.replaygain.discography import (
    AlbumOutcome,
    DiscographyResult,
    discography_summary_text,
    find_album_folders,
    level_album,
    process_discography,
    write_discography_summary,
)
from refrain.core.replaygain.loose import (
    LooseResult,
    process_loose_tracks,
)
from refrain.core.replaygain.options import (
    DEFAULT_OPTIONS,
    TARGET_RANGE,
    ReplayGainOptions,
    options_from_json,
    options_to_json,
    validate,
)
from refrain.core.replaygain.remove import (
    lufs_comment_pattern,
    remove_replaygain,
)
from refrain.core.replaygain.run_log import (
    Clock,
    NullReporter,
    Reporter,
    RunLog,
    files_summary,
    format_gain,
    format_peak_db,
    indent,
)

__all__ = [
    "ALBUM_GAIN",
    "ALBUM_PEAK",
    "AlbumOutcome",
    "Clock",
    "DEFAULT_OPTIONS",
    "DiscographyResult",
    "LooseResult",
    "NullReporter",
    "ReplayGainOptions",
    "Reporter",
    "RunLog",
    "Scanner",
    "TARGET_RANGE",
    "TRACK_GAIN",
    "TRACK_PEAK",
    "adjust_gain_to_prevent_clipping",
    "copy_replaygain_metadata",
    "discography_summary_text",
    "files_summary",
    "find_album_folders",
    "find_keyword_subfolders",
    "format_gain",
    "format_peak_db",
    "image_names",
    "indent",
    "level_album",
    "list_audio_files",
    "lufs_comment_pattern",
    "options_from_json",
    "options_to_json",
    "parse_replaygain",
    "process_album",
    "process_discography",
    "process_loose_tracks",
    "read_track_values",
    "remove_replaygain",
    "scan_album",
    "update_album_gain_metadata",
    "validate",
    "write_discography_summary",
]
