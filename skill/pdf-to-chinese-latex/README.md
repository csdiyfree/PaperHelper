# pdf-to-chinese-latex

把任意语种的学术 PDF 论文 → 翻译成中文 → 产出可在 Overleaf 用 XeLaTeX 编译的 LaTeX 工程，公式编号 / 表格 / 图 / 参考文献全部和原文一一对应。

这是一个 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) **skill**，安装之后跟 Claude 说一句"把这篇 PDF 翻成中文版"它就会自动跑全流程。也可以脱离 skill 直接用其中的脚本（`scripts/extract_text.py`、`scripts/render_figures.py`、`scripts/check_tex.py`）。

---

## 解决了什么问题

把一篇 30 页的英文论文翻译成中文，自己手工做大概是这个流程：抠图、转 LaTeX、翻文字、对公式编号、对参考文献。一篇下来半天到一天。常见的"AI 一键翻译 PDF" 工具有三个硬伤：

1. **矢量图丢失** —— 复杂的网络图、路径图都是矢量绘制，普通 PDF 解析器抽不出来；
2. **公式编号崩了** —— 翻译器把公式当文字处理，原文 (3.4) 在译文里可能变成图片或乱码；
3. **交付的是只读 PDF** —— 想往自己的文献综述里粘几句就只能复制粘贴，没办法二次编辑。

本 skill 用三个针对性的设计解决这三件事：

- **caption 锚定裁图**：用 PyMuPDF 在 PDF 里搜 "Fig. N." 关键字定位图题，把图题上方那块区域以 3× 比例栅格化导出 PNG —— 不管原图是矢量还是位图都能完整保留；
- **公式编号映射**：每个 `\label{eq:cN}` 严格对应原文式 (N)，正文里用 `\eqref{}` 引用，所以交叉引用永远不会错位；
- **交付 LaTeX 源码 + 静态校验**：产物是可继续编辑的 `.tex` 工程，附带一个 Python 校验脚本检查 `\begin/\end` 配对、`\cite ↔ \bibitem` 配对、`\ref ↔ \label` 配对、行内 `$...$` 配对。

## 适用场景

- 英文 / 日文 / 印尼文 / 德文 / 法文等任意语种的**学术论文** PDF；
- 4 ~ 40 页；含公式、表格、算法伪代码、参考文献；
- 目标是把论文翻成中文版供**文献综述 / 课题报告 / 课堂讲解**使用；
- 希望产出**可继续编辑**的 LaTeX 源码，而不是只读的中文 PDF。

不适合：
- 仅需要文字 OCR / 摘要 / 一句话总结 —— 用其他更轻量的工具；
- 原文已是中文；
- 想要 Word 文档 / PPT / 长篇 PDF 的纯排版 —— 用对应的 skill（docx / pptx / pdf）。

## 安装

### 作为 Claude Code skill 使用（推荐）

```bash
# 1. 克隆这个仓库到你的 skill 目录
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git \
  ~/.claude/skills/pdf-to-chinese-latex     # macOS / Linux

# Windows PowerShell
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git `
  "$env:USERPROFILE\.claude\skills\pdf-to-chinese-latex"

# 2. 安装 Python 依赖
pip install pdfplumber pypdf PyMuPDF

