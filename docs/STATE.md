# 项目状态

> **这份文档是「活的」—— agent 完成实质工作后应更新这里。**
> 记录当前状态、待办、以及容易随时间失真的事实（路径、版本、产物）。
>
> 配套：[`../AGENTS.md`](../AGENTS.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md)、
> [`DEVELOPMENT.md`](DEVELOPMENT.md)、[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
>
> **最后更新：2026-10-04**

---

## 1. 一句话状态

**可用，功能完整。** 日→中字幕翻译全链路跑通（多模型 ASR + LLM 融合/翻译/校对
+ 说话人分离 + ASS 输出），打包版与源码版均验证过端到端 `status: done`。

---

## 2. 环境快照

| 项 | 值 | 备注 |
|---|---|---|
| 项目根 | `E:\Study\AI\jsub-translator-exe` | **2026-09-28 从 `E:\大学\AI\` 移来** |
| Python | `.venv\Scripts\python.exe` → 3.13.14 | 项目内，不在 C 盘 |
| EXE | `dist\jsub-translator.exe` | 568 MB，**2026-09-30 23:38 构建**（含下载 cookies 支持）|
| 模型目录 | `<root>\models` | **3.5 GB**：small / medium / large-v3-turbo |
| 输出（源码版） | `<root>\output` | |
| 输出（EXE 版） | `<root>\dist\output` | |
| 测试 | 9 文件 / **326 断言** | 全绿 |
| 说话人后端 | sherpa-onnx | CPU，可用 |

**模型缓存明细**（`<root>\models\`）：

| 目录 | 体积 |
|---|---|
| `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo` | 1.6 G |
| `models--Systran--faster-whisper-medium` | 1.5 G |
| `models--Systran--faster-whisper-small` | 464 M |
| `3dspeaker_..._eres2net_..._16k.onnx` | 39 M |
| `sherpa-onnx-pyannote-segmentation-3-0/` | 分割模型 |

**EXE 备份**（`dist\`，共约 1.6 GB，**占空间，可清理**）：

| 文件 | 体积 | 说明 |
|---|---|---|
| `jsub-translator.exe.bak-prev` | 568 M | 上一版 |
| `jsub-translator.exe.bak-speaker` | 518 M | 加说话人前的版本 |
| `jsub-translator.exe.bak-20260831` | 541 M | 更早的版本 |

---

## 3. 界面设计系统（改样式前先读）

界面全部在 `web/templates/index.html` 内联（Flask 未配 `static_folder`，
打包也只带 `web` 目录）。**改样式请改 `:root` 的令牌，不要在组件里写死值。**

| 令牌组 | 值 | 说明 |
| --- | --- | --- |
| 底色 | `--ink-900 #0a0c11` → `--ink-500 #1f293c` | 页面 / 面板 / 提升层 |
| 描边 | `--line-1 #212a3a`、`--line-2 #2c3849` | 发丝线 |
| 文字 | `--tx-1 #e9edf5` / `--tx-2 #a7b2c6` / `--tx-3 #78859b` | 对比度均 ≥4.5:1（`--tx-3` 是实测筛选后的值，**不要再调暗**）|
| 强调 | `--cit-400/500/600` 柠檬色系 | 全站唯一强调色，语义色另计 |
| 语义 | `--ok` `--warn` `--bad` `--info` | 仅表意，不做装饰 |
| 字体 | `--f-display` 衬线 / `--f-body` 无衬线 / `--f-mono` 等宽 | 共 3 族，不再增加 |
| 节奏 | `--s-1..9`（4/8/12/16/24/32/48/64/96）| 间距只用这些值 |
| 圆角 | `--r-1..4`（6/10/14/20）| |
| 缓动 | `--ease` | 全站统一，不用浏览器默认 `ease` |

**约定**：

- 动效只动 `transform` / `opacity`；`prefers-reduced-motion` 下全部降级
- 可点击元素必须有 `cursor:pointer` 与可见 `:focus-visible` 环
- 说话人颜色 / 缩写名由服务端下发（`seg.color`、`seg.speaker_short`），
  前端**不得**自行计算，否则会与生成的 ASS 文件不一致

---

## 4. 最近变更

### 2026-10-04 — 输出格式清单：后端产出 5 种，前端按钮手写一份

`generate_all_formats()` 产出 5 个格式键，前端 `const map = {...}` 把下载按钮
映射到这些键 —— **两边各写了一份**。今天两边一致：

```
bilingual / bilingual_split / japanese / chinese / bilingual_speaker_colored
```

漂移的后果是**静默的**：后端加了新格式却忘了加按钮 → 那个文件生成了、也躺在
输出目录里，但界面上**根本没有按钮**，用户以为没有。

这类「契约型重复」不宜硬合并（按钮有固定顺序和文案），所以改为**测不变量**：
跑 `generate_all_formats()` 拿到真实产出的键集，与前端 map 的键集双向比对。
新增 2 条断言；变异验证：删掉一个前端按钮 → 退出码 1。

### 2026-10-04 — 翻译引擎清单被抄了四份（空串能过校验却建不出翻译器）

同一个语义「有哪些翻译引擎」被写在**四处**：`resolve_api_key`、`validate_config`
各一遍 if/elif，`create_translator` 再一遍，网页 `<option>` 还是手写死的。
加第三个引擎要改四处，漏一处就是「后端支持但界面选不到」。

更要紧的是**行为已经漂移了**：对空串 `''` 的处理三处不一致 ——

| 输入 | `resolve_api_key` | `validate_config` | `create_translator`（旧）|
|---|---|---|---|
| `''` | 回退默认 ✓ | 回退默认 ✓ | **抛错** ✗ |

前两处写的是 `(provider or TRANSLATION_PROVIDER)`，第三处写的是 `if provider is None`。
后果：空串**过得了校验、却建不出翻译器** —— 校验这道门等于没关。

修法：清单收口到 `config.VALID_PROVIDERS` 一处，`create_translator` 的空串处理
与前两处对齐，网页 `<option>` 改由服务端下发（标签属展示文案，留在模板里，
新引擎没配标签时退回显示引擎名）。

新增 6 条断言，核心是**真不变量**：同一个输入，三处判定必须得出同一结论。
变异验证：把 `is None` 装回去 → 退出码 1，报出 2 条失败。

### 2026-10-04 — 说话人配色：预览色与字幕文件 5/8 对不上

用「**同一语义在两处实现**」这个视角复查，抓到 `ass_writer.py` 里两张并行维护的
配色表：`SPEAKER_COLORS`（ASS 的 `&HAABBGGRR`）与 `SPEAKER_CSS_COLORS`（网页预览）。
两张表**数量都是 8、下标对齐**，所以之前那条「数量一致」的断言一直是绿的 ——
但实际逐项反解比对，**8 项里有 5 项不是同一个颜色**：

| 下标 | ASS 实际渲染 | 网页预览（旧） | |
|---|---|---|---|
| 0 | `#ffff00` | `#ffd700` | ✗ |
| 2 | `#00ff00` 纯绿 | `#69f0ae` 薄荷 | ✗ |
| 3 | `#ff00ff` 品红 | `#ff80ab` 粉 | ✗ |
| 6 | `#ffc0cb` 粉 | `#ea80fc` 紫 | ✗ |
| 7 | `#a0d0e0` 淡蓝 | `#e0d0a0` 土黄 | ✗ |

**第 8 项还藏着一个独立的字节序 bug**：ASS 是 `AABBGGRR`（蓝在先），
前 7 项都正确做了换位，**第 8 项漏了** —— 注释写着 Khaki，实际渲染成淡蓝。

**验证方式不是读规范，而是跑真正的消费者**：用 libass 渲染成帧后采样像素。

| 写入值 | libass 实际渲染 | 色相 |
|---|---|---|
| `&H00E0D0A0`（旧）| `#9fcedf` 淡蓝 | 196° |
| `&H00A0D0E0`（新）| `#decfa0` 土黄 | 45° ✓ |
| `&H00FFFFFF`（对照）| `#ffffff` | — |

对照组证明渲染管线工作正常；8 个色与转换函数的**最大色相差 1.1°**，
而字节序写错会产生 **99°～150°** 的偏差 —— 这个测试确实能分辨。

**修法是收口，不是同步**：`SPEAKER_CSS_COLORS` 改为由 `SPEAKER_COLORS`
经 `abgr_to_css()` 推导，`get_speaker_color` / `get_speaker_css_color`
共用同一个 `_speaker_index()`。以后改配色只改一处，两份不可能再漂移。

新增 6 条断言（含「CSS 色必须 == ASS 色反解」这个真不变量）。
**变异验证**：把手写表装回去 → 退出码 1，报出 3 条失败。

### 2026-10-04 — 走失败路径：端口重复绑定、配置笔误、文档锚点腐烂

**结论先行**：这一轮所有发现都来自「把失败路径也走一遍」，没有一个是看界面看出来的。
失败路径的缺陷密度明显更高 —— 写代码时想的是「顺利的话怎么走」，测试时测的也是主流程。

**一、双击两次会开出两个实例（Windows 允许重复绑定）**

实测：先占住端口，再起第二个实例 —— **第二个没有崩溃，继续跑着**。
直接验证两个 socket 都 `SO_REUSEADDR` 后绑定 7873：**两个都成功**。

这是 Windows 与 Linux 的行为差异（Linux 下第二个会 `EADDRINUSE`）。
后果是隐蔽的：两个实例都能 bind，但请求打到**先绑定的那个** ——
用户在浏览器里看到的可能是旧实例，改了代码却「没生效」。

处理：新增 TROUBLESHOOTING §17 说明现象与自检方法，并把 §11（测打包版时结论诡异）
指到这个根因。**未改代码加单实例锁** —— 这是 Windows 的既定行为，
加文件锁会引入新的失败模式（残留锁文件），收益不抵风险。

**二、`.env` 里一个笔误让整个应用起不来（本轮最严重）**

六个数字配置项裸写 `int(os.getenv(...))` / `float(os.getenv(...))`，在**导入阶段**求值。
实测 `FUSE_BATCH_SIZE=auto`：

```
File "src/config.py", line 166, in <module>
    FUSE_BATCH_SIZE = int(os.getenv("FUSE_BATCH_SIZE", "15"))
ValueError: invalid literal for int() with base 10: 'auto'
```

**为什么特别严重**：打包版双击启动时**没有控制台**，表现为「点了没反应」，无从排查。
而这几个都是可调参数（批大小、重试次数、阈值），写错一个不该让工具彻底打不开。

修：新增 `_env_int()` / `_env_float()`，值非法时**退回默认值 + 打印明确警告**
（含配置项名与当前值），空值也走默认。

**三、ffmpeg 报错是一堵 2499 字符的噪音墙**

走「拖进来一个非媒体文件会怎样」：ffmpeg 先打 10 行版本横幅
（`ffmpeg version` / `configuration` / `libav*`），真正的原因
`Invalid data found when processing input` 在**最后几行**。

修：新增 `_condense_ffmpeg_stderr()` 剔掉横幅 + 加一句人话开头
→ **2499 字符降到 251 字符**，关键信息在第一行之后立刻可见。

**四、文档锚点腐烂（两处已死了一段时间）**

校验文内锚点时发现 §4 / §5 的索引链接指向不存在的 slug ——
标题被改过，索引没跟着改。共 3 处失效（§4 被引用两次）。

这类问题**没有任何运行时症状**，只有点链接时才发现，所以会一直烂下去。
修：修正 3 处锚点，并新增 `_test_docs_links.py`（第 9 套测试，10 条断言）把这件事钉死：

| 守什么 | 怎么守 |
|---|---|
| 跨文件相对链接 | 逐个 `Path.exists()` 验证 |
| 文内 `#锚点` | 标题 slug（GitHub 算法近似）与链接比对 |
| 索引完整性 | TROUBLESHOOTING 每个编号章节都必须出现在快速索引里 |

**该测试本身做了变异验证**（确保不是空过）：
改标题 → 退出码 1，正确报出失效锚点；删索引行 → 退出码 1，报出漏索引的章节。

顺带把测试运行命令从「手写 8 个文件名」改成 `for t in _test_*.py`
—— 以后新增测试文件不用再改文档。

**验证**：九套测试 **312 passed, 0 failed**（302 → 312）；文档失效链接 **0**。

### 2026-10-04 — 界面重做：从「Bootstrap 后台」改为「字幕工作室」

原页面是 Tabler 默认外观 + 一堆等权卡片，问题很具体：

- **层级失效** —— 视频源、设置、缓存清理看起来一样重要
- **信息密度不均** —— 缓存清理这种低频破坏性功能占了与主流程相当的面积
- **无品牌特征** —— 换成任何工具都成立
- **移动端只是「没坏」** —— 靠框架默认行为，没有为小屏做过设计

**改法（保留全部功能与接口，只重做表现层）**：

| 维度 | 做法 |
| --- | --- |
| 宏观结构 | 工作台（Workbench）：不对称两栏，左侧主任务、右侧设置，而非「Hero → 三卡 → CTA」 |
| 色彩 | 墨蓝底 + 单一柠檬强调色（**刻意避开 AI 紫蓝渐变**），语义色仅表意 |
| 字体 | 衬线标题 + 无衬线正文 + 等宽数据，共 3 族 |
| 首屏 | 编辑式陈述「听日语，读中文。」+ **真实输出样例**（取自本工具实际跑出的 JP→CN 句对，非编造） |
| 渐进披露 | 缓存清理改为默认收起的 `details`，摘要行显示「N 个文件 · X MB」 |
| 动效 | 只动 `transform`/`opacity`；尊重 `prefers-reduced-motion` |

**过程中发现并修掉的真问题**：

1. **上传区布局塌陷** —— `.drop` 挂在 `label` 上，而 `label` 默认 `display:inline`，
   内部的绝对定位 `input` 把行盒撑出空档，原生按钮还露了出来。
   修：显式 `display:block` + `opacity:0` 铺满。
2. **弱化文字对比度不达标** —— `--tx-3` 在面板底色上只有 **4.48:1**，
   低于 AA 的 4.5。实测筛选后改为 `#78859b`（5.06 / 4.75）。
3. **320px 下状态胶囊溢出 5px** —— 加窄屏媒体查询收紧间距。
4. **装饰性章节编号（01 / 02）** —— 命中自己列的反 AI 套路清单第 12 条，已移除。
5. **文案长破折号** —— 两处 `——` 改为句号/冒号（清单第 11 条）。

**验证**（都是实测，不是推断）：

| 项目 | 结果 |
| --- | --- |
| 横向溢出 | 320 / 360 / 375 / 414 / 768 / 1024 / 1440 **全部 SW == VW，无溢出元素** |
| JS 语法 + ID 引用 | 35 个引用 id 全部存在；标签配对 |
| 结果态渲染 | 注入真实结构的结果数据，预览/说话人色标/未翻译态/统计/6 个下载项全部正确 |
| 缓存态渲染 | 摘要行正确显示「6 个文件 · 20.0 MB」 |
| 八套测试 | **280 passed, 0 failed** |

**评审后又修的两处**：

6. **主按钮落到首屏之外** —— 右栏「处理设置」约 700px 高，把「开始处理」推到 ~1030px，
   用户填完链接还得滚动。修：3 个开关收进「高级选项」折叠块（摘要显示开启数），
   默认路径「贴链接 → 点开始」完整落在首屏内。
7. **多行错误信息被压成一长句** —— 后端抛的是结构化三行错误
   （问题 / 原因 / 怎么办），但 `.note` 默认 `white-space:normal` 把换行压成空格。
   修：`#error-message{white-space:pre-wrap}`。

**排版收口**：

8. **中文字体栈顺序错** —— 拉丁字体不含 CJK 字形，中文会落到栈里**第一个有 CJK 的字体**。
   原栈把 `"Noto Serif JP"` 排在简体中文衬线之前（`Songti SC` 是 macOS 专有，本机没有），
   于是 **`lang="zh-CN"` 的页面用日文字形**（直 / 骨 / 令 写法不同）。
   修：拉丁字体在前 → CJK 按 **zh → ja** 排 → generic 兜底。
9. **标题动词权重不一致** —— 「听日语，**读**中文。」里 `读` 被弱化而 `听` 是正文色，
   但两者是同一语法角色。改为动词同色、强调只落在 `中文`（真正的语义对比）。

**无障碍收口**：

10. **5 个触控热区只有 37px** —— 实测 `.btn--sm` 为 `7px padding + 12.5px 字号`，
    低于 44px 下限。修：`@media(max-width:768px),(pointer:coarse)` 下 `min-height:44px`。
11. **日文缺 `lang="ja"`** —— 屏幕阅读器会用中文语音念日文。补 3 处。
12. **favicon 404 + 5 块死 CSS** —— 内联 SVG data-URI 图标（不额外发请求）；
    清掉重做过程中遗留的 `.wave` / `.pills` / `.reveal` / `@keyframes rise`。
13. **被淘汰的任务让界面静默卡住** —— 后端对不存在的任务回 `{error}` 且**不带 status**，
    前端只判断 `status` 就落到 `status !== 'done'` 那条静默 return，
    界面永远转圈。修：先判 `data.error`。
    （与「失败任务永远报 running」同类，只是入口不同。）

**下载端点收口**（这是整条链路的终点，用户拿到的就是它）：

14. **`.ass` 被当成 `audio/aac`** —— Python 的 `mimetypes` 把 `.ass` 猜成音频类型
    （实测 `guess_type('x.ass') == ('audio/aac', None)`）。字幕文件被标成音频，
    语义错误、也可能被安全软件误判。修：显式 `mimetype="application/octet-stream"`。
15. **404 回纯文本，违反本项目 R4** —— 原来 `return "File not found", 404`，
    而 R4 要求所有 `/api/*` 一律回 JSON。修：走 `_json_error()`。

同时验证了该端点的**穿越防护有效**：`../../etc/passwd` 与 `..%2F..%2Fapp.py`
都返回 404 JSON（`Path(filepath).name` 剥掉了所有目录部分），
中文文件名与 URL 编码后的中文名均可正常下载，附件头带 RFC 5987 文件名。

**上传端点实测**（用恶意文件名探测，未改动代码）：

| 输入 | 结果 |
| --- | --- |
| `../../../evil.mp4` | ✓ 穿越被挡，存为 `{uuid}.mp4` |
| `..\..\evil.mp4` | ✓ 反斜杠穿越同样被挡 |
| 无扩展名 | ✓ 兜底为 `.mp4` |
| 超长扩展名（300 字符）| ✓ 报可读错误（路径超限），不是崩溃 |
| `evil.exe` / `evil.bat` | ⚠ 被接受并保留扩展名 |

> `evil.exe` 一类被接受是**有意保留**的：前端 `accept` 只过滤选择框，
> **拖拽不受它约束**，用户可以把 `.m2ts` 等清单外但 ffmpeg 支持的格式拖进来。
> 加白名单会挡掉这些合法用法。非媒体文件会在 ffmpeg 阶段失败并给出清晰提示
> （见下一条），不会静默出错。

**ffmpeg 报错信息收口**：

16. **错误信息是一堵 2500 字符的噪音墙** —— ffmpeg 失败时先打 10 行版本横幅
    （`ffmpeg version` / `configuration` / `libav*` 版本号），真正的原因
    （`Invalid data found when processing input`）在**最后几行**。
    实测非视频文件产出 14 行约 2500 字符，界面上几乎只看到横幅。
    修：新增 `_condense_ffmpeg_stderr()` 剔掉横幅 + 加一句人话开头
    → **2499 字符降到 251 字符**，关键信息在第一行之后立刻可见。

**配置解析收口**：

17. **`.env` 里一个笔误让整个应用起不来** —— 六个数字配置项裸写
    `int(os.getenv(...))` / `float(os.getenv(...))`，**在导入阶段**求值。
    实测 `FUSE_BATCH_SIZE=auto` 直接抛：
    ```
    File "src/config.py", line 166, in <module>
        FUSE_BATCH_SIZE = int(os.getenv("FUSE_BATCH_SIZE", "15"))
    ValueError: invalid literal for int() with base 10: 'auto'
    ```
    **打包版被双击启动时用户根本看不到控制台，表现为「点了没反应」。**
    修：新增 `_env_int()` / `_env_float()`，值非法时**退回默认值 + 打印明确警告**
    （含配置项名与当前值），空值也走默认。可调参数写错不该让工具打不开。

**8 状态覆盖**（全部实测，非声明）：default ✓ / loading ✓（进度条 47% 且
`aria-valuenow` 同步为 47）/ error ✓（三行结构完整）/ success ✓ / empty ✓ /
focus-visible ✓（柠檬焦点环）/ disabled ✓（处理中按钮置灰 + 转圈）/ hover·active（CSS 已定义）。

**L0 真人功能验证**：真实任务跑通（19 段字幕、4 位说话人、5 个文件），
真实响应字段与 UI 期望逐项比对一致，真实数据渲染 `cues=19 downloads=6` ✓

> **踩到的坑**：headless Chrome 的 `--window-size` 有 ~503px 下限，
> 直接用它截 375px 会得到「右侧被裁掉」的假象，**差点误判成横向溢出**。
> 改用 iframe 固定宽度后才测到真实布局。
> 另外 7871 端口一度出现**两个监听者**（正是 AGENTS.md R7 警告的情形），
> curl 打到旧进程、返回缓存模板，导致「明明改了却没生效」。
> 还有：写注入探针时 `\n` 经「JSON → heredoc → Python → JS」四层转义被吃掉，
> 变成 JS 字符串里的真实换行 → 语法错误 → 整个脚本静默不执行。
> 改用 `String.fromCharCode(10)` 绕开转义层。
> 最后：`rm -rf` 大目录（234 文件）触发 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`，
> **把整条链式命令一起中断**，`rm ... && ./build.bat` 里的构建根本没启动 ——
> 不要把 `rm` 和关键操作串在一条命令里。

**验证局限**（如实记录）：想确认「实际渲染的是哪个字体」试了两条路都失败 ——
canvas 测宽度（CJK advance 恒为 1em，全部返回 480）与
`document.fonts.check()`（对**所有**字体都返回 true，连 macOS 专有的也「有」）。
字体栈修复依据的是**推理**（拉丁字体无 CJK 字形），不是直接测量；
但该修复无害 —— 即使 `Noto Serif SC` 不存在也只是像原来一样继续往后落。

**已知限制**：flex `gap` 未加旧版 iOS Safari 回退 —— 本工具是本地
`127.0.0.1` 桌面应用，iOS Safari 不在目标内；如后续要支持移动端访问需补
`@supports` 回退。

### 2026-10-01 — 失败判定不再靠字符串匹配

「某段没翻译成功」这个约定**被硬编码在两处**：

```python
# src/translator.py —— 写入
seg_copy["translated"] = f"[翻译失败: {str(e)}]"
```
```javascript
// web/templates/index.html —— 判断
const isMissing = !cn || cn.startsWith('[翻译失败');
```

后端一改文案，前端就**静默失效** —— 失败的字幕会被当成正常译文显示出来，
而且不报错、不留痕。

**修复**：改成**布尔字段**。

- 新增 `FAILED_MARKER` 常量 + `_mark_failed()` 辅助函数（唯一定义处）
- 每段下发 `failed: true/false`，前端用 `s.failed` 判断
- 译文里仍保留可读文本（字幕文件里要能看出哪段失败了）

**测试踩的小坑**：我断言「translator 里只出现一次该字符串」，
第一次算出 2 次 —— 其中一次是**注释**里提到它。
改成只统计非注释行。**断言要区分代码和注释，否则会把合理的文档当违规。**

**新增 8 条断言**（`_test_translate_resilience.py` 28 → **36**）；
其中一条跑真实 API 调用，确认成功段落 `failed=False`。

**验证**：八套测试 **280 passed, 0 failed**。

### 2026-10-01 — 说话人缩写名改由服务端下发

`speaker_short`（`SPEAKER_00 → S00`）原本前后端各写一遍：

```python
# 后端  ass_writer._short_speaker
speaker.replace("SPEAKER_", "S") if speaker.startswith("SPEAKER_") else speaker
```
```javascript
// 前端
spk.startsWith('SPEAKER_') ? spk.replace('SPEAKER_','S') : spk
```

**现在两处行为一致** —— 但同一个转换写两遍，迟早有一边先改
（这次的颜色问题就是活例）。改为：**服务端下发 `speaker_short`，前端直接渲染**，
与 `color` 的处理方式一致。

**新增 8 条断言**（`_test_ass_alignment.py` 47 → **55**）：
缩写规则正确、非前缀原样保留、空值返回空、
前端不再自带 replace、接口下发且值与 `_short_speaker` 一致。

**验证**：八套测试 **272 passed, 0 failed**。

> 顺带核查了几处，确认**没有**问题：输出模式名（后端唯一一份）、
> provider 名单（前后端一致）、config 默认值与 `.env`（差异都是用户自定义值）、
> `generate_all_formats` 的四种模式 + 分色变体（齐全）。
> 不报没问题的地方，和报有问题一样重要。

### 2026-10-01 — 删掉一份会「每次运行换颜色」的死代码

继续扫「同一语义多处实现」，找到**第三份**说话人配色 —— 在 `src/diarizer.py` 里。

它有**两个**问题：

1. 它是**旧版**配色，**首位是纯白 `&H00FFFFFF`** —— 而 `ass_writer` 特意避开纯白
   （分到白色就等于在分色版本里看不出区别）。前端之前也犯过同样的错。
2. 取色用**内置 `hash()`**，而 Python 对字符串的 hash **每进程随机化**
   （除非设 `PYTHONHASHSEED`）。实测同一标签三次运行得到三个不同值。
   可它的 docstring 写着 `"""Get consistent ASS color..."""` —— **一点都不 consistent**。

**当时没有任何代码引用它**（`pipeline` / `app` / 测试全部从 `ass_writer` 导入），
属于死代码。**已删除**，并在原处留下说明指向权威位置。

> 这类死代码比普通死代码更危险：它的名字和位置都「看起来很对」，
> 下一个要加配色功能的人极可能直接 import 它，然后得到一个
> 每次运行都不同的颜色 —— 而且很难想到是 hash 随机化导致的。

**新增 5 条断言**（`_test_ass_alignment.py` 42 → **47**）：
diarizer 不再定义配色/取色、留下指向说明、**取色跨进程稳定**。

跨进程稳定性用 `PYTHONHASHSEED=0/1/12345` 跑三次子进程比对 ——
**这个断言真的能抓到**：换成 `hash()` 实现后，三个 seed 分别得到 index 6 / 1 / 3，
断言立刻失败。

**验证**：八套测试 **264 passed, 0 failed**。

### 2026-10-01 — 字幕里的花括号会让内容凭空消失

继续用「**同一语义在多处各有一份实现**」扫，抓到两处。

**1（严重）：ASS 文本没转义花括号**

`ass_writer._escape_ass()` 只处理换行，docstring 还写着
「ASS handles most Unicode natively — only need to handle newlines」。
**这句是错的**：ASS 里 `{...}` 是 override block，正文里出现半角花括号，
渲染器会把**括号连同里面的内容**一起当成样式块丢掉。

**用 libass 渲染成图片逐张比对实测**（不是推测）：

| 字幕文本 | 渲染结果 |
| --- | --- |
| `A{これは} B` | 与 `A B` **逐像素完全相同** → `{これは}` 整段消失 |
| `A\{これは\} B` | 同样消失 —— **反斜杠转义在 libass 下无效** |
| `A｛これは｝ B` | 正常显示 ✓ |

也就是说：日文字幕里只要出现 `{`，**那一段字就没了**，
而且观众和作者都很难意识到「少了一段」。

**修复**：`{` → `｛`、`}` → `｝`（全角 U+FF5B/U+FF5D）。
字形几乎一样，但不是特殊字符 —— 唯一既保住内容、又不依赖特定渲染器扩展的做法。

**2（轻微）：网页文件选择框漏了 `.m4v`**

`cache.MEDIA_EXTS` 有 17 个扩展名，网页 `accept` 只有 16 个 —— 少 `.m4v`。
于是「缓存清理」认得 `.m4v`，用户在选择框里却挑不到它。

**修复**：补齐 `.m4v`，并加断言锁死两处一致（防止再漂移）。

**验证**：`_test_ass_alignment.py` 33 → **42**、`_test_cache_safety.py` 24 → **27**；
八套测试合计 **259 passed, 0 failed**。

> 渲染验证的价值：我一开始也想「按规范推断应该是这样」，
> 但**渲染出来逐像素比对**才真正证明了
> 「`\{` 转义无效」这个反直觉的点 —— 如果按推测去修，
> 就会用一个无效的转义方案「修好」它。

### 2026-10-01 — 两处 ffmpeg 查找不一致（Chocolatey / Scoop 用户会中招）

继续用「**同一语义在多处各有一份实现**」这个视角扫，找到第二处：

`audio.find_ffmpeg()` 与 `downloader._find_ffmpeg_path()` 各维护了一份
ffmpeg 搜索顺序，而 **downloader 少搜了三处**：

| 位置 | `audio` | `downloader` |
| --- | --- | --- |
| PATH / 注册表 | ✓ | ✓ |
| WinGet Packages | ✓ | ✓ |
| `C:/ffmpeg/bin` | ✓ | ✓ |
| `C:/Program Files/ffmpeg/bin` | ✓ | ✓ |
| `C:/Program Files (x86)/ffmpeg/bin` | ✓ | ✗ |
| `C:/ProgramData/chocolatey/bin` | ✓ | ✗ |
| `~/scoop/shims` | ✓ | ✗ |

**症状**：用 **Chocolatey 或 Scoop** 装 ffmpeg 的用户会看到
**「音频能提取，但视频合并不了」** —— 提取音频走 audio 的搜索（找得到），
合并走 downloader 的搜索（找不到）。两条路径看到的不是同一个 ffmpeg。

**修复**：`downloader._find_ffmpeg_path()` 改为**复用 `audio.find_ffmpeg()`**
并取其父目录（yt-dlp 的 `ffmpeg_location` 要的是目录）。
单一事实来源，搜索范围自动一致。找不到时仍返回 `""` 交给 yt-dlp，
与改动前行为一致。

顺带清掉 downloader 里因此不再使用的 `os` / `sys` 导入。

**新增 5 条断言**（`_test_downloader_cookies.py` 39 → **44**）：
两个查找器指向同一目录、候选目录数 ≥5、downloader 不再自带搜索顺序。

**验证**：八套测试 **247 passed, 0 failed**。

### 2026-10-01 — 网页预览的说话人颜色与字幕文件对不上

前几轮都在审 Python，**前端只验过语法、没验过正确性**。这轮补上，找到两个真问题：

**1. 预览颜色与生成的 ASS 文件不一致** —— 同一说话人颜色不同：

| 说话人 | 网页预览（旧） | 实际字幕文件 |
| --- | --- | --- |
| SPEAKER_00 | `#ffffff` **白** | `#ffd700` 黄 |
| SPEAKER_01 | `#ea80fc` 紫 | `#e0d0a0` 卡其 |
| SPEAKER_02 | `#82b1ff` 蓝 | `#ea80fc` 紫 |
| SPEAKER_03 | `#ff9100` 橙 | `#ff9100` 橙 ✓ |

两个原因叠加：

- **哈希算法不同** —— 前端用 djb2 风格，后端用 md5
- **调色板不同** —— 前端 8 色含纯白，后端 8 色不含

**2. 前端把纯白排在调色板首位** —— 于是 0 号说话人在预览里是**白色**，
正是 `ass_writer.py` 特意注释避开的情况（「分到白色就等于看不出区别」）。
后端避开了，前端又把它引回来了。

**修复**：**颜色改由服务端下发**（`/api/result` 每段带 `color`），
前端不再自己算 —— 单一事实来源，从结构上杜绝两边漂移。

**新增 8 条断言**（`_test_ass_alignment.py` 25 → **33**）：调色板不含纯白、
前后端调色板下标对齐、前端不再自带调色板、接口下发颜色且与 `ass_writer` 一致。

**验证**：八套测试 **242 passed, 0 failed**。

> 顺带用脚本扫了前端的 ID 引用与函数定义一致性：**33 个 ID 全部有定义、
> 23 个自定义函数无缺失调用** —— 这块是干净的。

### 2026-10-01 — 任务失败后界面不再「永远转圈」

做日文视频端到端验证时，代理又抖了、下载失败 —— 但这次失败**暴露出一个真 bug**：

**失败的任务在 `/api/result` 里永远报 `status: "running"`。**

`_jobs[job_id]["status"] = "error"` 明明有记录，但 `/api/result` 只看
`result is None`，把「失败」和「还在跑」混为一谈。后果：

- 错误信息只存在于那条 SSE 流里。**用户一刷新页面，就再也拿不到错误了**
- 前端 `loadResult()` 对非 `done` 的状态直接 `return`，按钮一直停在
  「处理中...」，既看不到错误也无法重试

**修复**：

1. `_run_job` 把失败原因存到 job 上（`_jobs[id]["error"]`）
2. `/api/result` 按真实状态回报 `status: "error"` + 原因
3. 前端 `loadResult()` 遇到 `error` 就调 `showError()`（恢复按钮 + 显示原因）
4. **SSE 重连补发终结事件**：进度队列是**一次性消费**的，客户端断线重连时
   `done`/`error` 已被上一个连接取走，不补发就只能一直收 ping。
   现在 `generate()` 先检查任务是否已结束，是则直接补发终结事件。

**验证**：`_test_job_lifecycle.py` 15 → **24 条**（新增失败态上报、
SSE 重连补发、运行中仍走队列）；八套测试 **234 passed, 0 failed**。

> 这轮的教训：**一个测试没跑通，不一定是环境问题** —— 顺着失败路径看下去，
> 往往能发现真正的缺陷。这次就是「代理抖动导致下载失败」顺带暴露了
> 「失败状态没上报」。

### 2026-10-01 — 翻译失败不再「慢慢等」

排查关键路径时发现：**填错 API Key 是最慢的一种失败**。

`translate_batch()` 对**所有**异常都重试 `MAX_RETRIES` 次并指数退避
（2s、4s），而 `translate()` 的批循环**从不提前中止**。于是一个
500 条字幕的视频（约 34 批）：

```
34 批 × (2s + 4s) ≈ 204 秒
```

用户填错 key，要**干等 3 分多钟**才看到报错。而 401 是确定性的 ——
重试多少次结果都一样。

**修复**（两层）：

1. `_is_retryable()` —— 400 / 401 / 403 / 404 / 422 属于确定性错误，
   **不重试，立刻抛**。408 / 429 / 5xx / 超时 / 断网仍然重试。
2. `EARLY_ABORT_AFTER = 3` —— 每批内部已经重试过 3 次，所以
   **连续 3 批失败 = 连续 9 次 API 调用失败**，这不是抖动而是系统性故障。
   直接中止，并在错误里说明「共几批、还有几批未尝试、最后一个错误是什么」。

**注意保留的边界**：中途恢复**不会**误判 —— 测试里专门有
「前两批失败、之后正常」的用例，确认不会提前中止。

**新增 `_test_translate_resilience.py`（28 条）**，离线（用假引擎）：
重试分类、401 只调用一次且不等待、503 重试到上限、
提前中止且只尝试 3 批、中途恢复不误判、正常路径不受影响。

> 测试踩坑：第一版直接调 `FakeTranslator.translate_batch()` 去验重试，
> 但重试逻辑在**真实**类里，假类没有 —— 测了个寂寞。
> 改成注入假 client 驱动真实 `DeepSeekTranslator` 才是对的。

**验证**：八套测试 **225 passed, 0 failed**；真实 API 调用正常
（「こんにちは、今日はいい天気ですね。」→「你好，今天天气真好啊。」）。

### 2026-10-01 — 任务结果不再无限占用内存

`app._jobs` **只增不减**：每个完成的任务都把它完整的 `result`
（含全部字幕段落、日文原文 + 中文译文）一直留在内存里。
桌面端连续处理多个长视频就是持续增长的占用。

**修复**：新增 `_mark_finished()`，只保留最近 **10** 个已结束任务，
超出后按**结束顺序**淘汰。两个关键点：

- **运行中的任务永不淘汰**（否则进度流会断）
- **按结束顺序而非创建顺序淘汰** —— 先创建但后结束的任务不该被优先丢掉
- 调用点在 `with _lock` **之外**（`threading.Lock` 不可重入，否则死锁）

被淘汰的任务 `/api/result` 返回 **404 JSON**（不是 500、也不是 HTML）。

**新增 `_test_job_lifecycle.py`（15 条）**：上限生效、运行中不淘汰、
按结束顺序淘汰、重复标记不重复计数、淘汰后 404 且为 JSON、未超限时结果完整。

**验证**：七套测试 **197 passed, 0 failed**。

### 2026-10-01 — 给「清理缓存」加上防误删护栏

审查删除路径（项目里**唯一会删文件**的代码）时发现一个**可能导致数据丢失**的缺口：

`_categories()` 用 `OUTPUT_DIR.glob("*")` 找视频文件。如果 `.env` 里把
`OUTPUT_DIR` 误写成**盘根**（`E:/`）或**系统目录**，这个 glob 就会圈进整个盘
/系统目录里的媒体文件 —— 用户点「清理缓存 → 输出目录中的视频」，
**删掉的就是自己的文件**。实测盘根下 `glob("*")` 确实能枚举出全部内容。

**修复**：新增 `cache.is_dangerous_root()`，识别盘根、盘根下的系统目录
（Windows / Program Files / Users / ProgramData …）、以及用户主目录。
命中时：

- `clean_cache()` **直接拒绝执行**并说明怎么改
- `cache_stats()` 带 `warning` 字段，前端提前显示警告横幅
  （免得用户点了「清理」才被拒、还不知道为什么）

**护栏性质**：只挡最危险的几类，正常输出目录（`E:/xxx/output`）完全不受影响 ——
测试里专门断言了「不该拦的别拦」。

**新增 `_test_cache_safety.py`（24 条）**，覆盖：危险目录识别、
误配时必须拒绝、删除不越出允许的根、绝不删 `OUTPUT_DIR` 本身、
`temp` 类别只认 `jsub_` 前缀、参数校验。

**验证**：六套测试 **182 passed, 0 failed**。

### 2026-10-01 — 链接下载的视频不再存两份

排查下载模块时发现的**磁盘浪费 + 静默泄漏**：

- `pipeline.py` 把 URL 下载的视频落到 `OUTPUT_DIR/videos/`
- `app._run_job` 随后又**复制一份**到 `OUTPUT_DIR/`（因为「原视频下载链接」
  和「缓存清理」都只看 `OUTPUT_DIR` 顶层）
- 结果：**每个下载的视频存两份**；而且 `videos/` 那份**永远不在清理范围内**，
  会一直堆着

**顺带发现一个必然触发的崩溃**：复制前的判断写的是
`result["video_path"] != str(dest)` —— 拿 `Path` 和 `str` 比，**恒为 True**。
一旦源和目标变成同一个文件（改成直落 `OUTPUT_DIR` 后必然如此），
就会执行 `shutil.copy2(x, x)` 抛 **SameFileError**。

**修复**：

1. `pipeline.py` 改为直接下载到 `OUTPUT_DIR`（不再用 `videos/` 子目录）
2. 复制逻辑抽成 `app._ensure_video_in_output()`，用 **Path 对 Path** 比较；
   同文件则跳过复制，本地文件/上传文件仍复制一份进 `OUTPUT_DIR`
3. 新增 5 条断言（`_test_path_resolution.py` 28 → 33），
   其中专门覆盖「已在 OUTPUT_DIR 时不得报 SameFileError」

**验证**：实测下载落点从 `output/videos/` 变为 `output/`，
`videos/` 保持空；五套测试 **158 passed, 0 failed**。

> 效果：每个通过链接下载的视频**省下一份完整副本**的磁盘占用。
> 对 1 GB 的视频就是省 1 GB —— 用户的 C 盘一直紧张。

### 2026-09-30 — 链接下载模块（YouTube 需要 cookies）

用户报「通过链接下载的模块调用失败」。

**先分清了「模块坏了」还是「站点要登录」**（两者表现很像，修法完全不同）：

| 站点 | 结果 |
|---|---|
| Bilibili | **正常**（EXE 里实测下载 15.3 MiB 视频 + 5.16 MiB 音频，全流程 `status: done`）|
| Niconico | **正常** |
| YouTube | **失败** —— `Sign in to confirm you're not a bot` / `No video formats found!` |

**排除了两个常见误判**：
- **不是打包问题** —— 检查 PYZ，yt-dlp **1043 个模块（含 972 个 extractor）都在**，
  CA 证书（certifi/cacert.pem）也在。
- **不是版本旧** —— yt-dlp 已是当时最新（2026.8.19），升级无用，这是服务端策略。

**根因**：YouTube 从 2024 年起对**未登录**下载做机器人校验。

**修复（纯增量，不影响原有行为）**：

- `src/config.py` 新增「Downloader (yt-dlp)」段：`YTDLP_COOKIES_FILE`、
  `YTDLP_COOKIES_FROM_BROWSER`、`YTDLP_RETRIES`
- `src/downloader.py` 新增 `_cookie_opts()` / `_looks_like_auth_error()` /
  `_auth_help()`；**未配置 cookies 时 `_cookie_opts()` 返回 `{}`**，
  行为与改动前完全一致
- 认证类失败时不再抛原始英文报错，而是给出**可直接照做的中文步骤**
- 三个 `.env` 都加了配置项与说明

**实测两种「读浏览器 cookie」在本机都不可用**（所以推荐导出 cookies.txt）：

| 浏览器 | 报错 | 原因 |
|---|---|---|
| Chrome | `Failed to decrypt with DPAPI` | App-Bound 加密（yt-dlp #10927）|
| Edge | `Could not copy Chrome cookie database` | cookie 库被占用，`Device or resource busy`（yt-dlp #7271）|

**验证**：`_test_downloader_cookies.py` **30 条全过**（离线）；
五套测试合计 **144 passed, 0 failed**；
新 EXE 实测 YouTube 给出中文指引、Bilibili 仍正常（无回归）。
新 EXE：`dist/jsub-translator.exe`（568 MB，23:38）。

### 2026-09-28（晚）— 模型加载失败的加固

用户报错：`RuntimeError: 所有 Whisper 模型识别失败 — … ConnectTimeout`。

- **确认根因就是上一节的 `MODEL_DIR` 失效**（用户运行的实例启动于 21:56，
  早于 22:23 的修复，所以进程内存里仍是旧路径）。
- **加了启动自检** `transcriber.diagnose_model_dir()`：启动时检查 `MODEL_DIR`
  里到底有没有模型，没有时会在其它位置找并把**正确路径直接打出来**。
  之前这类故障是**静默**的 —— 程序照常启动，直到识别阶段才发现没模型、
  转去联网下载、最后以看不懂的 `ConnectTimeout` 告终。
- `transcriber` 的失败信息也补上了「很可能是 MODEL_DIR 指错了」+ 候选路径。
- `/api/health` 新增 `models` 字段（`ok` / `cached` / `needed` / `detail`）。

**验证**：直接加载模型成功（turbo 3.1s、medium 2.4s，全部走本地磁盘）；
真实识别 57 秒音频 **2/2 模型成功，耗时 39.9s**；四套测试 **114/114**。

> ⚠️ 顺带发现 `nvidia-smi` 报 `Failed to initialize NVML: Unknown Error`
> （NVML 监控接口坏了，**CUDA 计算本身正常** —— 同一时段的识别就是在 CUDA 上跑通的）。
> 这是已知的 Windows 现象，通常需重启才恢复。**不影响应用运行。**

### 2026-09-28 — 项目迁移 + 路径修复 + 文档

- **项目从 `E:\大学\AI\jsub-translator-exe` 移到 `E:\Study\AI\jsub-translator-exe`**
  （工作区也从 `E:\大学` 改为 `E:\Study`）。
- 🔴 **修复迁移导致的真实故障**：`.env` 和 `dist\.env` 里的
  `MODEL_DIR` 仍指向旧路径 `E:/大学/.../models`（现为空目录），
  导致 `_local_model_sizes()` 返回空 —— **3.5 GB 模型被无视，程序准备重新下载**。
  已改为 `E:/Study/AI/jsub-translator-exe/models`，三个模型均本地命中。
  备份：`_backups/env.bak-pathfix-20260928`、`_backups/dist-env.bak-pathfix-20260928`。
- 新增面向 agent 的文档：`AGENTS.md` + `docs/{ARCHITECTURE,DEVELOPMENT,TROUBLESHOOTING,STATE}.md`。
- 同步修正 `README.md` 里的旧路径（`.venv` 引用），并补了「项目位置变更后要检查什么」一节。

### 2026-09-19 — 「请求失败」修复 + 对齐修复

- **修复「请求失败」**：根因是 `dist/.env` 的模板占位符
  （`sk-your-deepseek-key-here`）在 `load_dotenv(override=False)` 语义下
  **永久挡住**了真 key；叠加 Flask 把异常渲染成 HTML，前端只看到
  `Unexpected token '<'`。修了加载顺序、占位符识别、全局 JSON 错误处理、
  前端 `readJson()`，并新增 `/api/health` 自检端点。
- **修复字幕对齐**：说话人样式（`S00`…）原本硬编码 `Alignment=2`（底部），
  而日文样式是 8（顶部），导致开启说话人后**所有字幕掉到画面下方**。
  已抽成 `SPEAKER_ALIGNMENT = 8` / `SPEAKER_MARGIN_V = 20` 常量；
  用 `tools/fix_ass_alignment.py`（当时在根目录）修复了 10 个已有字幕文件（60 处样式，
  逐行 diff 确认 0 个非 `Style:` 行变化）。
- 新增 3 个测试文件（`_test_ass_alignment` / `_test_path_resolution` /
  `_test_api_key_resolution`）。
- 构建守卫：`build.bat` 新增 `[2b/4]`，防止 `src/` 下的备份被误打包。

### 更早

- 说话人识别接入（sherpa-onnx 路线），聚类阈值定为 0.92。
- 多模型并行 ASR + LLM 融合。
- 缓存清理板块 + 输出目录锚定修复。

---

## 5. 待办 / 已知问题

### 待处理

| # | 事项 | 优先级 | 说明 |
|---|---|---|---|
| 1 | `dist\` 下 1.6 GB 的 EXE 备份 | 低 | 用户此前未确认是否清理 |
| 2 | `dist\.env.bak-keyfix` 仍在 `dist\` 里 | 低 | 无害，但可移到 `_backups/` |
| 3 | `E:\大学\AI\jsub-translator-exe\` 空壳目录 | 低 | 迁移后残留（只剩空的 `models/`）|

### 已知限制（设计如此，不是 bug）

| 限制 | 说明 |
|---|---|
| 说话人识别走 CPU | 约 0.25x 实时（1 小时视频 ≈ 13 分钟）。为避开 torch 的 cuBLAS 冲突而有意选择 |
| CUDA 下多模型串行 | 并发加载 CUDA dll 会死锁（见 TROUBLESHOOTING §4） |
| 词级时间戳可能跳过切分 | `large-v3-turbo` 的 DTW 较粗，对齐率 < 95% 自动降级为整句归属 |
| FFmpeg 无法打包进 EXE | 必须用户自行安装（便携版里随包带了 `ffmpeg/`） |
| **YouTube 需要 cookies** | 服务端机器人校验，与程序无关。Bilibili/Niconico 不需要。见 TROUBLESHOOTING §16 |
| 删除不可恢复 | 缓存清理是直接删除，不是移到回收站 |

---

## 6. 验证记录

最近一次全量验证（2026-09-28，迁移后）：

| 项目 | 结果 |
|---|---|
| `_test_ass_alignment.py` | 25 / 25 ✓ |
| `_test_path_resolution.py` | 33 / 33 ✓ |
| `_test_diarization_logic.py` | 24 / 24 ✓ |
| `_test_api_key_resolution.py` | 37 / 37 ✓ |
| `_test_downloader_cookies.py` | 39 / 39 ✓ |
| 模型本地解析 | small / medium / large-v3-turbo 全部本地命中 ✓ |
| **模型实际加载** | turbo 3.1s、medium 2.4s，**全程本地磁盘，无联网** ✓ |
| **真实语音识别** | 57 秒音频，**2/2 模型成功**，耗时 39.9s ✓ |
| **链接下载（EXE）** | Bilibili **成功**（下载 + 转写 + 翻译 + 出 4 个 .ass）✓ |
| **YouTube（EXE）** | 给出**可操作中文指引**（需 cookies），非原始英文报错 ✓ |
| `/api/health` | `models.ok = true`、`output_dir_writable = true` ✓ |
| `OUTPUT_DIR` | `E:\Study\AI\jsub-translator-exe\output` ✓ |

> **注意**：打包版 EXE 是 **2026-09-19** 构建的，**早于本次路径修复**。
> 但 `MODEL_DIR` 是从 `dist\.env` **在运行时读取**的，不打包进 EXE，
> 所以本次修复**不需要重新构建**。
>
> ⚠️ **但必须重启正在运行的实例。** 环境变量在进程启动时就已载入内存，
> 改 `.env` 不会影响已经在跑的进程。实测：2026-09-28 21:56 启动的那个实例
> （早于 22:23 的修复）`/api/health` 仍回报旧路径
> `model_dir: E:\大学\AI\jsub-translator-exe\models`。
> **确认方式**：看 `/api/health` 的 `model_dir` 是否已是 `E:\Study\...`。

---

## 7. 怎么更新这份文档

完成实质工作后，至少更新：

1. **顶部「最后更新」日期**
2. **§1 一句话状态**（如果整体状态变了）
3. **§3 最近变更**（新条目加在最上面，保留历史）
4. **§4 待办**（划掉做完的，加新发现的）
5. **§5 验证记录**（如果跑过测试）

**要记录的是「容易随时间失真的事实」**：路径、版本、体积、产物位置、
以及「为什么当初这么选」的理由。**不要**记录临时的搜索过程或中间产物。

**过时信息要删掉，不要留着** —— 一份写着旧路径的文档比没有文档更危险
（本项目已经因为写死的旧路径出过一次故障，见 §3）。
