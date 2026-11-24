# Troubleshooting / FAQ

Read this only when the main workflow runs into a specific snag. Each entry is a self-contained problem → cause → fix.

## 抽取 / Extraction

### 文本里出现大量 "Downloaded from / Online Library" 噪声行
**原因：** Wiley / Elsevier 等出版社会在每页 PDF 上嵌入一个版权下载头，pdfplumber 会把它当成正文文本提取出来。
**处理：** 翻译时跳过这些行，不要把它们也译成中文。可以在 `raw.txt` 里写一个简单的过滤脚本，或者翻译时直接忽略。这种页眉/页脚是 PDF 标准做法，不影响公式与图。

### 下标/上标在 raw.txt 里跑到了下一行
**原因：** pdfplumber 对脚标/数学符号的行内对齐有时会失败。
**处理：** 翻译时按上下文恢复，例如 `t 1` 应解读为 $t_1$；`v 1 v 8` 解读为 $\nu_1\nu_8$。这是常态，不必修。

### Landscape（横向）表格抽出来全是反序文字
**原因：** 原 PDF 把整页旋转 90° 排版了，pdfplumber 按原始坐标系读出来字符顺序就反了。
**处理：**
- 简短表：直接看原 PDF 用眼睛读，手动重排成中文 `tabularx`。
- 巨型数据表：**总结要点**写到译稿正文，README 里加一行注明 "原始数据见原 PDF 第 X 页"。不要花一整天逐字复刻一张读者根本不会看的数据表。

## 抽图 / Figure rendering

### `render_figures.py` 报 "NOT FOUND on page X"
**原因：** caption 关键字大小写或缩写不一样。常见组合：
- `Fig. 1.` / `Figure 1.` / `Fig 1.` （注意空格 + 点）
- `FIGURE 1` （全大写，无点）
- 出版社特殊缩写如 `Abb. 1.`（德文）
**处理：** 用 `pdfplumber` 或 PyMuPDF 直接 grep 那一页的文本，看 caption 实际写法。然后改 `--figs` 里那条记录的 keyword。

### 图被裁切多了或少了（包含太多正文/缺一部分图）
**原因：** caption 上方可能还有其他文字段（如承接段、表题），上一张 caption 的位置识别有偏差。
**处理：** 用 `--figs` 单独跑那一张图，先用 `--scale 1.0` 快速预览整页，确认图实际占的 y 范围，再考虑：
- 加宽：把 `--top-margin` 改大（如从 72 改成 100~120），下次 fallback 时图框更靠下。
- 直接 override：在那条 `--figs` 项里加 `|bbox=x0,y0,x1,y1` 手动指定矩形。

### 抽出来的根本不是图，是一段英文正文（"Example 2 of this scenario results in..."）
**原因：** `Figure N` / `Fig. N` 字样在页面上同时出现在多处——正文 in-text 引用（"as shown in Figure 5"）、其他图的 caption 文字里、以及真正的图下方的 caption。早期脚本用 `search_for()[0]` 拿第一个匹配（reading order 中正文在前），就把 in-text reference 上方的段落当成了"图"。
**处理：** 当前 `render_figures.py` 使用**上方文字密度**判定真 caption：每个候选命中位置上方 100pt 内的字符数最少者胜。逻辑：真 caption 上方是图（稀疏 / 空白），in-text 引用上方是正文段落（密集）。两种常见布局都能正确：
- 图在首次提到之后（Tan 类）：真 caption 在页面下方，但上方是图区，文字稀少。
- 图在首次提到之前（Liu 第 7 页，三张图竖排在页面顶部，正文在底部讨论）：真 caption y 值较小，上方只有页眉，文字密度更低，依然胜出。

如果还是错（比如图被表格或 sidebar 包围），给那张图加 `|bbox=x0,y0,x1,y1` 手动指定矩形：
```
--figs "5:11:Figure 5.|bbox=50,200,540,560"
```
bbox 坐标是 PDF point（72 pt = 1 inch），可以先 `--scale 1.0` 渲染整页找位置。

