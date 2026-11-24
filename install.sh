#!/bin/bash
# One-time setup on macOS: launcher app + Claude Code skill.
# Clone the repository to ~/PaperHelper first (the skill calls ~/PaperHelper/bin/paperhelper).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ "$ROOT" != "$HOME/PaperHelper" ]; then
  echo "提示：翻译 skill 默认调用 ~/PaperHelper/bin/paperhelper，建议把仓库放在 ~/PaperHelper。" >&2
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "需要 Python 3.9 或更高版本" >&2; exit 1; }
python3 -c 'import fitz' 2>/dev/null || echo "提示：入库时提取文字需要 PyMuPDF：python3 -m pip install PyMuPDF" >&2

chmod +x "$ROOT/bin/paperhelper" "$ROOT/macos/build-app.sh"
mkdir -p "$ROOT/library" "$ROOT/work"

# Translation skill for Claude Code and/or Codex (keeps any local .git / .venv / evals)
TARGETS=()
{ [ -d "$HOME/.claude" ] || command -v claude >/dev/null 2>&1; } && TARGETS+=("$HOME/.claude/skills")
{ [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; } && TARGETS+=("$HOME/.codex/skills")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS+=("$HOME/.claude/skills")   # neither found yet: default to Claude Code
for dir in "${TARGETS[@]}"; do
  mkdir -p "$dir/pdf-to-chinese-latex"
  rsync -a --delete --exclude .git --exclude .venv --exclude evals \
    "$ROOT/skill/pdf-to-chinese-latex/" "$dir/pdf-to-chinese-latex/"
  echo "已安装 skill 到 $dir/pdf-to-chinese-latex"
done

"$ROOT/macos/build-app.sh"
echo "完成。打开「PaperHelper」（~/Applications），或运行：$ROOT/bin/paperhelper open"
