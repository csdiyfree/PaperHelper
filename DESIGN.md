# PaperHelper — design contract

Personal, single-user, local-only paper library for translated papers.
Frontend and backend are built against this file; change it first if the contract changes.

## Directory layout

```
~/PaperHelper/                       (git repo; the lines marked * are personal data, git-ignored)
├── DESIGN.md
├── README.md, LICENSE, install.sh
├── app/
│   ├── server.py        # local HTTP server, Python stdlib only, 127.0.0.1:4730
│   └── web/             # frontend (index.html, app.css, app.js, icon.svg, vendor/)
├── bin/paperhelper      # CLI (python3 script)
├── macos/build-app.sh   # builds ~/Applications/PaperHelper.app
├── skill/pdf-to-chinese-latex/   # translation skill; install.sh syncs it to ~/.claude/skills/ and ~/.codex/skills/
├── examples/<id>/       # built-in example papers (CC BY), same layout as a paper folder; install.sh copies each
│                        #   into library/示例/ once and records the id in .installed-examples *
├── docs/                # README images
├── config.json        * # {"deepseek_api_key": "...", "model": "deepseek-chat"}  chmod 600, never logged
├── library/<分类>/<paper-id>/ *  # one folder per paper; category = parent folder name
├── work/              * # scratch area for translation projects before ingest
├── logs/server.log    *
└── run/server.pid     *
```

