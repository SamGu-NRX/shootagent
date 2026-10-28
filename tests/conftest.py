import importlib.util

# tests/models imports the torch-backed adapters at module level. CI installs without the `local` extra
# (no GPU, no torch), so those tests are not collected there; run them locally with the extra installed.
collect_ignore_glob = [] if importlib.util.find_spec("torch") else ["models/*"]
