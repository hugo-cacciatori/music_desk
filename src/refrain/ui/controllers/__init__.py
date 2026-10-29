"""One controller per feature (SPEC §16): each owns its pages, runner, saves and job, and gets the window's shared
parts: search, tools (Rename, ReplayGain, Tidy), delete and split."""

STATUS_MS = 8000  # how long a status-bar message stays