Python: `python3` ≥ 3.9 from `PATH` (`#!/usr/bin/env python3`; the launcher app gets macOS's `/usr/bin/python3`).
`server.py` and the CLI must run on the stdlib alone; only `ingest` needs PyMuPDF (`fitz`) for text extraction.

## Paper folder `library/<分类>/<id>/`

| file | notes |
|---|---|
| `meta.json` | `{id, title_zh, title_en, authors, venue, year, pages, added, abstract_zh?, keywords?, source?, license?}` — `authors` is one display string, `year`/`pages` ints, `added` = `YYYY-MM-DD`, `pages` = page count of original; `source` (URL) and `license` are set on example papers |
| `original.pdf`, `zh.pdf`, `bilingual.pdf` | any may be missing; the frontend disables missing modes |
| `notes.md` | always exists (may be empty). Plain Markdown, written by the user, by DeepSeek (via tools) and by Claude |
| `text/original.txt`, `text/zh.txt` | plain text extracted at ingest, pages delimited by lines `=== 第 N 页 ===` |
| `latex/` | the translation's LaTeX project (main.tex, main_bilingual.tex, images/ …) |

- `id` = folder name, ASCII slug `firstauthorYEAR-keyword` (e.g. `vaswani2017-attention`), unique across the whole library.
- Category names: short Chinese topic names (2–10 chars), no `/`, not starting with `.`.
- A category with no papers is still a folder and still listed.

## HTTP API (JSON unless noted; all on 127.0.0.1:4730)

| method & path | body | response |
|---|---|---|
| `GET /api/ping` | | `{ok: true, version}` |
| `GET /api/library` | | `{root, categories: [{name, count}], papers: [{id, title_zh, title_en, authors, venue, year, pages, added, category, has_notes, modes: ["zh","bilingual","original"]}]}` — `modes` lists only files that exist |
| `GET /files/<id>/<mode>.pdf` | | PDF bytes (`mode` ∈ zh / bilingual / original), 404 if missing |
| `GET /api/papers/<id>/notes` | | `{content, mtime}` |
| `PUT /api/papers/<id>/notes` | `{content}` | `{ok, mtime}` — atomic write (temp file + rename) |
| `POST /api/papers/<id>/move` | `{category}` | `{ok, category}` — creates the category folder if needed |
| `POST /api/categories/rename` | `{from, to}` | `{ok}` (merges into `to` if it exists) |
| `GET /api/config` | | `{has_key, model}` — never returns the key |
| `PUT /api/config` | `{deepseek_api_key?, model?}` | `{ok}` |
| `POST /api/chat` | `{scope: "library"｜"paper", paper_id?, page?, mode?, messages: [{role: "user"｜"assistant", content}]}` | `text/event-stream`, see below |

Errors: JSON `{error: "中文说明"}` with 4xx/5xx. Everything else is static from `app/web/` with `Cache-Control: no-cache`.

### Chat event stream (`POST /api/chat`)

Server-sent events, each `event: <name>\ndata: <json>\n\n`:

| event | data | meaning |
|---|---|---|
| `step` | `{id, label}` | a tool call started; `label` is Chinese, e.g. `读取论文 · 中文版 第 1–5 页` |
| `step_done` | `{id, summary}` | that tool call finished; `summary` short Chinese, e.g. `找到 2 篇` |
| `delta` | `{text}` | streamed answer text (Markdown) |
| `library_changed` | `{}` | a tool moved/renamed something — frontend refetches `/api/library` |
| `notes_changed` | `{paper_id}` | a tool wrote notes — frontend reloads them if that paper is open |
| `error` | `{message}` | Chinese message (e.g. missing API key, network/HTTP error) |
| `done` | `{}` | always last |

## DeepSeek harness (inside server.py)

- `POST {base}/chat/completions`, `base` = env `PAPERHELPER_DEEPSEEK_BASE` or `https://api.deepseek.com`; `Authorization: Bearer <key>`; model from config (default `deepseek-chat`); `stream: true`; OpenAI-style `tools`.
- Loop ≤ 8 rounds: stream content deltas straight to the client; accumulate streamed `tool_calls` deltas; when `finish_reason == "tool_calls"`, run the tools, append the assistant message (with `tool_calls`) and one `role: "tool"` message per call, continue; stop at `finish_reason == "stop"`.
- System prompt (Chinese): you are PaperHelper's assistant for the user's personal library at `~/PaperHelper/library`; answer in concise Chinese Markdown; ground answers in tool results, cite pages as `第 N 页`; never invent papers; moving/renaming is reversible, do it directly when asked and say what changed; when writing notes, append unless told to replace, and keep the user's existing content. Inject scope: library scope → note that list/search tools cover everything; paper scope → that paper's meta, current mode and page.
- Tools (JSON-schema `parameters`):
  - `list_library()` → categories and papers (id, titles, year, category, has_notes)
  - `search_library(query, limit=10)` → hits across meta, notes and text, each `{id, title_zh, category, where: "标题|笔记|正文", page?, snippet}`
  - `read_paper(paper_id, version="zh"|"original", start_page=1, end_page=null)` → page-marked text, capped at ~24 000 chars with a truncation notice telling which pages to request next
  - `read_notes(paper_id)`
  - `write_notes(paper_id, content, mode="append"|"replace")`
  - `move_paper(paper_id, category)`
  - `rename_category(from, to)`

## CLI `~/PaperHelper/bin/paperhelper`

| command | does |
|---|---|
| `serve` | run the server in the foreground |
| `start` | start in background if not already up (pid file, log), wait until `/api/ping` answers |
| `stop` | stop the background server |
| `open [id]` | `start`, then open `http://127.0.0.1:4730/` (or `#/paper/<id>/zh`) |
| `categories` | categories with counts — Claude reads this before choosing a category |
| `list` | `id  category  title_zh` per paper |
| `path <id>` | print the paper folder |
| `ingest --id ID --category CAT --original PDF [--zh PDF] [--bilingual PDF] [--latex DIR] --title-zh T --title-en T --authors A --venue V --year N [--abstract-zh S] [--keywords k1,k2] [--replace]` | create `library/CAT/ID/`, copy files, extract `text/*.txt`, write `meta.json` (pages from original, added = today) and empty `notes.md`; refuse an existing id unless `--replace` (which keeps notes.md); print the folder |

## Launch

`~/Applications/PaperHelper.app` (AppleScript applet, ad-hoc signed) runs `paperhelper open`. Claude runs `paperhelper open <id>` after ingesting (pdf-to-chinese-latex Step 9). `open` reuses a server that already answers `/api/ping`, otherwise starts one in the background, then opens the URL as a Chrome app window (`open -na "Google Chrome" --args --app=<url>`), falling back to the default browser.

## Implementation notes (as built)

- `mtime` is seconds (float, `st_mtime`).
- Malformed `/api/chat` requests get a JSON 4xx before streaming; only a missing key and DeepSeek-side failures arrive as `error` events.
- Writes are refused (403) when `Origin` is cross-site or `Host` isn't `127.0.0.1:<port>` / `localhost:<port>` — blocks other web pages and DNS rebinding.
- `search_library` body-text hits also carry `version` (zh / original) because page numbers differ between the two.
- Env overrides for testing: `PAPERHELPER_HOME`, `PAPERHELPER_PORT`, `PAPERHELPER_DEEPSEEK_BASE`, `PAPERHELPER_NO_BROWSER=1`.
- Frontend keeps per-scope chat history in `localStorage` (`ph-chat-library`, `ph-chat-paper:<id>`); notes are always on disk.
- PDF text selection uses pdf.js's `endOfContent` anchoring (class `active`, pdf.js 4.4) so drags through gaps don't select to the end of the page.
