"""The ReplayGain options (the script's configuration block) and their checks and saved values (the ReplayGain
options card)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayGainOptions:
    """The script's configuration block, with its defaults."""

    target: float = -15.0  # LUFS
    safety_threshold: float = 0.99999
    safety_margin: float = 0.01
    subfolder_keywords: tuple[str, ...] = ("lite", "bonus", "cd2", "disc 2", "cd 2")
    track_data_keywords: tuple[str, ...] = ("lite",)
    track_data_excluded_keywords: tuple[str, ...] = ("bonus",)
    log_file: str = "replaygain.log"
    comment: str = "LUFS Target : {target}"

    @property
    def target_text(self) -> str:
        """The target as the script wrote it: "-15", not "-15.0"."""
        return f"{self.target:g}"

    @property
    def lufs_comment(self) -> str:
        return self.comment.format(target=self.target_text)


DEFAULT_OPTIONS = ReplayGainOptions()


TARGET_RANGE = (-30.0, -5.0)  # rsgain's -l accepts -30 <= n <= -5


def validate(options: ReplayGainOptions) -> list[str]:
    """The problems in the options (empty if they are fine). The script had no checks for its ReplayGain block;
    these keep a run from failing halfway (rsgain refusing the target, a log name Windows refuses...)."""
    from refrain.core.masks import FORBIDDEN_CHARACTERS  # the file-name rule, shared with the Rename tool

    problems = []
    low, high = TARGET_RANGE
    if not (isinstance(options.target, int | float) and low <= options.target <= high):
        problems.append(f"The target loudness must be between {low:g} and {high:g} LUFS (rsgain's range)")
    if not 0 < options.safety_threshold <= 1:
        problems.append("The safety threshold must be above 0 and at most 1 (a peak of 1.0 is full scale)")
    if not 0 <= options.safety_margin <= 6:
        problems.append("The safety margin must be between 0 and 6 dB")
    for label, keywords in (("Album-data subfolder keywords", options.subfolder_keywords),
                            ("Track-data keywords", options.track_data_keywords),
                            ("Excluded keywords", options.track_data_excluded_keywords)):
        for keyword in keywords:
            if not keyword.strip():
                problems.append(f"{label}: a keyword is empty")
            elif keyword != keyword.lower():
                problems.append(f"{label}: {keyword!r} must be lowercase (folder names are compared in lowercase)")
    name = options.log_file
    if not name.strip() or name in (".", "..") or FORBIDDEN_CHARACTERS.search(name) or name.endswith((" ", ".")):
        problems.append("The log file name isn't a valid file name")
    try:
        options.comment.format(target=options.target_text)
    except (ValueError, KeyError, IndexError) as e:
        problems.append(f"The comment text is invalid ({e}); it may only use {{target}}")
    return problems


def options_to_json(options: ReplayGainOptions) -> dict:
    return {
        "target": options.target,
        "safety_threshold": options.safety_threshold,
        "safety_margin": options.safety_margin,
        "subfolder_keywords": list(options.subfolder_keywords),
        "track_data_keywords": list(options.track_data_keywords),
        "track_data_excluded_keywords": list(options.track_data_excluded_keywords),
        "log_file": options.log_file,
        "comment": options.comment,
    }


def options_from_json(data) -> ReplayGainOptions:
    """Saved options; a missing or mistyped value falls back to its default (see rename.options_from_json)."""
    data = data if isinstance(data, dict) else {}
    default = DEFAULT_OPTIONS

    def number(key):
        value = data.get(key)
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else getattr(default, key)

    def strings(key):
        value = data.get(key)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return tuple(value)
        return getattr(default, key)

    def text(key):
        value = data.get(key)
        return value if isinstance(value, str) else getattr(default, key)

    return ReplayGainOptions(
        target=number("target"), safety_threshold=number("safety_threshold"), safety_margin=number("safety_margin"),
        subfolder_keywords=strings("subfolder_keywords"), track_data_keywords=strings("track_data_keywords"),
        track_data_excluded_keywords=strings("track_data_excluded_keywords"), log_file=text("log_file"),
        comment=text("comment"))
