"""One controller per feature (SPEC §16): each owns its pages, runner, saves and job, and gets the window's shared
parts: search, tools (Rename, ReplayGain, Tidy), delete and split."""

STATUS_MS = 8000  # how long a status-bar message stays
# Preview in player found no app for cue sheets (the split and trim pages).
NO_CUE_PLAYER = ("No app is set to open .cue sheets. To choose one, open Windows Settings > Apps > Default apps "
                 "and search for .cue.")