### 同一篇论文有多张图同号 caption "Fig. N." 出现在多页
**原因：** 不同章节的图可能错位排版到不同页（尤其同一组图集中排在一页时），加上正文 in-text 引用，page.search_for 跨页可能命中 3~4 处同名字段。
**处理：** `--auto` 模式现在跨页收集所有命中候选，用上方文字密度统一打分（密度越低越像真 caption），最终选全局最优。如果你跑 `--auto` 发现某张图明显不对，先单页跑 `--scale 1.0 --figs "N:<page>:Fig. N."` 看是哪一页错了，再用 `|bbox=...` override。

### 图里带着 Wiley/Elsevier 下载水印（右侧竖排 "Downloaded from..." 或顶部 "C. Liu et al. / ..."）
**原因：** 出版社把水印盖到了每一页边缘，按整页宽度裁就会把水印带进来。
**处理：** 新版 `render_figures.py` 会自动搜 `Downloaded from` / `onlinelibrary.wiley` / `Wiley Online Library` 等模式，把它们的 bbox 从 clip 矩形里挖掉（只在水印靠页边缘 80pt 之内时收缩，避免误伤）。如果水印用了别的字符串或者距离边缘超过 80pt，手动加：
```
--side-margin 30  --top-margin 100
```
Wiley 系刊物的推荐组合就是 `--side-margin 30 --top-margin 100`。Elsevier 类似但 sidebar 可能在 25 pt，可以再细调。

### 是矢量路径图，PyMuPDF 渲染出来模糊
**原因：** `scale=3.0` 对极致清晰度的论文不够。
**处理：** `--scale 4.0` 或 `5.0`。文件会大一点（几 MB），但 Overleaf 完全能撑住。

### 中文期刊论文图全部抽不出来（n_figs=0 但论文明明有图）
**症状：** 跑 `render_figures.py --auto` 在某中文期刊论文上返回 0 figures，但 PDF 里明明有图。
**两种可能：**

1. **Caption 用中文模式但带不同符号**：现支持的中文形式有 `图 N.` / `图N.` / `图 N:` / `图N:` / `图 N：` / `图N：` / `图 N ` / `图N `。如果你的论文用了别的符号（如 `图N、` 顿号、`图-N` 减号）→ 给那张图加 `|bbox=` override，或临时在 `render_figures.py` 顶部 `_STRONG_KW_FORMS` 加一行。

2. **Caption 被栅格化进了图片里**（中文期刊老论文常见做法）：`图 N XXX说明` 整段文字是图片的一部分，`page.search_for("图 N")` 在 PDF 文字层里搜不到任何东西。这种情况启发式无解，必须：
   - 用 PDF viewer 量出每张图的 bbox（PDF point），手动指定：
     ```
     --figs "1:3:图 1|bbox=70,180,540,400"
     ```
   - 或者先用 OCR（如 `ocrmypdf`）把图片里的文字提取成 PDF 文字层，再喂给本 skill。

判断属于哪种：在 raw.txt 里 grep `图 1` 看是否有命中。命中但只在正文段落里（如 "如图 1 所示"）→ 情况 (2)；连一处都没有 → PDF 文字层完全没 caption，必然是 (2)。

### 抽出来的图整张是横倒的（90° 转过来才看得懂）
**原因：** 原 PDF 把横向的大图旋转 90° 塞进竖向的页面里（多面板对比图、宽网络示意图最常见）。PyMuPDF 按原页面坐标渲染，所以保留了原始旋转方向。
**处理：** 新版 `render_figures.py` 会读取 clip 区域内 text span 的 `line.dir` 向量。如果 ≥60% 字符方向不是水平的，就自动反向旋转回正（用 Pillow，是 pdfplumber 的依赖之一，本来就装着）。如果检测器误判，手动 override：
```
--figs "9:24:Fig. 9.|rotate=90"     # 强制 90° CW
--figs "9:24:Fig. 9.|rotate=0"      # 关闭自动旋转
```
rotate 只接 0 / 90 / 180 / 270。