# 3. 安装导出工具（可选，但推荐——本地直接出 PDF + Word）
#    出 PDF（XeLaTeX 引擎）：
#      Windows: winget install MiKTeX.MiKTeX
#      macOS  : brew install --cask mactex
#      Linux  : sudo apt install texlive-xetex texlive-lang-chinese
#    出 Word（pandoc）：
#      conda install -c conda-forge pandoc   # 或 winget install JohnMacFarlane.Pandoc
```

> 不装导出工具也能用：skill 仍会产出 LaTeX 工程和 zip，上传 Overleaf 即可编译出 PDF。装了之后才能本地一键出 `main.pdf`（保真版）和 `main.docx`（可编辑版）。

启动 Claude Code 时它会自动发现 `~/.claude/skills/` 下的 skill。

**装完先跑一次自检**确认环境通：

```bash
python ~/.claude/skills/pdf-to-chinese-latex/scripts/self_check.py
```

会临时生成一个合成 PDF 走完 extract → render → check_tex → xelatex 整条链路，打印每一步 PASS / FAIL。10 秒内出结果。`xelatex` 那一步出 SKIP 是因为没装 TeX，只想用 Overleaf 编译可以忽略；出 FAIL 就要先修依赖再用。

然后这样使唤它：

> 我有这两篇文献 `D:\path\to\paper1.pdf` 和 `D:\path\to\paper2.pdf`，请帮我翻成中文 LaTeX 版本。

Claude 会触发本 skill，先用 `AskUserQuestion` 问你两个关键选项（图表处理 / 翻译颗粒度），然后跑完整流程，最后本地导出 `main.pdf`（保真版，用于阅读/存档）和 `main.docx`（可编辑版，用于往论文里粘）。

### 单独使用脚本（不需要 Claude Code）

```bash
pip install pdfplumber pypdf PyMuPDF
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git
cd pdf-to-chinese-latex

# 抽取全文文本
python scripts/extract_text.py input.pdf --out output_dir/raw.txt

# 抽取图（自动扫描所有页，按 Fig. N. / Figure N. 锚定裁切）
python scripts/render_figures.py input.pdf --out output_dir/images --auto
# Wiley / Elsevier 类带下载水印的论文加上：--side-margin 30 --top-margin 100
# 想手动指定某几张图：--figs "1:3:Fig. 1.,2:5:Fig. 2.,3:7:Fig. 3."（可与 --auto 叠加 override）

# 从 raw.txt 直接抽出 \bibitem 骨架，省去手抄 20~40 条参考文献
python scripts/extract_bibliography.py output_dir/raw.txt --out output_dir/_bib.tex

# 翻译后做静态校验（环境配对 / cite-bibitem / ref-label / $ 配对 / 图文件真伪）
python scripts/check_tex.py output_dir/main.tex

# 可选：本地 XeLaTeX 编译（不走 Overleaf）
python scripts/compile_pdf.py output_dir
```

## 工作流（7 步）

详细解释见 [`SKILL.md`](SKILL.md)。简版：

1. **询问选项**：图表处理（抽图嵌入 / 占位 / 跳过）、翻译颗粒度（全文 / 核心章节 / 全文+术语表）；
2. **抽文本**：`pdfplumber` 按页输出到 `raw.txt`；
3. **抽图**：`PyMuPDF` 按图题关键字定位 caption 矩形，向上裁切并以 3× 比例渲染成 PNG；
4. **建工程**：`{Author}{Year}_{Topic}_中文版/main.tex` + `images/` + `README.md`，基于 `references/latex_template.tex` 的 ctex 模板；
5. **逐段翻译**：摘要 / 引言 / 模型 / 算法 / 实验 / 结论 / 参考文献全部覆盖，公式 / 表格 / 算法伪代码逐一重排；
6. **静态校验**：`scripts/check_tex.py` 检查环境配对、引用配对、行内公式配对；
7. **打包**：把 `{main.tex, images/, README.md}` 压成同名 zip，给出 Overleaf 上传步骤。
8. **（可选）本地编译**：装了 MikTeX / TeX Live 的用户可以 `python scripts/compile_pdf.py <output_folder>`，两遍 XeLaTeX 直出 PDF，省去 Overleaf round-trip。

## 输出示例

skill 跑完一篇 14 页的论文后产出大致这样的结构：

```
Tan2025_MDEVRPSTW_中文版/
├── main.tex        # 全文中译，500+ 行，含 35 条 MILP 约束公式
├── images/
│   ├── fig1.png    # MDEVRPTW 路径示例（图题锚定自动裁切）
│   ├── fig2.png
│   ├── ...
│   └── fig5.png
├── raw.txt         # 原文逐页文本，校对用
└── README.md       # Overleaf 编译说明

