# AGENTS.md — 项目导航与操作约定

> **这是本项目的唯一入口文档。** 任何 agent（或新加入的人）先读这一份。
> 需要深入时再按下面的「深入文档」跳转，不必一上来读全部源码。
>
> 最后更新：2026-09-28

---

## 1. 这是什么

**jsub-translator** —— 把日语视频变成中文字幕的桌面工具。

流程：下载/读取视频 → 提取音频 → **多个 Whisper 模型并行识别** → LLM 交叉比对融合
→ **说话人分离** → LLM 翻译 → LLM 连贯性校对 → 输出 ASS 字幕。

形态：本地 Flask 网页应用，用 PyInstaller 打成**单文件 EXE**，双击启动、
浏览器自动打开 `http://127.0.0.1:7860`。

---

## 2. 硬规则（违反会出事）

| # | 规则 | 原因 |
|---|------|------|
| **R1** | **禁止往 C 盘写任何东西。** 用 `dist\run.bat` 启动，不要直接双击 EXE。 | onefile EXE 每次启动往 `%TEMP%`（在 C 盘）解包约 850 MB。用户 C 盘长期 83%+ 占用。 |
| **R2** | **`.bat` 文件必须是 GBK + CRLF 编码**，且**不要加** `chcp 65001`。 | 中文 Windows 代码页是 936。UTF-8 字节会被 cmd 切碎，报 `'-' 不是内部或外部命令`。 |
| **R3** | **`.env` 里不要写死绝对路径**（除非有意为之）。 | 项目被移动过一次（`E:\大学` → `E:\Study`），写死的 `MODEL_DIR` 直接失效，3.5 GB 模型被无视、重新下载。 |
| **R4** | **任何新增的 `/api/*` 路由必须返回 JSON。** 用 `_json_error()`。 | Flask 默认把异常渲染成 HTML，前端 `resp.json()` 抛 `Unexpected token '<'`，真正的错误被完全吞掉。 |
| **R5** | **`src/` 目录里不要放备份/临时文件。** | spec 的 `datas` 写的是 `('src','src')`，整个目录会被打进 EXE。备份放 `_backups/`。 |
| **R6** | **改字幕样式前先读 `docs/TROUBLESHOOTING.md` 的「对齐」一节。** | 说话人样式的 `Alignment` 必须与日文样式一致，否则字幕整体跳位。 |
| **R7** | **不要在测试打包版时让别的进程占着 7860。** | 两个监听者时 `curl` 打到先绑定的那个，会静默得出错误结论。 |

---

## 3. 快速事实

| 项 | 值 |
|---|---|
| 项目根 | `E:\Study\AI\jsub-translator-exe` |
| Python 环境 | `.venv\Scripts\python.exe`（Python 3.13.14，**在项目内，不要用 C 盘的**） |
| 源码入口 | `app.py`（Flask 应用） |
| 打包产物 | `dist\jsub-translator.exe`（约 568 MB） |
| 启动方式 | `dist\run.bat`（**推荐**，TEMP 重定向到 E 盘） |
| 端口 | `7860`（可用 `PORT` 环境变量覆盖） |
| 输出目录（源码版） | `<root>\output` |
| 输出目录（EXE 版） | `<root>\dist\output` |
| 模型目录 | `<root>\models`（**3.5 GB**：small / medium / large-v3-turbo） |
| 自检端点 | `http://127.0.0.1:7860/api/health` |
| 测试 | 10 个 `_test_*.py`，共 **399** 条断言，必须全绿 |

---

## 4. 文件地图

### 顶层目录速查（哪些会进 Git，哪些不会）

| 目录 | 用途 | 进 Git？ |
|---|---|---|
| `src/` | 后端模块（处理链 + 配置） | ✅ |
| `web/` | 前端单页 UI | ✅ |
| `docs/` | 深入文档（架构/开发/排查/状态）+ `screenshots/` | ✅ |
| `tools/` | 维护用的一次性脚本（如 ASS 对齐修复） | ✅ |
| `_test_*.py` | 9 套回归测试，根目录平铺（运行用 `for t in _test_*.py`） | ✅ |
| `app.py` / `build.bat` / `*.spec` / `requirements.txt` | 入口、构建、依赖 | ✅ |
| `models/` | Whisper 模型 3.5 GB（首次运行自动下载） | ❌ |
| `dist/` | 构建产物：`jsub-translator.exe` + EXE 版输出 | ❌ |
| `output/` | **源码版**运行输出 | ❌ |
| `.venv/` | 项目虚拟环境（禁止用 C 盘环境） | ❌ |
| `_cuda_dlls/` | CUDA 运行库本地备份 608 MB | ❌ |
| `_backups/` | 编辑前的备份（**R5：绝不放 `src/`**） | ❌ |
| `.env` | 真实 API Key | ❌ |

> `build.bat` 每次构建前会自己清空 `_build/work`、`_build/dist`；
> `build/`、`dist/.runtime/` 这类中间产物用完即弃，不出现在上表里。

### 入口与配置

| 文件 | 作用 |
|---|---|
| `app.py` | Flask 应用：全部路由、SSE 进度流、任务线程、全局 JSON 错误处理 |
| `src/config.py` | **配置中心**：`.env` 加载顺序、路径锚定、API Key 校验、说话人后端探测 |
| `src/pipeline.py` | **流程编排**：`process()` 串起全部阶段，唯一的总入口 |
| `jsub-translator.spec` | PyInstaller 打包配置（含说话人模型与 cuBLAS 的打包规则） |
| `build.bat` | 构建脚本（含 4 道前置校验） |
| `dist/run.bat` | **推荐启动方式**：把 TEMP/TMP 重定向到 E 盘 |