### 抽出来的图不完整：旋转后边缘被裁掉一截
**原因：** 原始 caption-anchored cropping 假设"图在 caption 上方"。对旋转图来说这个假设破产——caption 实际在图的侧边，clip 矩形只覆盖到 caption 的 y 位置就停了，旋回正后那部分变成左/右边缘缺失。
**处理：** 当前 `render_figures.py` 使用**真实 image / drawing bbox 定 clip**：先用 `page.get_images()` 和 `page.get_drawings()` 收集所有 ≥30pt 的图区候选，再按 Voronoi 距离（横排 caption 用「candidate 上方最近的 caption」规则）把候选分给对应 caption，然后取那一组的 bbox 并集做 clip。旋转图的全部内容、同页多图的精确分割都能拿到正确边界。

### 子图带各自的 caption（Fig.5(a) / (b) / (c)），想分别抽成 fig5a.png / fig5b.png …
**症状：** 论文里一张大 Fig.5 下面挂着 (a)/(b)/(c) 三个子图，每个有独立 caption "(a) Random distribution"。当前 skill 默认把整张 Fig.5 当一张 fig5.png 抽出来，没法分别引用子图。多数情况下这是想要的——译稿里直接 `\includegraphics{fig5.png}` + 中文 caption 描述三个子图就够了。
**处理（想分别抽）：** 用 `|bbox=` 指定每个子图各自的矩形：

1. **目测拆分**：在 `_inspection.html` 看红框内的整张 Fig.5，估计 (a)/(b)/(c) 的横向分隔位置（典型并排 3 子图：每个占 1/3 宽，间距 10~20pt）。
2. **配置**：
   ```
   --figs "5a:11:Figure 5.|bbox=80,400,260,520, \
           5b:11:Figure 5.|bbox=270,400,440,520, \
           5c:11:Figure 5.|bbox=450,400,610,520"
   ```
   注意：fig_num 字段必须是数字，不接受 `5a`/`5b` 这种字符串。当前实现下要拆子图，只能用不同的整数（如 `51:11:...|bbox=...` 渲染成 `fig51.png` 作为 5(a)）然后在 main.tex 里 alias 引用。
3. **在 LaTeX 里**：用 `subcaption` 包并排排版：
   ```latex
   \usepackage{subcaption}
   ...
   \begin{figure}[!htbp]\centering
     \begin{subfigure}{0.3\textwidth}
       \includegraphics[width=\linewidth]{fig51.png}
       \caption{随机分布}
     \end{subfigure}\hfill
     ... 5b, 5c 同上 ...
     \caption{LBS 与 NPS 在 FC 选择上的对比}
     \label{fig:lbs-nps}
   \end{figure}
   ```

**默认建议：** 不拆。整张 fig5.png 一起放、在 caption 里写「图 5：…(a) 随机分布；(b) 偏态分布；(c) 中间分布」是论文界惯例。只在子图需要独立 `\ref{}` 引用时才拆。

### 双栏论文（IEEE / Elsevier 双栏版式）抽出来的图横向包含了邻栏的正文/公式
**症状：** `_inspection.html` 里红框横跨整页宽，但其实该图只占左栏（或右栏），抽出 PNG 里能看到一边是图、另一边是密密麻麻的正文段落或一截公式。
**原因：** bbox 检测把邻栏的 raster / 大块 vector drawing 也吸进了 candidates 列表；Voronoi 归属用 "纵向最近的 caption" 判定，没有横向列边界的概念，于是邻栏候选被错分给了这个 caption。
**处理：** 当前没有自动判定双栏布局的能力，需要手动给那张图加 x 范围限定的 `|bbox=`：

