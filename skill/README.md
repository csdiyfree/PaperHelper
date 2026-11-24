# skill/pdf-to-chinese-latex

Claude Code / Codex 通用的 skill：把外文论文 PDF 翻译成中文 LaTeX 译稿，同时编出**中文版**和**中英对照版**两个 PDF，最后自动归类存进 PaperHelper 并打开。

`install.sh` 会把它同步到 `~/.claude/skills/pdf-to-chinese-latex` 和 `~/.codex/skills/pdf-to-chinese-latex`（取决于装了哪个工具）。

## 来源

基于 [a19394263615-dotcom/academic-pdf-translator](https://github.com/a19394263615-dotcom/academic-pdf-translator)（MIT 许可，© the pdf-to-chinese-latex authors，许可证原文见 [`pdf-to-chinese-latex/LICENSE`](pdf-to-chinese-latex/LICENSE)）。`README.md` / `README_EN.md` 是上游原文。

## 本仓库的改动

- **中英对照版**：正文用 `\bipar{原文}{中文}` 等成对宏书写；`main.tex` 编出纯中文版，新增的固定入口 `references/main_bilingual.tex` 编出「上原文、下中文」的对照版。两个版本出自同一份源码。
- **`source.txt`**：`extract_text.py` 额外用 PyMuPDF 输出保留词间空格、按段分块的原文，作为对照版原文一侧的来源（pdfplumber 在紧排版 PDF 上会丢空格）。
- **`scripts/check_bilingual.py`**：编译后核对每一对原文 / 译文是否都正确出现在两个 PDF 里。
- **`compile_pdf.py`** 同时编译两个版本；`self_check.py` 增加对照版编译与核对。
- **Step 9**：翻译完成后用 `~/PaperHelper/bin/paperhelper` 选分类、入库、打开。
- **修复上游的几个问题**：
  - `render_figures.py`：能识别 `Figure N:` 这类带冒号的图注；图注认得不可靠时会给出醒目警告；`|bbox=` 手动裁剪参数在命令行里可以正常使用；裁出的图只含图本身、不含原图注；检查用的缩略图不再混进 `images/`。
  - `extract_bibliography.py`：改从 `source.txt` 读取，词间空格不再丢失；去掉粘在条目上的页码；不再把附录吞进最后一条。
  - `check_tex.py`：忽略注释里的内容。
  - 模板：英文里的长破折号「–」按西文字符排版；放宽浮动体参数，减少半空页，避免表格跑到章节标题前面；缓解中英混排的行溢出。
- **兼容 Codex**：提问不再依赖 Claude Code 的 `AskUserQuestion`；`SKILL.md` 增加了沙盒说明；`compile_pdf.py` 在系统字体不可用时（如 Codex 沙盒）自动改用 Fandol 字体重编。
- 没有包含上游的 `evals/`（测试用 PDF），需要请到上游仓库获取。
