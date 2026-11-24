# <作者-年份-主题> 中文版 — 编译与导出说明

## 文件结构
```
<output_folder>/
├── main.tex            # 主文档（每段同时写原文与译文 + LaTeX 源码）
├── main_bilingual.tex  # 中英对照版入口（固定内容，不含正文，\input main.tex）
├── main.pdf            # 编译产物：中文译文，保真版，用于阅读 / 存档（公式、图、交叉引用全对）
├── main_bilingual.pdf  # 编译产物：中英对照版，每段原文在上、中文在下
├── main.docx           # 导出产物：可编辑版，用于往论文里粘 / 二次编辑
├── images/             # 从原 PDF 提取的图（fig1.png ~ figN.png）
├── raw.txt             # 原文（外文）逐页文本，仅作译者校对用，不参与编译
├── source.txt          # 原文逐页文本（保留词间空格、按段分块），对照版原文一侧的来源
└── README.md           # 本说明
```

> **产物的定位**：`main.pdf` 是**保真版**——公式编号、图表、交叉引用（"如式(8)""图5"）全部正确，用来阅读和存档。`main_bilingual.pdf` 是**中英对照版**——与 `main.pdf` 出自同一份 `main.tex`，每个段落、标题、列表项、脚注、表题都是原文在上、中文在下，公式、图、表格内容和参考文献只出现一次，适合逐段核对译文。`main.docx` 是**可编辑版**——适合往毕业论文文献综述里粘段落、继续改；但 pandoc 转换时 **LaTeX 自动编号与交叉引用会丢失**（正文里的"见式(8)"可能变空或问号，参考文献编号链接失效），需在 Word 里手动补。按需取用。

## 本地一键导出（推荐）
本文件夹已随附 `main.pdf`、`main_bilingual.pdf` 和 `main.docx`。若需重新生成：
```bash
# 出 PDF（需 XeLaTeX：MikTeX / TeX Live / MacTeX）；同时生成 main.pdf 与 main_bilingual.pdf
python <skill>/scripts/compile_pdf.py "<output_folder>"
# 出 Word（需 pandoc）
python <skill>/scripts/export_docx.py "<output_folder>"
```
其中 `<skill>` 为 skill 安装目录（如 `~/.claude/skills/pdf-to-chinese-latex`）。

手动编译亦可（TeX Live 2020+ 或 MikTeX 22+）：
```powershell
xelatex main.tex
xelatex main.tex   # 第二次编译以解决交叉引用
xelatex main_bilingual.tex
xelatex main_bilingual.tex   # 中英对照版
```

修改译文时只改 `main.tex`，两个版本会同时更新。

## 在 Overleaf 编译（无本地 TeX 时的兜底）
1. 打开 [Overleaf](https://www.overleaf.com)，点击 **New Project → Upload Project**。
2. 上传本文件夹同名的 `.zip` 文件。
3. 进入项目后，**Menu → Compiler → XeLaTeX**（必须 XeLaTeX，否则 ctex 中文无法渲染）。
4. **Recompile**。
5. 要看中英对照版：**Menu → Main document → main_bilingual.tex**，再 Recompile。

## 译稿说明
- 使用 `ctex` 包做中文排版，所有汉字直接以 UTF-8 写入。
- 公式编号与原文一一对应：原文式 (N) 在本译稿中对应 `\label{eq:cN}`，用 `\eqref{eq:cN}` 引用。
- 表格用 `tabularx + booktabs` 重排为中文版，数据与原表保持一致。
- 算法伪代码用 `algorithm + algpseudocode`。
- 参考文献使用手工 `thebibliography` 列出（条目保留英文，便于检索原文）。

## 已知简化（如有）
- 原文中 <第 N 页/第 M 节> 的大型横向表格因数据规模较大，本译稿以要点形式概括，未逐字复刻；如需原始数据请参阅原 PDF 对应页。