1. **读列边界**：用 PDF viewer (Sumatra/Adobe) 打开源 PDF，量一下左栏文字 x 范围（典型 A4 双栏：左栏 ≈ 50~280pt，右栏 ≈ 315~545pt，栏间距 ≈ 30~40pt）。或者跑 `--scale 1.0 --figs "N:<page>:<kw>"` 单独渲染一次整页，肉眼比对图所在 x 范围。
2. **覆盖**：
   ```
   --figs "3:7:Fig. 3.|bbox=50,180,280,360"   # 锁在左栏
   --figs "5:7:Fig. 5.|bbox=315,180,545,360"  # 锁在右栏
   ```
3. **跨栏图**：少数大图会占两栏宽（学术论文常见）。这种情况不需要 override，bbox 检测会自然包含两栏。看 `_inspection.html` 确认。

未来若发现频繁需要 override 双栏论文，可以扩 `find_figure_region_near` 加列边界检测（用 text blocks x 范围分布判定 1 栏 vs 2 栏）。

### `_inspection.html` 里某张图明显裁错（启发式没顶住边界）
**原因：** 当前 bbox + Voronoi + rotation pipeline 是为常见学术论文版式调好的，但具体到某一篇可能遇到没编码过的新边界条件——双栏布局、子图独立 caption (`Fig. 5(a)`)、表格和图共享 bbox、广告/插画混在图区里等等。启发式静默通过（`0 fallback`），但红框框错了位置。
**处理：** 单图 override，一行参数搞定。两种方式按你看到的问题选：

- **位置 / 范围错了** → 手动指定 bbox（PDF point 单位，1 inch = 72 pt）：
  ```
  --figs "5:11:Figure 5.|bbox=50,200,540,560"
  ```
  bbox 坐标可以从 `_inspection.html` 的 clip meta 文字反推（比如它显示 `clip = (113, 100) → (480, 380)`，往左/上/右/下推几十 pt 就能扩到包含完整图），或用 PDF 阅读器（Adobe / Sumatra）的"测量"工具读。

- **旋转方向错了** → 强制或关闭旋转：
  ```
  --figs "9:24:Fig. 9.|rotate=90"     # 强制顺时针 90°
  --figs "9:24:Fig. 9.|rotate=0"      # 关闭自动旋转
  ```

override 之后重跑 `--auto --figs "..."` （--auto 会自动发现其余的图，--figs 只覆盖你显式指定的几张），再看一遍 `_inspection.html` 确认修对了。

### 图顶端被额外裁掉一截（最上方的 node / 标签缺失）
**原因：** bbox-based 路径里如果有「图 y0 接近 top_margin 时强制钳到 top_margin」的 safety floor，会把图实际起始位置高于 top_margin 的那部分裁掉。Tan2025 fig3 在 page 9 上 y0=57，但 top_margin=72 把 clip 钳到了 72，丢了顶 15pt（node 10 + 上面的连接箭头）。
**处理：** 已修复——bbox-based 路径**不再**对 figure_region.y0 做 top_margin 钳制（图区是真实检测到的，本来就准）；top_margin / side_margin 只在 caption-anchored fallback 路径里作用。watermark 自动剔除照常工作，所以 Wiley 顶页眉仍然会被避开。

### 同页多张图被 cross-contaminate（一张图里包含其他图的内容）
**原因：** 一张 PDF 页面上常常有 2~3 张图竖排（如 Liu fig3/4/5 共在 page 7）。如果 clip 直接按 caption y 锚定，一旦图区检测半径设大就会把邻居图也拉进来；设小又可能遗漏。
**处理：** `find_figure_region_near` 接受 `other_captions` 参数做 Voronoi 归属判定——每个 image/drawing 候选先找它**纵向上最近的下方 caption**（横排 caption 时）或**Euclidean 最近的 caption**（旋转 caption 时），只有"最近 = 当前 anchor"时才纳入。`find_all_caption_rects_on_page` 帮 render_one 收集页面上所有 fig caption 位置。