Tan2025_MDEVRPSTW_中文版.zip   # 同名打包，可直接上传 Overleaf
```

Overleaf 上 Compiler 选 **XeLaTeX**，Recompile 即得 14 页中文 PDF。

## 项目结构

```
pdf-to-chinese-latex/
├── SKILL.md                        # skill 主文档（被 Claude 读取的部分）
├── README.md                       # 你正在看的这份
├── LICENSE                         # MIT
├── .gitignore
├── scripts/
│   ├── extract_text.py             # pdfplumber 抽文本
│   ├── render_figures.py           # PyMuPDF caption 锚定裁图（含 --auto 全扫描 + 水印剔除）
│   ├── extract_bibliography.py     # raw.txt → \bibitem 骨架（numbered / 作者-年 两种格式）
│   ├── check_tex.py                # main.tex 静态校验（含 \includegraphics 真伪 magic-byte 检查）
│   └── compile_pdf.py              # 可选：本地 XeLaTeX 编译直出 PDF
├── references/
│   ├── latex_template.tex          # ctex 中文 LaTeX 骨架
│   ├── readme_template.md          # 每篇译稿用的 README 模板
│   └── troubleshooting.md          # 常见坑与排错
└── examples/                       # （可选）放置示例输入/输出对
```

## 依赖

| 包 | 用途 | 安装 |
|---|---|---|
| pdfplumber | 抽取 PDF 文本 | `pip install pdfplumber` |
| pypdf | 备用 PDF 处理 | `pip install pypdf` |
| PyMuPDF | 渲染图片（包含矢量图） | `pip install PyMuPDF` |
| XeLaTeX *(可选)* | 本地直接编译 PDF | Windows: `winget install MiKTeX.MiKTeX`；macOS: `brew install --cask mactex`；Linux: `apt install texlive-xetex texlive-lang-chinese` |

Python 3.8+。Overleaf 端无需任何配置，免费账号即可编译。装了 XeLaTeX 之后可以 `python scripts/compile_pdf.py <output_folder>` 直接在本地出 PDF，完全脱离 Overleaf。

## 给贡献者 / 修 skill 的人：核心主旨

这个 skill 的所有改动都遵循一个核心主旨——**所有工作的最终目的，是把通用原则内化进 skill 本身，让任何人装载之后能在低消耗下拿到高质量产出**。具体到三条测试：

1. **普适性**：修 bug 时把根因抽成规则，不要硬编码 Wiley / 某页 / 某图。规则才能复用到未见过的论文。
2. **内化到 skill**：新来的人不用读 commit history 就能受益——把知识塞进**代码默认行为**（脚本开箱即用）、**SKILL.md 流程**（带强制检查点）、**troubleshooting.md**（具体的 失败→原因→修法）。只留在某次对话或 commit message 里 ≠ 内化。
3. **低摩擦高质量**：装载到出成品的路径要短。合理默认（不让 agent 反复问用户）、显式失败（绝不静默吐废品）、验证闭环在工具里（如 `_inspection.html` 让"图截对没"30 秒就能扫完）、文档省 token（SKILL.md 只讲做什么，不讲怎么演化来的）。

提 PR 前自问：
- 这个修法能帮**每一篇**未来同类论文，还是只解决眼前这一篇？
- 落地后，新用户**不读这段对话**也能受益吗？
- 它是**减少**还是**增加**了下一个用户的摩擦？

三个都是 yes 才合并。否则改到三个都 yes 为止。完整版见 [`SKILL.md`](SKILL.md#operating-principles-read-before-extending-this-skill)。

## 致谢

本 skill 诞生于一次具体的研究生工作：把两篇英文运筹学论文翻译成中文 LaTeX 版本以用作毕业论文文献综述。流程沉淀过程详见 [实际工作流博客 / 学习日志](#)（如果你也想分享，欢迎 PR 加链接）。

## License

MIT
