# Jiang2023 Mistral 7B 中文版 — 编译与导出说明

原文：Albert Q. Jiang 等，*Mistral 7B*，arXiv:2310.06825v1 [cs.CL]，2023 年 10 月 10 日（CC BY 4.0）。

## 文件结构
```
Jiang2023_Mistral7B_中文版/
├── main.tex              # 主文档（每段同时写原文与译文 + LaTeX 源码）
├── main_bilingual.tex    # 中英对照版入口（固定内容，不含正文，\input main.tex）
├── images/               # 从原 PDF 裁出的图（fig1.png ~ fig6.png）与标题页 logo.png
└── README.md             # 本说明
```

编译产物在上一级目录：`../zh.pdf`（中文译文，即 main.pdf）和 `../bilingual.pdf`（中英对照，即 main_bilingual.pdf）。在本目录编译会重新生成 `main.pdf` / `main_bilingual.pdf`。

> **产物的定位**：`main.pdf` 是**保真版**——图表编号、交叉引用（“见表 2”“图 5”）、参考文献编号全部正确，用来阅读和存档。`main_bilingual.pdf` 是**中英对照版**——与 `main.pdf` 出自同一份 `main.tex`，每个段落、标题、列表项、脚注、图表标题都是原文在上、中文在下；表格数据和参考文献只出现一次，适合逐段核对译文。
>
> 本次未导出 Word 版（`main.docx`）：生成时本机未安装 pandoc。需要时安装 pandoc 后运行下方 `export_docx.py` 即可（Word 版会丢失 LaTeX 自动编号与交叉引用，PDF 才是保真版）。

## 本地一键导出（推荐）
```bash
# 出 PDF（需 XeLaTeX：MikTeX / TeX Live / MacTeX）；同时生成 main.pdf 与 main_bilingual.pdf
python <skill>/scripts/compile_pdf.py "<本文件夹>"
# 出 Word（需 pandoc）
python <skill>/scripts/export_docx.py "<本文件夹>"
```
其中 `<skill>` 为 skill 安装目录（Claude Code：`~/.claude/skills/pdf-to-chinese-latex`；Codex：`~/.codex/skills/pdf-to-chinese-latex`）。

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
- 使用 `ctex` 包做中文排版，所有汉字直接以 UTF-8 写入；专业术语首次出现时以“中文（English）”形式标注原文。
- 原文无编号公式；行内数学（$W$、$h_i$、$i \bmod W$ 等）照原文排版。
- 6 幅图均从原 PDF 按图区裁出（不含原英文图注），图注在 LaTeX 中重排：中文版为中文图注，并在“图中文字”一段给出图内英文标注的译文（图 6 含两段模型回答的全文译文）；对照版图注为原文在上、中文在下。
- 表 1–5 用 `booktabs` 重排，数值与加粗与原表一致；表 2 列数较多，按版心宽度等比缩放。表 5 中两段模型回答在中文版给出译文，对照版中英并列。
- 第 5.1 节的系统提示词方框：中文版为译文，对照版为原文 + 译文。
- 参考文献以 `thebibliography` 列出，条目保留英文，编号与原文一致。
- 第 2 节正文写“图 2 给出了 $W=3$ 时的示意”，而图 2 图注为 $W=4$，属原文不一致，已照译并加译者注。

## 已知简化
- 无。版式（单栏、图表位置）与原文不完全相同，但章节、图表编号与原文一一对应。
