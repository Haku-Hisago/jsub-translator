# 排查手册

> **症状 → 原因 → 修复。** 按「你看到什么」查找，不要按「你以为哪里错了」查找。
> 配套：[`../AGENTS.md`](../AGENTS.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md)、[`DEVELOPMENT.md`](DEVELOPMENT.md)
> 最后更新：2026-09-28

---

## 快速索引

| 你看到的现象 | 去看 |
|---|---|
| 网页只显示「请求失败: ...」，没有任何有用信息 | [§1](#1-网页只显示请求失败) |
| `configured: true` 但仍 401 / 翻译全失败 | [§2](#2-key-看着配好了但还是-401) |
| 字幕跑到画面**下方**了（本该在上方） | [§3](#3-字幕位置不对掉了-8-的对齐) |
| 进度**卡在「1/2 模型」**，CPU/GPU 都 0% | [§4](#4-进度卡在12-模型cpu--gpu-全-0) |
| `Library cublas64_12.dll is not found` | [§5](#5-cublas64_12dll-is-not-found) |
| 字幕出现 `テテテテテテ...` 复读 | [§6](#6-复读幻觉) |
| 「打开输出文件夹」打开的目录是空的/不对 | [§7](#7-打开输出文件夹打开错了地方) |
| **移动/重命名项目后**，模型好像丢了、要重新下载 | [§8](#8-移动项目后模型失效) |
| `所有 Whisper 模型识别失败 — … ConnectTimeout` | [§8](#8-移动项目后模型失效) |
| `.bat` 报 `'-' 不是内部或外部命令` | [§9](#9-bat-脚本报怪错) |
| C 盘空间莫名减少 | [§10](#10-c-盘空间被吃掉) |
| 测打包版结果和源码版对不上 | [§11](#11-测打包版时结论诡异) |
| 构建 6 秒就结束 | [§12](#12-构建秒退) |
| EXE 体积异常增大 | [§13](#13-exe-体积变大) |
| 说话人数量明显不对 | [§14](#14-说话人数量不对) |
| 说话人识别整体不生效 | [§15](#15-说话人识别完全不工作) |
| **通过链接下载失败** / YouTube 报 bot 校验 | [§16](#16-通过链接下载失败youtube-要-cookies) |
| 下载报 `No video formats found!` | [§16](#16-通过链接下载失败youtube-要-cookies) |
| **双击两次开了两个实例**，任务/缓存对不上 | [§17](#17-双击两次开了两个实例) |

---

## 1. 网页只显示「请求失败」

**现象**：界面上只有一句 `请求失败: ...`，后面的内容毫无信息量
（常见 `Unexpected token '<'`）。

**第一步永远是打开自检端点**：

```
http://127.0.0.1:7860/api/health
```

```json
{
  "env_files_loaded": ["...\\dist\\.env"],
  "api_keys": { "deepseek": { "configured": true, "prefix": "sk-a3f8a..." } },
  "output_dir_writable": true,
  "diarization": { "available": true, "reason": "sherpa-onnx" }
}
```

**根本原因（两个叠加）**：

**(a) `load_dotenv` 默认 `override=False` —— 后加载者才优先。**

早期 `config.py` 先加载 EXE 目录的 `.env`、后加载项目根 `.env`，
而 `dist/.env` 是一份**从没改过的 `.env.example` 副本**：

```
DEEPSEEK_API_KEY=sk-your-deepseek-key-here     ← 占位符
```

于是占位符**永久挡住**了项目根 `.env` 里的真 key。
（现已反转顺序，并加 `_looks_like_placeholder()` 把占位符当作未配置。）

**(b) Flask 把未捕获异常渲染成 HTML。**

前端 `await resp.json()` 撞上 `<!doctype html>` 抛
`Unexpected token '<'`，`catch` 报的是**这个语法错误**，真正的异常被完全吞掉。

**修复（已实施，改动新代码时别退化）**：

| 位置 | 措施 |
|---|---|
| `config._load_env_files()` | 顺序改为「打包副本 → EXE 同目录」，**后者优先** |
| `config._looks_like_placeholder()` | 占位符归一化为 `""` → `validate_config()` 给可读提示 |
| `app._json_error()` + `@app.errorhandler(Exception)` | `/api/*` 一律返回 JSON；非 API 路由保持 HTML |
| `index.html` 的 `readJson(resp)` | 非 JSON 响应也带出 HTTP 状态码 + 响应片段 |
| `app.api_start()` | **入队前**就 `validate_config()`，配置问题立刻暴露 |

**排查「为什么没有有用信息」时**，检查这三处是否还在：
`app.py` 有 `@app.errorhandler`、路由用 `_json_error()`、
前端用 `readJson()`（`grep -n "resp.json()" web/templates/index.html`
应该只剩 `openOutputFolder` 里那处**被 try 包住**的）。

---

## 2. Key 看着配好了但还是 401

先独立验证 key 本身（**不要通过应用猜**）：

```python
import json, urllib.request
req = urllib.request.Request(
    "https://api.deepseek.com/chat/completions",
    data=json.dumps({"model": "deepseek-chat", "max_tokens": 5,
                     "messages": [{"role": "user", "content": "ping"}]}).encode(),
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    print("HTTP", r.status)     # 200 → key 没问题，是配置没送到应用
```

| 结果 | 结论 |
|---|---|
| HTTP 200 | key 有效 → 问题是**配置没送到应用**，回到 [§1](#1-网页只显示请求失败) |
| 401 / 402 | key 失效或余额不足 → 去平台重新生成/充值 |

`/api/health` 里 `reason: "仍是模板占位符，请填入真实 Key"` 就是这个坑的专用提示。

> ⚠️ **不要把没改过的 `.env.example` 直接复制成 `.env`。**

---

## 3. 字幕位置不对（掉了 8 的对齐）

**现象**：原本字幕在**画面上方**，开启说话人识别后**全部跑到下方**。
手动在 Aegisub 改回来，下次运行又被覆盖。

**原因**：`ass_writer.py` 里，说话人样式（`S00`/`S01`…）早期是照抄
`STYLE_DEFAULT` 的尾部字段（`Alignment=2`，底部），而日文样式是
`Alignment=8`（顶部）。**一旦检测到说话人，所有 Dialogue 行都改引用 `S0x`**，
于是整体跳位。

**修复**：已抽成常量，改样式**只改这里**：

```python
# src/ass_writer.py
SPEAKER_ALIGNMENT = 8      # 必须与 STYLE_JAPANESE 的 8 一致
SPEAKER_MARGIN_V  = 20     # 与日文样式一致，避免贴边
```

**ASS 对齐是 numpad 布局**：`1/2/3` 底部、`4/5/6` 中部、`7/8/9` 顶部。
在 `Style:` 行里是**下标 18**（0-based），`MarginV` 是下标 21，共 23 个字段。

**修复历史遗留文件**：

```bash
.venv/Scripts/python.exe tools/fix_ass_alignment.py --dir dist/output          # 预演
.venv/Scripts/python.exe tools/fix_ass_alignment.py --dir dist/output --apply  # 写入
```

只重写名字匹配 `S\d+` 的 `Style:` 行，备份为 `<文件>.ass.bak-align`。
**改完必须逐行 diff 确认 0 个非 `Style:` 行变化** —— 时间轴、文本、颜色都不能动。

**回归测试**：`_test_ass_alignment.py`（25 条）逐字段比对说话人样式与日文样式。

---

## 4. 进度卡在「1/2 模型」，CPU / GPU 全 0%

**现象**：进度停在 `语音识别中 (0/2 成功)`，进程一直挂着，
`nvidia-smi` 和任务管理器里 CPU/GPU 都是 0%。

**原因**：**CUDA 下并发加载多个模型会让 Windows 加载器死锁** ——
两个模型同时抢着加载同一批 CUDA dll。

**修复**：`transcriber.py` 已强制：**CUDA 下串行，CPU 下并行**。
串行只损失一点速度（GPU 本来就是瓶颈），换来稳定。

**不要**为了提速把 CUDA 路径改回并行。

---

## 5. `cublas64_12.dll is not found`

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

**原因**：CTranslate2 **运行时动态加载** cuBLAS，而它的 wheel
**只自带 cuDNN，不带 cuBLAS**。

**修复**：三个 DLL 必须落在 `site-packages/ctranslate2/`（与 `ctranslate2.dll` 同级）：

```
cublas64_12.dll   (~105 MB)
cublasLt64_12.dll (~530 MB)
cudart64_12.dll   (~0.5 MB)
```

```bash
pip install nvidia-cublas-cu12
# 然后手动从 site-packages/nvidia/cublas/bin 复制到 site-packages/ctranslate2/
```

项目根的 `_cuda_dlls/` 有本地备份。`build.bat` 的 `[2/4]` 会检查。

**打包后**这三个 DLL 必须在 EXE 内的 `ctranslate2/` 目录里 —— 这是已验证的布局。
`spec` 里有两段逻辑：先看 `ctranslate2/` 里有没有（`collect_all` 自动带走），
没有再回退到 `nvidia-cublas-cu12` wheel。

**临时绕过**：`.env` 改 `WHISPER_DEVICE=cpu` + `WHISPER_COMPUTE_TYPE=int8`。

---

## 6. 复读幻觉

**现象**：整段输出 `テテテテテテテテ...` 之类的重复。

**原因**：Whisper 在难段上陷入复读死循环。

**修复（两者必须同时设置，只改一个无效）**：

```python
compression_ratio_threshold = 2.4          # Whisper 默认值
temperature = [0.0, 0.2, ..., 1.0]         # 温度回退链
```

> 实测：关掉守卫时 `medium` 对一段 39 秒音频只输出 **1 条 39 秒复读**；
> 开启后恢复为 **8 条正常字幕**，与 `large-v3-turbo` 的 10 条互相印证 ——
> 参考模型这才真正起到交叉验证作用。

**保持关闭**：`log_prob_threshold` 和 `no_speech_threshold` ——
这两项对音量偏低的日语过于苛刻，静音判断交给 Silero VAD。

---

## 7. 「打开输出文件夹」打开错了地方

**现象**：打开的目录里没有自己的字幕；或报 `mkdir` 失败。

**原因**：`OUTPUT_DIR=./output` 这种相对路径被 `.resolve()`，**跟着进程 CWD 走**：

| 启动方式 | CWD | 解析到 |
|---|---|---|
| `run.bat` | `dist\` | `dist\output\` ← 成品在这 |
| 双击 EXE | Windows 决定 | 不确定 |
| 从项目根跑 | 项目根 | `output\`（空的）|

CWD 若是只读位置（如 `C:\Windows`），`mkdir` 还会失败。

**修复**：路径一律通过 `config._anchor_dir()` 解析
（打包版 = EXE 所在目录；源码版 = 项目根目录）。
**新增路径时不要用 `Path(相对).resolve()`。**

**回归测试**：`_test_path_resolution.py` 从 4 个不同 CWD 验证解析结果一致。

---

## 8. 移动项目后模型失效

**现象**：项目移动/重命名后，程序好像找不到 Whisper 模型，准备重新下载
（尽管 3.5 GB 模型就在新位置的 `models/` 里）。

**原因**：`.env` 里写死了绝对路径：

```
MODEL_DIR=E:/大学/AI/jsub-translator-exe/models      ← 项目已移到 E:\Study
```

`_resolve_dir()` 对绝对路径**原样使用**，所以指向了一个空目录。
`transcriber._resolve_model_path()` 找不到本地模型 → 返回**裸模型名**
→ 触发联网下载。

**诊断**：

```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
from src import config, transcriber
print('MODEL_DIR =', config.MODEL_DIR, '| 存在:', config.MODEL_DIR.exists())
print('本地可用模型:', transcriber._local_model_sizes() or '(无 —— 说明路径不对)')
"
```

**启动时就会报出来（2026-09-28 起）**：

```
============================================================
  [Whisper 模型] 就绪 — E:\Study\AI\jsub-translator-exe\models（可用：small, medium, large-v3-turbo）
============================================================
```

路径配错时改成：

```
  [Whisper 模型] 有问题 — E:\大学\AI\jsub-translator-exe\models 下没有找到任何 Whisper 模型
  -> 但在这些位置找到了模型，很可能是 .env 里的 MODEL_DIR 指错了：
       E:\Study\AI\jsub-translator-exe\models
```

`/api/health` 的 `models` 字段也会回报同样的信息：

```json
"models": { "ok": true, "cached": ["small","medium","large-v3-turbo"],
            "needed": ["large-v3-turbo","medium"], "detail": "..." }
```

> 为什么要加这道检查：这类故障**不报错、只是静默降级去联网下载**，
> 用户要等很久才拿到一个看不懂的 `ConnectTimeout`。
> 启动时一句话就能省掉一整轮等待。

**修复**：把 `.env` 和 `dist\.env` 里的 `MODEL_DIR` 改成新路径
（或用相对路径 `models`，它会锚定到项目根）。

```bash
grep -rn "旧路径关键字" --include="*.env" --include="*.py" --include="*.md" .
```

> 这条坑已经踩过一次（`E:\大学` → `E:\Study`）。
> **移动项目后，第一件事就是搜旧路径。** 还要检查 `README.md` 里的路径引用。

> ⚠️ **改完 `.env` 必须重启应用。** 环境变量只在进程启动时读取一次，
> 改文件对已经在跑的进程无效。判断方法：看 `/api/health` 的 `model_dir`
> 是否已是新路径。实测踩过：21:56 启动的实例，22:23 改了 `.env`，
> 22:29 依然报旧路径。

---

## 9. `.bat` 脚本报怪错

**现象**：

```
'-' 不是内部或外部命令，也不是可运行的程序
'rint' 不是内部或外部命令
```

看起来像脚本内容坏了，其实是**编码**问题：cmd 按代码页 936（GBK）解析，
而文件是 UTF-8，中文字节被切碎，连带把命令也切了。

**原因**：`Write`/`Edit` 工具默认写 UTF-8。直接编辑 GBK 的 `.bat`
会产生**混合编码**（旧字节 GBK + 新字节 UTF-8）。

**诊断**：

```python
from pathlib import Path
raw = Path('build.bat').read_bytes()
for enc in ('gbk', 'utf-8'):
    try: raw.decode(enc); print(enc, 'ok')
    except Exception as e: print(enc, 'FAILS:', e)
# utf-8 FAILS 而 gbk ok → 混合编码（或纯 GBK），需统一
```

**修复**：

```python
t = raw.decode('gbk').replace('\r\n', '\n').replace('\n', '\r\n')
Path('build.bat').write_bytes(t.encode('gbk'))
```

校验：CRLF > 0、裸 LF == 0、中文按 GBK 正常。

**另外两点**：

- **不要加 `chcp 65001`** —— 会让 GBK 字节被当 UTF-8 读，重新乱码。
- **Git Bash 下用 `./build.bat`**；`cmd //c build.bat` **不会真正执行**脚本，
  只打印横幅（看起来像脚本坏了，其实是调用方式错了）。

---

## 10. C 盘空间被吃掉

**来源**：onefile EXE 每次启动往 `%TEMP%`（C 盘）解包约 **850 MB**；
程序提取的音频也落在那里。

**预防**：**用 `dist\run.bat` 启动** —— 它把 `TEMP`/`TMP` 重定向到
`dist\.runtime\`（E 盘）。

**清理**：

```bash
ls -d "$TEMP"/_MEI* "$TEMP"/jsub_* 2>/dev/null
# 确认没有 jsub/python 进程持有（否则删不掉）
rm -rf "$TEMP"/_MEI* "$TEMP"/jsub_*
```

正常退出会自动清理 `_MEI*`；**被强制结束（任务管理器）会残留**。
实测一次残留过 910 MB × 2。

**关于强杀进程**：强杀持有 CUDA context 的进程会让 `nvidia-smi` 报
`Failed to initialize NVML: Unknown Error`，需重启才能恢复。**尽量避免强杀。**

---

## 11. 测打包版时结论诡异

**现象**：测 EXE 时 `/api/health` 显示的是**源码版**的配置，
或行为和预期完全不符。

**原因**：**端口 7860 被另一个实例占着。** 两个监听者时，`curl` 打到
**先绑定的那个**，你以为是 EXE，其实是旧的源码实例。

（为什么 Windows 上两个进程能绑同一端口、用户日常怎么撞上 —— 见 [§17](#17-双击两次开了两个实例)。）

**这是正确性问题，不是小事** —— 它会让所有结论静默失效。

**修复**：启动前先清端口。

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

**判据**：`/api/health` 的 `env_files_loaded` 必须指向 **`dist\.env`**：

```
["...\\dist\\.env"]        ← 打包版，正确
["...\\<项目根>\\.env"]    ← 你打到源码版了
```

---

## 12. 构建秒退

**现象**：`build.bat` 几秒就结束。

**原因**：几乎总是 **spec 解析失败**。典型例子：

```
AttributeError: module 'os' has no attribute 'basename'
```

—— 写成了 `_os.basename()`，而 `_os` 是 `os` 不是 `os.path`。

**规则**：

- **正常构建约 2–3 分钟。6 秒结束 = 失败。**
- **必须看退出码**：`./build.bat > _build_run.log 2>&1; echo "exit=$?"`
- **不要把构建输出 pipe 给 `grep`** —— GBK 字节会让 grep 报
  `Binary file (standard input) matches` 并把 traceback 藏起来。
  **重定向到文件**，再用 `grep -a` 看。

---

## 13. EXE 体积变大

**现象**：EXE 比预期大，或归档里出现奇怪文件。

**原因**：`spec` 的 `datas` 写的是 **`('src', 'src')`** ——
**整个目录**都会被打包。在 `src/` 下留了 `config.py.bak-fix` 之类的备份，
就会被一起打进去。

**检查**：

```bash
.venv/Scripts/python.exe -c "
from PyInstaller.archive.readers import CArchiveReader
r = CArchiveReader('dist/jsub-translator.exe')
names = list(r.toc.keys() if hasattr(r.toc,'keys') else [n for n,_ in r.toc])
print('文件数:', len(names))
print('备份残留:', [n for n in names if '.bak' in n or '.orig' in n] or '[] ✓')
"
```

**修复**：备份移到 `_backups/`（不在 `datas` 范围内）。
`build.bat` 的 `[2b/4]` 步骤已加守卫，会直接报错并提示。

---

## 14. 说话人数量不对

**现象**：角色被拆得太碎（一个人被当成好几个），或不同角色被合并成一个。

**原因**：自动聚类阈值 `DIARIZE_CLUSTER_THRESHOLD`。

**调法**：

| 症状 | 动作 |
|---|---|
| 角色被拆太碎 | **往上调**（0.92 → 0.96）|
| 不同角色被合并 | **往下调**（0.92 → 0.88）|
| **已知人数** | **直接在网页「说话人数量」填** —— 比调阈值准得多 |

实测（官方 4 人测试音频，turns 恒为 10）：

| 阈值 | 0.5 | 0.7 | 0.80–0.86 | **0.88–0.96** | 1.0 |
|---|---|---|---|---|---|
| 说话人 | 7 | 5 | 5 | **4 ✓** | 3 |

默认 **0.92** 是**有意取平台区正中** —— 边缘值（如 0.8）换段音频就翻成 5 人。

**相关**：一条字幕横跨两人时靠**词级时间戳**切分。`large-v3-turbo` 只有 4 层
decoder，词级时间戳较粗，所以 `_word_timestamps_reliable()` 会先校验
「词序列能否还原原文」，**对齐率 < 95% 就自动跳过切分**（只按整句归属），
并在进度里说明原因。**这是有意的降级，不是 bug。**

---

## 15. 说话人识别完全不工作

**先看启动日志**：

```
============================================================
  [说话人识别] 可用 — sherpa-onnx
============================================================
```

不可用时也会**明确报出原因**（不会静默跳过）：

| 提示 | 修复 |
|---|---|
| `sherpa-onnx 未安装` | `pip install sherpa-onnx` |
| `缺少说话人模型文件：xxx` | 模型在 `<root>\models\`；检查 `DIARIZE_SEG_MODEL` / `DIARIZE_EMB_MODEL` |
| `没有可用的说话人识别后端` | 检查 `DIARIZE_BACKEND`（`sherpa` / `auto` / `none`）|

**自检**：`/api/health` 的 `diarization` 字段。

**为什么不用 pyannote.audio**：它要 torch 全家桶（约 3 GB）和 HF Token，
而且 **torch 会再带一套 cuBLAS 12**，与本项目手工放进
`ctranslate2/` 的那套**撞车** —— 正是 [§4](#4-进度卡在12-模型cpu--gpu-全-0)
死锁的根因。sherpa-onnx 走 ONNX Runtime CPU，**完全不碰 CUDA 加载路径**。

代价：说话人识别走 CPU，实测约 **0.25x 实时**（1 小时视频约 13 分钟），
不占显存，不和 Whisper 抢那 8 GB。

---

## 16. 通过链接下载失败（YouTube 要 cookies）

**现象**：填了视频链接，任务失败。日志里是：

```
ERROR: [youtube] <id>: Sign in to confirm you're not a bot
ERROR: [youtube] <id>: No video formats found!
```

**原因**：YouTube 从 2024 年起对**未登录**的下载做机器人校验，匿名请求直接拒绝。
**这不是程序坏了** —— Bilibili / Niconico 等站点照常可用。

> 排查时先分清是「模块坏了」还是「站点要登录」，两者表现很像但修法完全不同：
> ```bash
> # 用一个不受影响的站点测一下。能下 → 模块没问题，是站点要 cookies
> .venv/Scripts/python.exe -c "
> from src.downloader import download_video
> print(download_video('https://www.bilibili.com/video/BV1GJ411x7h7'))
> "
> ```
> 实测（2026-09-30）：**Bilibili 正常**（打包版也实测下载成功），
> **只有 YouTube 被拒**。另外注意 yt-dlp **已经是当时最新版**（2026.8.19），
> 升级并不能解决 —— 这是服务端的策略，不是客户端版本问题。
>
> ⚠️ **但这个对照法有个前提：网络本身要可靠。** 本机访问境外站点走代理，
> 代理不稳时会报出各种**看起来像程序 bug** 的错误 —— 实测遇到过
> `SSL: UNEXPECTED_EOF_WHILE_READING` 和 `[WinError 10061] 由于目标计算机
> 积极拒绝`，同一个 URL 隔几分钟就换一种。**换一个站点/过一会儿再试**，
> 若错误随之变化，那就是网络问题，不是代码问题。
>
> 🔴 **最容易误判的一种情况：后台启动的进程没继承代理环境变量。**
> 实测踩过：源码版跑 Niconico 正常，打包版一直 SSL 报错，看着像
> 「EXE 的 SSL 有问题」；实际是启动 EXE 时**没把 `http_proxy` 传进去**，
> 显式带上代理后立刻正常（0 报错）。
> 排查前先确认：
> ```bash
> # 源码与打包版必须在同一套代理环境下对比
> http_proxy=... https_proxy=... ./jsub-translator.exe
> ```
> 否则你会去修一个不存在的问题。

**修复：给 yt-dlp 配 cookies。** `.env` 里二选一：

| 方式 | 配置 | 说明 |
|---|---|---|
| **1（推荐）** | `YTDLP_COOKIES_FILE=cookies.txt` | 导出 Netscape 格式 cookie 文件，**最稳** |
| 2 | `YTDLP_COOKIES_FROM_BROWSER=edge` | 直接读浏览器 cookie 库，**经常失败**，见下 |

方式 1 步骤：

1. 浏览器装扩展 **“Get cookies.txt LOCALLY”**
2. 打开并登录该站点，用扩展导出 `cookies.txt`
3. 把文件放到程序目录下，`.env` 写 `YTDLP_COOKIES_FILE=cookies.txt`
4. **重启程序**

**方式 2 为什么经常失败**（实测两种浏览器都不行）：

| 浏览器 | 报错 | 原因 |
|---|---|---|
| Chrome | `Failed to decrypt with DPAPI` | Chrome 127+ 的 App-Bound 加密，只有 Chrome 自己能解密（yt-dlp issue #10927）|
| Edge | `Could not copy Chrome cookie database` | cookie 库被占用（`Device or resource busy`），即便任务管理器里看不到 msedge.exe（yt-dlp issue #7271）|

**所以优先用方式 1。**

**配完怎么确认生效**（不用真跑一次任务）：

```
http://127.0.0.1:7860/api/health
```

看 `download_cookies` 字段：

```json
"download_cookies": {
  "configured": true, "source": "file",
  "path": "E:\\Study\\AI\\jsub-translator-exe\\cookies.txt",
  "exists": true, "cookie_count": 42,
  "hint": "已配置。若仍被拒，多半是 cookies 已过期，请重新导出。"
}
```

| 现象 | 含义 |
|---|---|
| `configured: false` | 还没配（`.env` 里两项都空）|
| `exists: false` | 路径不对 —— 相对路径是基于**程序所在目录** |
| `cookie_count: 0` | 文件不是 Netscape 格式，或导出的是空文件 |
| `cookie_count > 0` 但仍被拒 | cookies 过期，重新导出 |

**留空 = 维持原行为**：不配置 cookies 时不会发送任何 cookie，
Bilibili 等站点不受影响（`_cookie_opts()` 返回空 dict）。

**相关代码**：`src/downloader.py` 的 `_cookie_opts()` / `_looks_like_auth_error()` /
`_auth_help()` / `cookie_status()`；配置在 `src/config.py` 的「Downloader (yt-dlp)」段。
失败时程序会直接给出上面这套操作指引，不用回来翻文档。

**回归测试**：`_test_downloader_cookies.py`（39 条，离线，不联网）。

---

## 17. 双击两次，开了两个实例

**现象**：提交的任务在页面上看不到；缓存统计和刚才不一样；
或者两个页面各自显示不同的任务列表。

**原因**：**Windows 下同一个端口能被两个进程同时绑定。**

这不是猜测，是实测的：

```python
a = socket.socket(); a.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
a.bind(("127.0.0.1", 7873)); a.listen(1)
b = socket.socket(); b.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
b.bind(("127.0.0.1", 7873)); b.listen(1)   # ← 也成功，不报错
```

Werkzeug 默认给服务端 socket 设 `SO_REUSEADDR`，所以在 Windows 上
**双击两次 EXE 不会像 Unix 那样第二个报「端口被占用」退出**，
而是两个进程都在跑、各自有独立的任务表和缓存状态。
浏览器打开的是**先绑定的那个**，于是你看到的和刚提交的对不上。

> 这也是 [§11](#11-测打包版时结论诡异) 那条「端口有两个监听者」的**根因** ——
> §11 讲的是它如何让测试结论失效，这里讲的是用户日常怎么撞上它。

**判断有没有多个实例**：

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique
```

返回 **1 个** PID 才正常；返回 2 个以上就是开了多个实例。

**处理**：全部停掉再启动一个。

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

**另外注意 GPU**：两个实例各自加载 Whisper 会争同一块显存。
项目里已经记录过「同一 CUDA 设备上两个 CTranslate2 实例会让 Windows
加载器死锁、进程挂在 0% CPU / 0% GPU」的现象（见 §4），
所以**不要同时跑两个实例处理视频**。

---

## 附：改动前的自检清单

改完代码，发布前过一遍：

- [ ] 9 个测试文件全绿（**326 passed, 0 failed**）
- [ ] `grep -n "resp.json()" web/templates/index.html` 只剩被 try 包住的那处
- [ ] 新增的 `/api/*` 路由都走 `_json_error()`
- [ ] `src/` 下没有备份文件
- [ ] `.bat` 是 GBK + CRLF
- [ ] `.env` 与 `dist\.env` 同步（尤其 `MODEL_DIR`）
- [ ] 构建退出码为 0，且耗时是分钟级不是秒级
- [ ] 归档里没有 `.bak`
- [ ] **实际启动打包版**跑一次端到端，`env_files_loaded` 指向 `dist\.env`
- [ ] C 盘没有新增 `_MEI*` / `jsub_*` 残留
