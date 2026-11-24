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

# Claude Code skill (keeps any local .git / .venv / evals in the installed copy)
SKILL="$HOME/.claude/skills/pdf-to-chinese-latex"
mkdir -p "$SKILL"
rsync -a --delete --exclude .git --exclude .venv --exclude evals \
  "$ROOT/skill/pdf-to-chinese-latex/" "$SKILL/"
echo "已安装 skill 到 $SKILL"

"$ROOT/macos/build-app.sh"
echo "完成。打开「PaperHelper」（~/Applications），或运行：$ROOT/bin/paperhelper open"