### 旋转后的图把整页吃掉、被 LaTeX 浮动算法甩到文末
**原因：** 原本横长的图（landscape，宽 > 高）被旋正 90° 之后变成竖长（portrait，高 > 宽）。如果你照其他 landscape 图那样写 `\includegraphics[width=0.9\textwidth]`，渲染出来会接近"满版半张纸"，`[ht]` placement 在当前页和下一页都塞不下，浮动算法只能一路推到文末。
**处理：** 给"被旋转过的 portrait 图"单独设置：
```latex
\begin{figure}[!htbp]                          %% 改宽松的 placement
\centering
\includegraphics[width=0.55\textwidth]{fig9.png}  %% 改小宽度
\caption{...}
\label{fig:...}
\end{figure}
```
- `width=0.55\textwidth` 让图渲染成约 9cm × 12cm（A4 双栏可读区的 60% 左右），能与正文同页共存
- `[!htbp]` 让 LaTeX 在「这里 / 顶部 / 底部 / 浮动页」四个位置都尝试
- 若还是漂走，加 `\usepackage{float}` + `[H]` 强制定位（代价是会把当前页剩余空间空出来）

判断哪张图需要这样处理：跑完 `render_figures.py` 看 "auto-rotated" 字样，然后 `python -c "from PIL import Image; img=Image.open('images/figN.png'); print(img.width/img.height)"`——aspect < 0.9 的就属于 portrait，建议宽度 0.5~0.6\textwidth。

## 翻译 / Translation

### 公式编号在 PDF 里不是 (1)(2)(3) 而是 (3.1)(3.2)
**处理：** 在 `\label{}` 里照搬：`\label{eq:c3-1}`、`\label{eq:c3-2}`。`check_tex.py` 只检查 cite/label 一致性，不在意命名约定。

### 一篇论文里反复出现一些专有术语，要不要每次都解释？
**处理：** 首次出现时给中文 + 英文括注，例如 "六边形配送网络（hexagon-based delivery network, HBDN）"。之后用中文或缩写均可。如果用户选了"全文 + 中英术语对照表"，把每个术语首次出现都加进文末附录表。

### 参考文献条目要不要翻成中文？
**默认 NOT。** 保留英文条目方便读者去搜原文。如果遇到中文文献（GB/T 引用风格），保留原中文条目。

### 中文论文（参考文献用 GB/T 7714 格式）能不能直接抽 bibitem？
**能，开箱即用。** `extract_bibliography.py` 的 numbered 路径同时识别：
- 英文段标题 `References` / `Bibliography` 和中文 `参考文献` / `文献参考`
- `[1]` `[2]` `[3]` 前缀（含 GB/T 7714 的 `[J]/[M]/[C]/[D]/[N]` 文献类型标识——这些在条目内部不影响切分）
跑法和英文论文一样：
```
python scripts/extract_bibliography.py raw.txt --out _bib.tex
```
中英混排的条目（部分中文文献 + 部分英文文献）也会正确切分并保留原样。**唯一需要手工干预的是**：若该论文用作者-年份 (`张三 (2020). 标题…`) 而非编号格式，当前不识别——把它改成 `[1] 张三 (2020). 标题…` 一次性手工编号即可。这种格式在中文学术圈罕见。

## 校验 / Validation

### `check_tex.py` 报 ODD $ count
**最常见原因：** 把 `$x$` 写成 `$x` 漏了一个 $；或者把美元符号当真实美元用了（应该转义为 `\$`）。
**处理：** 看脚本输出的具体行号，到 `main.tex` 那一行去找，往往一眼就看出来。

### `check_tex.py` 报 MISSING bibitems
**原因：** 翻译时手快写了 `\cite{Smith2020}`，但 `\begin{thebibliography}` 里忘了加。
**处理：** 要么加 bibitem，要么删 cite。永远不要忽略这个警告 — Overleaf 编译时会报 undefined citation。

