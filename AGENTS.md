# PaperHelper — notes for coding agents (Codex, Claude Code, …)

- **What this is:** a local paper library app. Contract for every file format, API and CLI command: [DESIGN.md](DESIGN.md) — read it before changing code, and update it first if the contract changes.
- **Translating a paper:** use the `pdf-to-chinese-latex` skill (source in `skill/pdf-to-chinese-latex/`, installed to `~/.claude/skills/` and `~/.codex/skills/` by `./install.sh`). It ends by filing the paper with `bin/paperhelper ingest` and opening it with `bin/paperhelper open <id>`.
- **Sandboxes:** starting the agent in this folder makes `work/` and `library/` writable. `bin/paperhelper open` / `start` reach `127.0.0.1:4730` and open a window — run them with escalated permissions.
- **Personal data:** `library/`, `config.json` (DeepSeek API key), `logs/`, `run/`, `work/` are git-ignored. Never commit them, never print the key. Users' notes live in `library/<分类>/<id>/notes.md` — read them when asked about notes; append, don't overwrite, unless told to.
- **Example papers (`examples/<id>/`):** shipped in the public repo and copied into the library by `install.sh` on first install. Only add papers whose license allows redistributing the original *and* a translation (e.g. CC BY 4.0 — check the license link on the arXiv abstract page), and record attribution and changes in `examples/README.md`. arXiv's default "non-exclusive distribution" license does not allow this.
- **Editing the skill:** change `skill/pdf-to-chinese-latex/`, then run `./install.sh` to sync the installed copies; check with `python3 skill/pdf-to-chinese-latex/scripts/self_check.py`.
- **Style:** stdlib-only Python for `app/server.py` and `bin/paperhelper` (Python ≥ 3.9); vanilla JS/CSS frontend, no build step; minimal UI and subtle motion.
