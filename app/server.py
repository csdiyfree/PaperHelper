#!/usr/bin/env python3
"""PaperHelper local server (Python stdlib only).

Serves app/web/ statically and the JSON / SSE API described in ../DESIGN.md.
Binds 127.0.0.1 only.

Env overrides (for testing):
  PAPERHELPER_HOME           data root (default ~/PaperHelper): library/, config.json, logs/, run/
  PAPERHELPER_PORT           port (default 4730)
  PAPERHELPER_DEEPSEEK_BASE  DeepSeek API base (default https://api.deepseek.com)
"""

import bisect
import http.client
import json
import os
import re
import signal
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"

APP_DIR = os.path.dirname(os.path.realpath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
HOME = os.path.abspath(os.path.expanduser(os.environ.get("PAPERHELPER_HOME") or "~/PaperHelper"))
LIB = os.path.join(HOME, "library")
CONFIG_PATH = os.path.join(HOME, "config.json")
LOG_DIR = os.path.join(HOME, "logs")
LOG_PATH = os.path.join(LOG_DIR, "server.log")
RUN_DIR = os.path.join(HOME, "run")
PID_PATH = os.path.join(RUN_DIR, "server.pid")
HOST = "127.0.0.1"
PORT = int(os.environ.get("PAPERHELPER_PORT") or 4730)
DEEPSEEK_BASE = (os.environ.get("PAPERHELPER_DEEPSEEK_BASE") or "https://api.deepseek.com").rstrip("/")
DEFAULT_MODEL = "deepseek-chat"

MODES = ("zh", "bilingual", "original")
MODE_LABEL = {"zh": "中文版", "bilingual": "中英对照版", "original": "原文"}
TEXT_LABEL = {"zh": "中文版", "original": "原文"}
MAX_BODY = 20 * 1024 * 1024
MAX_ROUNDS = 8
READ_CAP = 24000
DEEPSEEK_TIMEOUT = 180  # seconds per blocking read

PAGE_RE = re.compile(r"^=== 第 (\d+) 页 ===[ \t]*$", re.M)
PAGE_INLINE_RE = re.compile(r"=== 第 \d+ 页 ===")

NO_KEY_MSG = "还没有设置 DeepSeek API key，在设置里填入后再试。"

# Serialises every mutation of the library (moves, renames, notes writes).
LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _bad_component(s):
    return (not s or "/" in s or "\\" in s or ".." in s or s.startswith(".")
            or any(ord(c) < 32 or ord(c) == 127 for c in s))


def valid_id(pid):
    return isinstance(pid, str) and len(pid) <= 200 and not _bad_component(pid)


def check_id(pid):
    if not valid_id(pid):
        raise ApiError(400, "论文 id 不合法")
    return pid


def clean_category(name, what="分类名"):
    if not isinstance(name, str):
        raise ApiError(400, f"{what}必须是字符串")
    name = name.strip()
    if not name:
        raise ApiError(400, f"{what}不能为空")
    if len(name) > 64 or _bad_component(name):
        raise ApiError(400, f"{what}「{name}」不合法：不能包含 / 或 ..，也不能以 . 开头")
    return name


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def atomic_write(path, data, mode=0o644):
    """Write bytes to path via a temp file in the same dir + os.replace."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def short(s, n=24):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def approx_chars(n):
    return f"约 {round(n, -2) if n >= 1000 else n} 字"


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

def list_category_names():
    try:
        names = os.listdir(LIB)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if not n.startswith(".") and os.path.isdir(os.path.join(LIB, n)))


def read_meta(d):
    try:
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


class Paper:
    def __init__(self, pid, category, d, meta):
        self.id = pid
        self.category = category
        self.dir = d
        self.meta = meta

    @property
    def title(self):
        return str(self.meta.get("title_zh") or self.meta.get("title_en") or self.id)

    def has_notes(self):
        txt = read_text(os.path.join(self.dir, "notes.md"))
        return bool(txt and txt.strip())

    def modes(self):
        return [m for m in MODES if os.path.isfile(os.path.join(self.dir, m + ".pdf"))]

    def record(self):
        m = self.meta
        return {
            "id": self.id,
            "title_zh": str(m.get("title_zh") or ""),
            "title_en": str(m.get("title_en") or ""),
            "authors": str(m.get("authors") or ""),
            "venue": str(m.get("venue") or ""),
            "year": _int_or_none(m.get("year")),
            "pages": _int_or_none(m.get("pages")),
            "added": str(m.get("added") or ""),
            "category": self.category,
            "has_notes": self.has_notes(),
            "modes": self.modes(),
        }


def all_papers():
    """[(category, [Paper...])] for every category folder (empty ones included)."""
    out = []
    for cat in list_category_names():
        cdir = os.path.join(LIB, cat)
        papers = []
        try:
            entries = sorted(os.listdir(cdir))
        except OSError:
            entries = []
        for pid in entries:
            if pid.startswith("."):
                continue
            d = os.path.join(cdir, pid)
            if not os.path.isdir(d):
                continue
            meta = read_meta(d)
            if meta is None:
                continue
            papers.append(Paper(pid, cat, d, meta))
        out.append((cat, papers))
    return out


def scan_library():
    cats, papers = [], []
    for cat, ps in all_papers():
        cats.append({"name": cat, "count": len(ps)})
        papers.extend(p.record() for p in ps)
    papers.sort(key=lambda p: p["id"])
    papers.sort(key=lambda p: p["added"], reverse=True)
    return {"root": LIB, "categories": cats, "papers": papers}


def find_paper(pid):
    if not valid_id(pid):
        return None
    for cat in list_category_names():
        d = os.path.join(LIB, cat, pid)
        if os.path.isfile(os.path.join(d, "meta.json")):
            meta = read_meta(d)
            if meta is not None:
                return Paper(pid, cat, d, meta)
    return None


def get_paper(pid):
    check_id(pid)
    p = find_paper(pid)
    if p is None:
        raise ApiError(404, f"找不到论文 {pid}")
    return p


# ---------------------------------------------------------------------------
# operations shared by the HTTP API and the chat tools
# ---------------------------------------------------------------------------

def read_notes(p):
    path = os.path.join(p.dir, "notes.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
            mtime = os.fstat(f.fileno()).st_mtime
        return content, mtime
    except FileNotFoundError:
        return "", 0


def write_notes(p, content):
    path = os.path.join(p.dir, "notes.md")
    with LOCK:
        atomic_write(path, content.encode("utf-8"))
        return os.stat(path).st_mtime


def move_paper(pid, category):
    """Returns (old_category, new_category, moved?)."""
    category = clean_category(category)
    with LOCK:
        p = get_paper(pid)
        if p.category == category:
            return p.category, category, False
        cdir = os.path.join(LIB, category)
        if os.path.exists(cdir) and not os.path.isdir(cdir):
            raise ApiError(409, f"「{category}」已存在但不是文件夹")
        os.makedirs(cdir, exist_ok=True)
        dest = os.path.join(cdir, p.id)
        if os.path.exists(dest):
            raise ApiError(409, f"分类「{category}」里已经有 {p.id}")
        os.rename(p.dir, dest)
        return p.category, category, True


def rename_category(frm, to):
    """Returns "renamed" | "merged" | "same"."""
    frm = clean_category(frm, "原分类名")
    to = clean_category(to, "新分类名")
    with LOCK:
        src = os.path.join(LIB, frm)
        if not os.path.isdir(src):
            raise ApiError(404, f"没有分类「{frm}」")
        if frm == to:
            return "same"
        dst = os.path.join(LIB, to)
        if os.path.exists(dst) and not os.path.samefile(src, dst):
            if not os.path.isdir(dst):
                raise ApiError(409, f"「{to}」已存在但不是文件夹")
            children = [c for c in os.listdir(src) if c != ".DS_Store"]
            clash = [c for c in children if os.path.exists(os.path.join(dst, c))]
            if clash:
                raise ApiError(409, f"「{to}」里已经有同名条目：{', '.join(clash)}")
            for c in children:
                os.rename(os.path.join(src, c), os.path.join(dst, c))
            try:
                os.unlink(os.path.join(src, ".DS_Store"))
            except FileNotFoundError:
                pass
            os.rmdir(src)
            return "merged"
        os.rename(src, dst)
        return "renamed"


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(HOME, exist_ok=True)
    atomic_write(CONFIG_PATH, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), 0o600)


# ---------------------------------------------------------------------------
# text: pages, reading, search
# ---------------------------------------------------------------------------

def split_pages(text):
    marks = list(PAGE_RE.finditer(text))
    if not marks:
        return [(1, text.strip("\n"))]
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append((int(m.group(1)), text[m.end():end].strip("\n")))
    return pages


def make_snippet(text, s, e, width=120):
    ctx = max(10, (width - (e - s)) // 2)
    a, b = max(0, s - ctx), min(len(text), e + ctx)
    snip = PAGE_INLINE_RE.sub(" ", text[a:b])
    snip = re.sub(r"\s+", " ", snip).strip()
    return ("…" if a > 0 else "") + snip + ("…" if b < len(text) else "")


META_FIELDS = ("title_zh", "title_en", "authors", "keywords", "venue", "year", "abstract_zh", "id")


def _meta_value(p, field):
    if field == "id":
        return p.id
    v = p.meta.get(field)
    if v is None:
        return ""
    if isinstance(v, list):
        return "、".join(str(x) for x in v)
    return str(v)


def _search(papers, terms):
    pats = [re.compile(re.escape(t), re.I) for t in terms]
    meta_hits, note_hits, text_hits = [], [], []
    for p in papers:
        base = {"id": p.id, "title_zh": str(p.meta.get("title_zh") or ""), "category": p.category}
        # meta
        hit = None
        for field in META_FIELDS:
            val = _meta_value(p, field)
            for pat in pats:
                m = pat.search(val) if val else None
                if m:
                    snip = val if len(val) <= 120 else make_snippet(val, m.start(), m.end())
                    if field not in ("title_zh", "title_en"):
                        snip = f"{field}: {snip}"
                    hit = dict(base, where="标题", snippet=snip)
                    break
            if hit:
                break
        if hit:
            meta_hits.append(hit)
        # notes
        notes = read_text(os.path.join(p.dir, "notes.md")) or ""
        for pat in pats:
            m = pat.search(notes)
            if m:
                note_hits.append(dict(base, where="笔记", snippet=make_snippet(notes, m.start(), m.end())))
                break
        # body text: at most 2 hits (distinct pages) per paper
        per, seen = [], set()
        for version in ("zh", "original"):
            text = read_text(os.path.join(p.dir, "text", version + ".txt"))
            if not text:
                continue
            marks = [(m.start(), int(m.group(1))) for m in PAGE_RE.finditer(text)]
            starts = [s for s, _ in marks]
            for pat in pats:
                for m in pat.finditer(text):
                    i = bisect.bisect_right(starts, m.start()) - 1
                    page = marks[i][1] if i >= 0 else None
                    if (version, page) in seen:
                        continue
                    seen.add((version, page))
                    h = dict(base, where="正文")
                    if page is not None:
                        h["page"] = page
                    h["version"] = version
                    h["snippet"] = make_snippet(text, m.start(), m.end())
                    per.append(h)
                    if len(per) >= 2:
                        break
                if len(per) >= 2:
                    break
            if len(per) >= 2:
                break
        if per:
            text_hits.append(per)
    ordered_text = [lst[i] for i in range(2) for lst in text_hits if i < len(lst)]
    return meta_hits + note_hits + ordered_text


def search_library(query, limit=10):
    q = str(query or "").strip()
    if not q:
        raise ApiError(400, "query 不能为空")
    try:
        limit = max(1, min(50, int(limit or 10)))
    except (TypeError, ValueError):
        limit = 10
    papers = [p for _, ps in all_papers() for p in ps]
    hits = _search(papers, [q])
    if not hits:
        terms = [t for t in re.split(r"\s+", q) if t]
        if len(terms) > 1:
            hits = _search(papers, terms)
    return hits[:limit]


def resolve_text_version(p, version):
    """Returns (version actually used, fallback note or '')."""
    version = version if version in ("zh", "original") else "zh"
    if os.path.isfile(os.path.join(p.dir, "text", version + ".txt")):
        return version, ""
    other = "original" if version == "zh" else "zh"
    if os.path.isfile(os.path.join(p.dir, "text", other + ".txt")):
        return other, f"（这篇没有{TEXT_LABEL[version]}文本，下面是{TEXT_LABEL[other]}。）"
    raise ApiError(404, f"《{p.title}》没有提取过的文本")


def read_paper(pid, version="zh", start_page=1, end_page=None):
    p = get_paper(pid)
    version, note = resolve_text_version(p, version)
    text = read_text(os.path.join(p.dir, "text", version + ".txt")) or ""
    pages = split_pages(text)
    total = pages[-1][0] if pages else 0
    try:
        start = max(1, int(start_page or 1))
        end = int(end_page) if end_page not in (None, "") else None
    except (TypeError, ValueError):
        raise ApiError(400, "start_page / end_page 必须是整数")
    if start > total:
        raise ApiError(400, f"{TEXT_LABEL[version]}一共只有 {total} 页")
    sel = [(n, body) for n, body in pages if n >= start and (end is None or n <= end)]
    if not sel:
        raise ApiError(400, f"第 {start}–{end} 页没有内容（{TEXT_LABEL[version]}共 {total} 页）")
    out, used, last, cut_mid = [], 0, None, False
    for n, body in sel:
        block = f"=== 第 {n} 页 ===\n{body}\n\n"
        if used + len(block) > READ_CAP:
            if not out:
                out.append(block[:READ_CAP])
                used = READ_CAP
                last = n
                cut_mid = True
            break
        out.append(block)
        used += len(block)
        last = n
    body_text = "".join(out).rstrip("\n")
    first = sel[0][0]
    header = f"《{p.title}》{TEXT_LABEL[version]}，第 {first}–{last} 页（{TEXT_LABEL[version]}共 {total} 页）"
    if note:
        header += "\n" + note
    tail = ""
    remaining_in_range = any(n > last for n, _ in sel)
    remaining_after = last < total and end is None
    if cut_mid:
        nxt = f"继续读请从第 {last + 1} 页开始" if last < total else "这是最后一页"
        tail = f"\n\n（已截断：第 {last} 页太长，只给出前 {READ_CAP} 字；{nxt}）"
    elif remaining_in_range or remaining_after:
        tail = f"\n\n（已截断：到第 {last} 页为止，继续读请从第 {last + 1} 页开始）"
    return {
        "text": header + "\n\n" + body_text + tail,
        "chars": len(body_text),
        "version": version,
        "fallback": bool(note),
        "last": last,
        "truncated": bool(tail),
    }


# ---------------------------------------------------------------------------
# DeepSeek harness
# ---------------------------------------------------------------------------

def _tool(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _tool("list_library", "列出论文库里的全部分类和论文（id、中英文标题、年份、分类、是否有笔记）。"),
    _tool("search_library",
          "在论文库中做不区分大小写的子串检索，范围包括元数据（标题、作者、关键词、摘要）、笔记和正文。"
          "返回命中列表，每条含 id、title_zh、category、where（标题/笔记/正文）、正文命中的 page 和 version，以及 snippet。",
          {"query": {"type": "string", "description": "检索词，中英文均可，越短越容易命中"},
           "limit": {"type": "integer", "description": "最多返回几条，默认 10"}},
          ["query"]),
    _tool("read_paper",
          "读取一篇论文的提取文本，带 `=== 第 N 页 ===` 页标记。单次最多约 24000 字，超出会截断并提示下次从哪一页开始。",
          {"paper_id": {"type": "string"},
           "version": {"type": "string", "enum": ["zh", "original"], "description": "zh=中文译本（默认），original=原文"},
           "start_page": {"type": "integer", "description": "起始页，默认 1"},
           "end_page": {"type": "integer", "description": "结束页（含）；不填则一直读到字数上限"}},
          ["paper_id"]),
    _tool("read_notes", "读取一篇论文的笔记（Markdown）。",
          {"paper_id": {"type": "string"}}, ["paper_id"]),
    _tool("write_notes", "写一篇论文的笔记。默认 append（追加到末尾）；只有用户明确要求替换时才用 replace。",
          {"paper_id": {"type": "string"},
           "content": {"type": "string", "description": "Markdown 内容"},
           "mode": {"type": "string", "enum": ["append", "replace"], "description": "默认 append"}},
          ["paper_id", "content"]),
    _tool("move_paper", "把论文移动到某个分类（分类不存在会自动创建）。可逆操作。",
          {"paper_id": {"type": "string"},
           "category": {"type": "string", "description": "目标分类名，简短中文主题名"}},
          ["paper_id", "category"]),
    _tool("rename_category", "重命名分类；如果新名字已存在，就把论文合并过去。可逆操作。",
          {"from": {"type": "string"}, "to": {"type": "string"}}, ["from", "to"]),
]


def display_lib():
    home = os.path.expanduser("~")
    return "~" + LIB[len(home):] if LIB.startswith(home + os.sep) else LIB


def build_system_prompt(scope, paper=None, mode=None, page=None):
    lines = [
        f"你是 PaperHelper 的助手，服务于用户自己的本地论文库（{display_lib()}，按分类文件夹存放；"
        "每篇论文可能有中文译本、中英对照版、原文、提取的文本和笔记）。",
        "",
        "规则：",
        "- 用简洁的中文 Markdown 回答。",
        "- 回答要以工具结果为依据；引用论文内容时注明页码，格式为“第 N 页”，并说明是中文版还是原文的页码。",
        "- 绝不编造论文、作者或内容；库里没有就直说没有。",
        "- 移动论文、重命名分类都是可逆操作：用户要求时直接调用工具去做，做完说清楚改了什么（从哪到哪）。",
        "- 写笔记时默认追加（mode=append），除非用户明确要求替换；保留用户已有的内容。",
        "- 分类名用简短的中文主题名（2–10 个字），先看已有分类再决定是否新建。",
        "",
    ]
    if scope == "paper" and paper is not None:
        m = paper.meta
        avail = [TEXT_LABEL[v] for v in ("zh", "original")
                 if os.path.isfile(os.path.join(paper.dir, "text", v + ".txt"))]
        lines += [
            "当前范围：用户正在看下面这篇论文，说“这篇”“这一页”时就是指它。",
            f"- id：{paper.id}",
            f"- 标题：《{m.get('title_zh') or ''}》（{m.get('title_en') or ''}）",
            f"- 作者：{m.get('authors') or ''}；{m.get('venue') or ''} {m.get('year') or ''}",
            f"- 分类：{paper.category}",
            f"- 原文页数：{m.get('pages') or '未知'}",
            f"- 可读文本：{'、'.join(avail) if avail else '无'}",
        ]
        if mode:
            lines.append(f"- 当前查看：{MODE_LABEL.get(mode, mode)}" + (f"，第 {page} 页" if page else ""))
        if m.get("abstract_zh"):
            lines.append(f"- 摘要：{m.get('abstract_zh')}")
        lines += [
            "",
            "read_paper 的页码按所选版本（zh / original）计；中英对照版没有单独文本，它的页码和中文版、原文都对不上，"
            "需要时按内容去中文版里找。用户问的不限于这篇时，也可以用 list_library / search_library 查整个库。",
        ]
    else:
        lines.append("当前范围：整个论文库。list_library 和 search_library 覆盖库里的全部论文；需要细节时用 read_paper 读正文。")
    return "\n".join(lines)


class ClientGone(Exception):
    """The browser closed the event stream."""


class ToolRunner:
    def __init__(self, scope_paper_id=None):
        self.scope_paper_id = scope_paper_id

    def _title(self, pid):
        p = find_paper(pid) if valid_id(pid) else None
        return short(p.title if p else str(pid))

    def _name(self, pid):
        return "论文" if pid and pid == self.scope_paper_id else f"《{self._title(pid)}》"

    def label(self, name, a):
        try:
            if name == "list_library":
                return "查看论文库"
            if name == "search_library":
                return f"检索论文库：{a.get('query', '')}"
            if name == "read_paper":
                pid = a.get("paper_id")
                version = a.get("version") if a.get("version") in ("zh", "original") else "zh"
                p = find_paper(pid) if valid_id(pid) else None
                if p is not None:
                    try:
                        version, _ = resolve_text_version(p, version)
                    except ApiError:
                        pass
                s = _int_or_none(a.get("start_page")) or 1
                e = _int_or_none(a.get("end_page"))
                rng = f"第 {s} 页" if e == s else (f"第 {s}–{e} 页" if e else f"第 {s} 页起")
                name = self._name(pid)
                return f"读取{name}{' · ' if name == '论文' else '· '}{TEXT_LABEL[version]} {rng}"
            if name == "read_notes":
                pid = a.get("paper_id")
                return "读取笔记" if pid == self.scope_paper_id else f"读取笔记《{self._title(pid)}》"
            if name == "write_notes":
                verb = "改写" if a.get("mode") == "replace" else "追加"
                return f"{verb}笔记《{self._title(a.get('paper_id'))}》"
            if name == "move_paper":
                return f"移动《{self._title(a.get('paper_id'))}》→ {a.get('category', '')}"
            if name == "rename_category":
                return f"重命名分类 {a.get('from', '')} → {a.get('to', '')}"
        except Exception:
            pass
        return f"调用工具 {name}"

    def run(self, name, a):
        """Returns (content for the model, Chinese summary, effects dict)."""
        effects = {}
        try:
            fn = getattr(self, "t_" + name, None)
            if fn is None:
                raise ApiError(400, f"没有这个工具：{name}")
            content, summary = fn(a, effects)
            return content, summary, effects
        except ApiError as e:
            return json.dumps({"error": e.message}, ensure_ascii=False), "出错：" + short(e.message, 40), {}
        except Exception as e:  # never let a tool bug kill the stream
            traceback.print_exc()
            return json.dumps({"error": f"工具执行失败：{e}"}, ensure_ascii=False), "出错", {}

    @staticmethod
    def _dump(obj):
        return json.dumps(obj, ensure_ascii=False)

    def t_list_library(self, a, fx):
        lib = scan_library()
        papers = [{k: p[k] for k in ("id", "title_zh", "title_en", "year", "category", "has_notes")}
                  for p in lib["papers"]]
        return (self._dump({"root": lib["root"], "categories": lib["categories"], "papers": papers}),
                f"共 {len(papers)} 篇 · {len(lib['categories'])} 个分类")

    def t_search_library(self, a, fx):
        hits = search_library(a.get("query"), a.get("limit", 10))
        out = {"query": a.get("query"), "hits": hits}
        if not hits:
            out["note"] = "没有匹配。可以换个更短的关键词，或中英文都试试。"
        n = len({h["id"] for h in hits})
        return self._dump(out), (f"找到 {n} 篇" if n else "没有找到")

    def t_read_paper(self, a, fx):
        r = read_paper(a.get("paper_id"), a.get("version") or "zh", a.get("start_page") or 1, a.get("end_page"))
        summary = approx_chars(r["chars"])
        if r["fallback"]:
            summary += f"（读的是{TEXT_LABEL[r['version']]}）"
        if r["truncated"]:
            summary += f"，到第 {r['last']} 页"
        return r["text"], summary

    def t_read_notes(self, a, fx):
        p = get_paper(a.get("paper_id"))
        content, _ = read_notes(p)
        if not content.strip():
            return f"《{p.title}》还没有笔记（notes.md 为空）。", "笔记为空"
        return f"《{p.title}》的笔记：\n\n{content}", approx_chars(len(content))

    def t_write_notes(self, a, fx):
        p = get_paper(a.get("paper_id"))
        content = a.get("content")
        if not isinstance(content, str):
            raise ApiError(400, "content 必须是字符串")
        mode = a.get("mode") or "append"
        if mode not in ("append", "replace"):
            raise ApiError(400, "mode 只能是 append 或 replace")
        with LOCK:
            old, _ = read_notes(p)
            if mode == "append" and old.strip():
                sep = "" if old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
                new = old + sep + content
            else:
                new = content
            if new and not new.endswith("\n"):
                new += "\n"
            write_notes(p, new)
        fx["notes_changed"] = p.id
        return (self._dump({"ok": True, "paper_id": p.id, "mode": mode, "length": len(new)}),
                "已追加" if mode == "append" else "已改写")

    def t_move_paper(self, a, fx):
        old, new, moved = move_paper(a.get("paper_id"), a.get("category"))
        if moved:
            fx["library_changed"] = True
        return (self._dump({"ok": True, "paper_id": a.get("paper_id"), "from": old, "category": new, "moved": moved}),
                "已移动" if moved else "本来就在这个分类")

    def t_rename_category(self, a, fx):
        r = rename_category(a.get("from"), a.get("to"))
        if r != "same":
            fx["library_changed"] = True
        return (self._dump({"ok": True, "result": r}),
                {"renamed": "已重命名", "merged": "已合并", "same": "名字没变"}[r])


def _opener_for(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _http_error_message(e, key):
    code = e.code
    api_msg = ""
    try:
        raw = e.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    try:
        j = json.loads(raw)
        err = j.get("error") if isinstance(j, dict) else None
        if isinstance(err, dict):
            api_msg = str(err.get("message") or "")
        elif err:
            api_msg = str(err)
        elif isinstance(j, dict) and j.get("message"):
            api_msg = str(j["message"])
    except ValueError:
        if raw and "<" not in raw[:50]:
            api_msg = raw.strip()
    if key and key in api_msg:
        api_msg = api_msg.replace(key, "***")
    hints = {
        400: "请求格式有误",
        401: "API key 无效，请在设置里检查",
        402: "账户余额不足",
        403: "没有权限",
        404: "接口地址不对",
        422: "请求参数有误",
        429: "请求太频繁或额度已用完，稍后再试",
        500: "DeepSeek 服务器出错，稍后再试",
        502: "DeepSeek 网关出错，稍后再试",
        503: "DeepSeek 服务繁忙，稍后再试",
    }
    hint = hints.get(code) or ("DeepSeek 服务出错，稍后再试" if code >= 500 else "请求被拒绝")
    msg = f"DeepSeek 请求失败（HTTP {code}）：{hint}。"
    if api_msg:
        msg += f"（{short(api_msg, 300)}）"
    return msg


class UpstreamError(Exception):
    pass


def stream_completion(key, body, emit):
    """One streamed chat-completions call. Content deltas go straight to emit().

    Returns (content, reasoning, tool_calls, finish_reason). Raises UpstreamError(msg)."""
    url = DEEPSEEK_BASE + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Authorization": "Bearer " + key, "User-Agent": "PaperHelper/" + VERSION})
    try:
        resp = _opener_for(url).open(req, timeout=DEEPSEEK_TIMEOUT)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"chat: DeepSeek HTTP {e.code}\n")
        raise UpstreamError(_http_error_message(e, key))
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise UpstreamError("DeepSeek 响应超时，稍后再试。")
        raise UpstreamError(f"连不上 DeepSeek（{e.reason}），检查网络后再试。")
    except (socket.timeout, TimeoutError):
        raise UpstreamError("DeepSeek 响应超时，稍后再试。")
    except (OSError, http.client.HTTPException) as e:
        raise UpstreamError(f"连不上 DeepSeek（{e}），检查网络后再试。")

    content, reasoning, calls, finish, got_done = [], [], {}, None, False
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue  # blank separators, ": keep-alive" comments, event: lines
                payload = line[5:].strip()
                if payload == "[DONE]":
                    got_done = True
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    err = chunk["error"]
                    m = err.get("message") if isinstance(err, dict) else str(err)
                    raise UpstreamError(f"DeepSeek 返回错误：{short(str(m), 300)}")
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        content.append(text)
                        emit("delta", {"text": text})
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", len(calls))
                        slot = calls.setdefault(idx, {"id": "", "type": "function",
                                                      "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
    except (socket.timeout, TimeoutError):
        raise UpstreamError("DeepSeek 响应超时（流中断），稍后再试。")
    except (OSError, http.client.HTTPException) as e:
        raise UpstreamError(f"和 DeepSeek 的连接中断了（{e.__class__.__name__}），稍后再试。")
    if not got_done and finish is None:
        raise UpstreamError("和 DeepSeek 的连接中途断开了，回答可能不完整，稍后再试。")
    tool_calls = [calls[i] for i in sorted(calls)]
    return "".join(content), "".join(reasoning), tool_calls, finish


def run_chat(emit, scope, paper, mode, page, messages):
    cfg = load_config()
    key = str(cfg.get("deepseek_api_key") or "").strip()
    if not key:
        emit("error", {"message": NO_KEY_MSG})
        return
    model = str(cfg.get("model") or "").strip() or DEFAULT_MODEL
    runner = ToolRunner(paper.id if paper else None)
    msgs = [{"role": "system", "content": build_system_prompt(scope, paper, mode, page)}] + messages
    step_no = 0
    try:
        for rnd in range(MAX_ROUNDS):
            body = {"model": model, "messages": msgs, "stream": True, "tools": TOOLS}
            last_round = rnd == MAX_ROUNDS - 1
            if last_round:
                body["tool_choice"] = "none"
            content, reasoning, calls, finish = stream_completion(key, body, emit)
            if not calls:
                if finish == "length":
                    emit("delta", {"text": "\n\n（回答达到长度上限，被截断了。）"})
                elif finish == "content_filter":
                    emit("error", {"message": "DeepSeek 因内容审核中止了回答。"})
                elif finish == "insufficient_system_resource":
                    emit("error", {"message": "DeepSeek 资源不足，回答被中断，稍后再试。"})
                return
            if last_round:
                break
            for i, c in enumerate(calls):
                if not c["id"]:
                    c["id"] = f"call_{rnd}_{i}"
            assistant = {"role": "assistant", "content": content, "tool_calls": calls}
            if reasoning:
                assistant["reasoning_content"] = reasoning
            msgs.append(assistant)
            lib_changed, notes_changed = False, []
            for c in calls:
                step_no += 1
                sid = f"step-{step_no}"
                name = c["function"]["name"]
                raw_args = c["function"]["arguments"] or "{}"
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError
                except ValueError:
                    args = None
                emit("step", {"id": sid, "label": runner.label(name, args or {})})
                if args is None:
                    out, summary, fx = (json.dumps({"error": "参数不是合法的 JSON 对象"}, ensure_ascii=False),
                                        "出错：参数格式不对", {})
                else:
                    out, summary, fx = runner.run(name, args)
                emit("step_done", {"id": sid, "summary": summary})
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                if fx.get("library_changed"):
                    lib_changed = True
                if fx.get("notes_changed") and fx["notes_changed"] not in notes_changed:
                    notes_changed.append(fx["notes_changed"])
            if lib_changed:
                emit("library_changed", {})
            for pid in notes_changed:
                emit("notes_changed", {"paper_id": pid})
        emit("error", {"message": f"工具调用轮数达到上限（{MAX_ROUNDS} 轮），先停在这里。可以把问题拆小一点再问。"})
    except UpstreamError as e:
        emit("error", {"message": str(e)})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".wasm": "application/wasm",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".xml": "application/xml", ".ftl": "text/plain; charset=utf-8",
    ".bcmap": "application/octet-stream", ".pfb": "application/octet-stream",
}

PAPER_ROUTE = re.compile(r"^/api/papers/([^/]+)/(notes|move)$")
FILE_ROUTE = re.compile(r"^/files/([^/]+)/([^/]+)\.pdf$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PaperHelper/" + VERSION
    sys_version = ""

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args))

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("HEAD")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_PATCH(self):
        self.dispatch("PATCH")

    # -- plumbing ----------------------------------------------------------

    def dispatch(self, method):
        self._sent = False
        try:
            raw = self.read_body()
            self.check_origin(method)
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/"):
                self.route_api(method, path, raw)
            elif path.startswith("/files/"):
                self.route_files(method, path)
            else:
                self.route_static(method, path)
        except ApiError as e:
            if self._sent:
                self.close_connection = True
            else:
                self.send_json(e.status, {"error": e.message})
        except (ClientGone, BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            if not self._sent:
                try:
                    self.send_json(500, {"error": "服务器内部错误，详情见 logs/server.log"})
                except OSError:
                    pass

    def check_origin(self, method):
        """Local-only hardening: refuse DNS-rebinding hosts and cross-site writes."""
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed:
            raise ApiError(403, "只接受来自 127.0.0.1 的请求")
        origin = (self.headers.get("Origin") or "").strip().lower()
        if method not in ("GET", "HEAD") and origin and origin not in {"http://" + h for h in allowed}:
            raise ApiError(403, "拒绝跨站请求")

    def read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            self.close_connection = True
            raise ApiError(411, "请求需要 Content-Length")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            raise ApiError(400, "Content-Length 不合法")
        if n > MAX_BODY:
            self.close_connection = True
            raise ApiError(413, "请求体太大")
        return self.rfile.read(n) if n > 0 else b""

    @staticmethod
    def json_body(raw):
        if not raw:
            raise ApiError(400, "请求体不能为空")
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "请求体不是合法的 JSON")
        if not isinstance(obj, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return obj

    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._sent = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_file(self, path, ctype):
        try:
            f = open(path, "rb")
        except OSError:
            raise ApiError(404, "找不到文件")
        with f:
            st = os.fstat(f.fileno())
            size = st.st_size
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                m = RANGE_RE.match(rng.strip())
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                    else:
                        start, end = max(0, size - int(m.group(2))), size - 1
                    if start >= size or start > end:
                        self._sent = True
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
            length = max(0, end - start + 1)
            self._sent = True
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Last-Modified", self.date_time_string(int(st.st_mtime)))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    @staticmethod
    def allow(method, *allowed):
        if method not in allowed:
            raise ApiError(405, "不支持这个请求方法")

    # -- routes ------------------------------------------------------------

    def route_api(self, method, path, raw):
        if path == "/api/ping":
            self.allow(method, "GET", "HEAD")
            return self.send_json(200, {"ok": True, "version": VERSION})
        if path == "/api/library":
            self.allow(method, "GET", "HEAD")
            return self.send_json(200, scan_library())
        if path == "/api/config":
            self.allow(method, "GET", "HEAD", "PUT")
            if method == "PUT":
                return self.put_config(self.json_body(raw))
            cfg = load_config()
            return self.send_json(200, {"has_key": bool(str(cfg.get("deepseek_api_key") or "").strip()),
                                        "model": str(cfg.get("model") or "").strip() or DEFAULT_MODEL})
        if path == "/api/categories/rename":
            self.allow(method, "POST")
            b = self.json_body(raw)
            rename_category(b.get("from"), b.get("to"))
            return self.send_json(200, {"ok": True})
        if path == "/api/chat":
            self.allow(method, "POST")
            return self.chat(self.json_body(raw))
        m = PAPER_ROUTE.match(path)
        if m:
            pid = urllib.parse.unquote(m.group(1))
            if m.group(2) == "notes":
                self.allow(method, "GET", "HEAD", "PUT")
                p = get_paper(pid)
                if method == "PUT":
                    b = self.json_body(raw)
                    content = b.get("content")
                    if not isinstance(content, str):
                        raise ApiError(400, "content 必须是字符串")
                    mtime = write_notes(p, content)
                    return self.send_json(200, {"ok": True, "mtime": mtime})
                content, mtime = read_notes(p)
                return self.send_json(200, {"content": content, "mtime": mtime})
            self.allow(method, "POST")
            b = self.json_body(raw)
            _, new, _ = move_paper(check_id(pid), b.get("category"))
            return self.send_json(200, {"ok": True, "category": new})
        raise ApiError(404, "没有这个接口")

    def put_config(self, b):
        cfg = load_config()
        if "deepseek_api_key" in b:
            k = b["deepseek_api_key"]
            if k is not None and not isinstance(k, str):
                raise ApiError(400, "deepseek_api_key 必须是字符串")
            k = (k or "").strip()
            if k:
                cfg["deepseek_api_key"] = k
            else:
                cfg.pop("deepseek_api_key", None)
        if "model" in b:
            mdl = b["model"]
            if mdl is not None and not isinstance(mdl, str):
                raise ApiError(400, "model 必须是字符串")
            mdl = (mdl or "").strip()
            if mdl:
                cfg["model"] = mdl
            else:
                cfg.pop("model", None)
        save_config(cfg)
        return self.send_json(200, {"ok": True})

    def route_files(self, method, path):
        self.allow(method, "GET", "HEAD")
        m = FILE_ROUTE.match(path)
        if not m:
            raise ApiError(404, "找不到文件")
        pid, mode = urllib.parse.unquote(m.group(1)), m.group(2)
        if mode not in MODES:
            raise ApiError(404, f"没有 {mode} 这种版本")
        p = get_paper(pid)
        fp = os.path.join(p.dir, mode + ".pdf")
        if not os.path.isfile(fp):
            raise ApiError(404, f"这篇论文没有{MODE_LABEL[mode]} PDF")
        self.send_file(fp, "application/pdf")

    def route_static(self, method, path):
        self.allow(method, "GET", "HEAD")
        rel = urllib.parse.unquote(path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        parts = rel.split("/")
        if any(not s or s.startswith(".") or "\\" in s or "\x00" in s for s in parts):
            raise ApiError(404, "找不到文件")
        root = os.path.realpath(WEB_DIR)
        full = os.path.realpath(os.path.join(root, *parts))
        if not full.startswith(root + os.sep):
            raise ApiError(404, "找不到文件")
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            raise ApiError(404, "找不到文件")
        ext = os.path.splitext(full)[1].lower()
        self.send_file(full, MIME.get(ext, "application/octet-stream"))

    # -- chat --------------------------------------------------------------

    def chat(self, b):
        scope = b.get("scope") or "library"
        if scope not in ("library", "paper"):
            raise ApiError(400, "scope 只能是 library 或 paper")
        raw_msgs = b.get("messages")
        if not isinstance(raw_msgs, list) or not raw_msgs:
            raise ApiError(400, "messages 必须是非空数组")
        messages = []
        for m in raw_msgs:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                    or not isinstance(m.get("content"), str):
                raise ApiError(400, "messages 的每一项都要是 {role: user|assistant, content: 字符串}")
            if m["role"] == "assistant" and not m["content"].strip():
                continue
            messages.append({"role": m["role"], "content": m["content"]})
        if not messages:
            raise ApiError(400, "messages 不能为空")
        paper = mode = page = None
        if scope == "paper":
            if not b.get("paper_id"):
                raise ApiError(400, "scope 为 paper 时需要 paper_id")
            paper = get_paper(b.get("paper_id"))
            mode = b.get("mode") if b.get("mode") in MODES else "zh"
            page = _int_or_none(b.get("page"))

        self._sent = True
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event, data):
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                raise ClientGone()

        try:
            run_chat(emit, scope, paper, mode, page, messages)
        except ClientGone:
            return
        except Exception:
            traceback.print_exc()
            try:
                emit("error", {"message": "服务器内部错误，详情见 logs/server.log"})
            except ClientGone:
                return
        try:
            emit("done", {})
        except ClientGone:
            pass


class PaperServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ClientGone)):
            return
        super().handle_error(request, client_address)


def main():
    os.makedirs(LIB, exist_ok=True)
    try:
        httpd = PaperServer((HOST, PORT), Handler)
    except OSError as e:
        sys.stderr.write(f"PaperHelper: 无法监听 {HOST}:{PORT}：{e}\n")
        sys.exit(1)

    def _term(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _term)
    sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} PaperHelper {VERSION} 运行于 "
                     f"http://{HOST}:{PORT}/ （数据目录 {HOME}）\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()
        sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} PaperHelper 已停止\n")


if __name__ == "__main__":
    main()