### `compile_pdf.py` 报 "Undefined control sequence" 比如 `\multirow`
**原因：** `main.tex` 用到了某个命令，但 preamble 里漏装了对应的包。最常见的就是 `\multirow`（需要 `\usepackage{multirow}`）、`\toprule`（需要 `\usepackage{booktabs}`）。`check_tex.py` 是纯静态检查，不知道每个控制序列归属哪个包，所以抓不到这类问题。
**处理：** 看脚本输出里 "Undefined control sequence <recently read> \xxx" 那一行，在 preamble 加上对应的 `\usepackage{xxx}`，重跑。如果不确定哪个包提供这个命令，搜 "ctan \xxx package"。

### `check_tex.py` 全过但 Overleaf 还是编译报错
**最常见两个原因：**
1. **Compiler 没选 XeLaTeX**：ctex 必须 XeLaTeX，pdfLaTeX 直接报错。Menu → Compiler → XeLaTeX。
2. **某个包 Overleaf 没装**：通常 Overleaf 自带 ctex、tabularx、algpseudocode 等常见包。如果用了冷门包，看 Overleaf 报错的 Log 面板第一条 `! LaTeX Error: File 'xxx.sty' not found.`，然后在 `main.tex` 把那条 `\usepackage{xxx}` 去掉换等效写法。

## 中英对照版 / Bilingual edition

### `main.pdf` 编译成功，`main_bilingual.pdf` 编译失败
**原因：** 纯中文版把每个 `\bipar` 的原文一半直接丢掉，从不排版它；只有对照版会排原文。所以原文一半里的错误只在对照版暴露：没转义的 `&`、`#`、`_`（数学外），或者原文里抄进了 `ht−1` 这类不在 `$...$` 里的公式残片。
**处理：** 看 `main_bilingual.log` 的第一条 `!` 报错和行号，回到 `main.tex` 那个 `\bipar` 的第一对花括号里修。注意没转义的 `%` 更隐蔽：它把这一行剩下的内容（包括中文一半）都注释掉，两个版本都会出错或丢段。

### `check_bilingual.py` 报 MISSING-SRC / MISSING-ZH
**原因：** 多半是花括号不配对——某个 `\bipar` 的第一或第二个参数吞掉了后面的段落，或者把中文写进了第一个参数。
**处理：** 打开报错行号处的 `\bipar`，确认形如 `\bipar{原文}{中文}`，两个参数各自闭合。若只是页眉、分页把探测文字截断，看一眼那一页确认正常即可。

### 对照版脚注出现两次
**原因：** 把 `\bifoot` 写进了原文一半，或者原文、中文两半各写了一次。
**处理：** `\bifoot{原文脚注}{中文脚注}` 只写一次，放在中文那一半里。作者区的脚注用 `\bithanks`，`\bifoot` 在 `\author` 里不起作用。

### 对照版标题下面多出一个空行
**原因：** 在 `\bipar` 前面写了 `\noindent`（例如摘要）。
**处理：** 改成 `\bipar{原文}{\noindent 中文}`。

### “摘要”“参考文献”标题只有中文，或页眉里出现英文
**原因：** 在 `main.tex` 里自己改了 `\abstractname` / `\refname` / `\@mkboth`。
**处理：** 删掉这些改动。对照版入口 `main_bilingual.tex` 会自动给这两个标题配上 Abstract / References，并让页眉只显示中文。

## 一般性建议

- **先小后大**：第一次跑别上来就 30 页，先用 5 页测试整条 pipeline，再上完整稿。
- **保留 raw.txt**：调好之后别删，做引文对照很有用，也方便后面回头再翻一遍。
- **结果应当能再编辑**：交付物是 `.tex` 源码，不是只读 PDF。用户拿到后会自己改、自己加批注，不要把内容塞进图片或扁平化。