### 处理链（按调用顺序）

| 文件 | 职责 |
|---|---|
| `src/downloader.py` | `resolve_video_input()` — 本地文件直接用，URL 走 yt-dlp |
| `src/audio.py` | `extract_audio()` — 找 ffmpeg（含注册表查找）、提取 16 kHz 音频 |
| `src/transcriber.py` | `transcribe_multi()` — 多 Whisper 模型并行/串行识别；CUDA DLL 注册；模型本地解析 |
| `src/fusion.py` | `fuse_transcripts()` — 用 LLM 以主转录为骨架，比对参考模型结果纠错 |
| `src/diarizer.py` | `diarize()` — 说话人分离（sherpa-onnx 为主）；词级时间戳切分 |
| `src/translator.py` | `translate()` / `review()` — 日→中翻译 + 连贯性校对；系统提示词在此 |
| `src/glossary.py` | JPOP 术语表，命中时作为提示注入翻译 |
| `src/ass_writer.py` | `generate_all_formats()` — 生成 ASS；**样式与对齐常量在此** |
| `src/cache.py` | 缓存统计/清理/打开目录；路径白名单校验 |

### 文档

| 文件 | 内容 |
|---|---|
| `README.md` | **面向使用者**的说明（安装、配置、调参、原理科普） |
| `AGENTS.md` | 本文件：面向 agent 的导航与约定 |
| `docs/ARCHITECTURE.md` | 架构、数据流、模块契约、进度模型 |
| `docs/DEVELOPMENT.md` | 环境、运行、测试、构建、发布 |
| `docs/TROUBLESHOOTING.md` | **排查手册**：症状 → 原因 → 修复 |
| `docs/index-translate.md` | **本地 Index-Translate-2B 部署**（WSL2 + vLLM、术语表、排错、验收清单）|
| `docs/STATE.md` | 当前状态、待办、最近变更（**可写**） |

---

## 5. 常见任务（照抄即可）

### 启动（开发）

```bash
cd "E:/Study/AI/jsub-translator-exe"
.venv/Scripts/python.exe app.py
# 浏览器打开 http://127.0.0.1:7860
```

### 启动（打包版，推荐）

```bash
cd "E:/Study/AI/jsub-translator-exe/dist"
./run.bat          # Git Bash 下必须用 ./ 而不是 cmd //c
```

### 跑测试（改任何东西之前/之后都跑）

```bash
cd "E:/Study/AI/jsub-translator-exe"
for t in _test_*.py; do        # 自动包含新增的测试文件，无需改这段
    echo "--- $t"; .venv/Scripts/python.exe "$t" | tail -3
done
```

### 查「为什么请求失败」/「为什么识别失败」

```bash
curl -s http://127.0.0.1:7860/api/health | .venv/Scripts/python.exe -m json.tool
```

重点看四个字段：

| 字段 | 说明 |
|---|---|
| `api_keys.*.configured` | key 是否配置（只回前缀，不回明文）|
| `env_files_loaded` | **实际加载了哪些 `.env`** —— 判断你打到的是源码版还是 EXE 版 |
| `output_dir_writable` | 输出目录是否可写 |
| `models.ok` | **Whisper 模型是否找得到**（`model_dir` 配错时这里为 `false`）|
| `download_cookies` | **下载 cookies 是否配置**（YouTube 需要；含条数/过期提示）|

> 启动时控制台也会打印同样的自检结论，不必等到失败才发现配置有问题。
> 模型路径配错是**静默**故障，详见
> [`docs/TROUBLESHOOTING.md` §8](docs/TROUBLESHOOTING.md#8-移动项目后模型失效)。

### 重新打包

```bash
cd "E:/Study/AI/jsub-translator-exe"
./build.bat > _build_run.log 2>&1; echo "exit=$?"
```

- **必须看退出码**：正常约 2–3 分钟。**6 秒就结束 = spec 解析失败**。
- **不要把构建输出 pipe 给 grep**：GBK 字节会让 grep 说 `Binary file matches`
  并把 traceback 藏起来。**重定向到文件**再用 `grep -a`。

### 停止服务（Windows）

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

### 清理 C 盘解包残留

```bash
ls -d "$TEMP"/_MEI* "$TEMP"/jsub_* 2>/dev/null
# 确认没有 jsub/python 进程持有后：
rm -rf "$TEMP"/_MEI* "$TEMP"/jsub_*
```

---

## 6. 改代码时必须知道的三件事

1. **`src/config.py` 的 `.env` 加载顺序是「后加载者优先」**
   （`load_dotenv` 默认 `override=False`）。
   冻结版顺序：解包目录的 `.env`（最低）→ EXE 同目录的 `.env`（最高）。
   **新增配置项时要意识到这一点。**

2. **路径一律通过 `_anchor_dir()` 解析**，绝不要用 `Path(相对路径).resolve()`
   —— 那会跟着进程 CWD 走。

3. **改字幕样式/对齐要动 `src/ass_writer.py` 的常量**，不要逐条改 ASS：
   - `STYLE_JAPANESE` → `Alignment 8`（顶部）
   - `SPEAKER_ALIGNMENT = 8`（说话人样式，必须与日文一致）
   - `STYLE_DEFAULT` / `STYLE_CHINESE` → `Alignment 2`（底部）

---

## 7. 深入文档

| 我想…… | 去看 |
|---|---|
| 理解整体架构与数据流 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 搭环境 / 跑测试 / 打包发布 | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| 排查某个具体故障 | [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) |
| 知道现在做到哪了 / 接着做什么 | [`docs/STATE.md`](docs/STATE.md) |
| 给最终用户看的说明 | [`README.md`](README.md) |
