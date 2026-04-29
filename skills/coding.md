# Coding skill

When the user asks you to make changes to a codebase:

1. Before editing, **read** the file(s) you intend to change so the surrounding
   code is in your context. Use `read_file` for small files and `grep` for
   targeted searches in large ones.
2. Prefer `edit_file` over `write_file` for changes to existing files - it
   replaces a single unique span and fails loudly if the span isn't unique,
   which prevents accidental sweeping rewrites. Pass enough surrounding
   context so the `old` string matches exactly once.
3. After meaningful changes, run a quick verification: lint, tests, or a
   minimal `run_python` snippet that imports the module. Don't claim success
   without evidence.
4. Be terse. Show what changed and why; skip restating the obvious.
5. Respect the platform. Use `pathlib` and `sys.executable` rather than
   hard-coded slashes or `python` shell strings.
