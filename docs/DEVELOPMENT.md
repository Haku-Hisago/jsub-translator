# 开发与构建

> 配套阅读：[`../AGENTS.md`](../AGENTS.md)（约定）、[`ARCHITECTURE.md`](ARCHITECTURE.md)（架构）
> 最后更新：2026-09-28

---

## 1. 环境

### 1.1 Python 环境在项目内，不在 C 盘

```
E:\Study\AI\jsub-translator-exe\.venv\Scripts\python.exe    # Python 3.13.14
```

这个 venv 含全部构建依赖（PyInstaller、faster-whisper、ctranslate2、
sherpa-onnx 等）。**不要用 C 盘的解释器，也不要往 C 盘装包。**

若 venv 损坏需要重建（注意：**建在项目盘**）：

```bash
# 用你自己的 Python 3.13+（系统 Python 或任一 venv 基础解释器均可）
python \
    -m venv "E:/Study/AI/jsub-translator-exe/.venv"
E:/Study/AI/jsub-translator-exe/.venv/Scripts/python.exe -m pip install -r requirements.txt
E:/Study/AI/jsub-translator-exe/.venv/Scripts/python.exe -m pip install pyinstaller nvidia-cublas-cu12
```

### 1.2 外部依赖

| 依赖 | 说明 |
|---|---|
| **FFmpeg** | **必须单独安装，无法打包进 EXE**。`audio.py` 会依次找 PATH、注册表、常见目录。`winget install Gyan.FFmpeg` |
| **NVIDIA 驱动** | 只有用 GPU 识别才需要。CPU 也能跑，见 1.4 |

### 1.3 cuBLAS（GPU 识别的关键坑）

`ctranslate2` 的 wheel **只自带 cuDNN，不带 cuBLAS**，而 cuBLAS 是**运行时
动态加载**的。缺了就会：

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

三个 DLL 必须出现在 `site-packages/ctranslate2/`（与 `ctranslate2.dll` 同级）：

| 文件 | 大小 |
|---|---|
| `cublas64_12.dll` | ~105 MB |
| `cublasLt64_12.dll` | ~530 MB |
| `cudart64_12.dll` | ~0.5 MB |

`pip install nvidia-cublas-cu12` 会把它们放进 `site-packages/nvidia/cublas/bin`，
**需要手动复制**到 `ctranslate2/`。项目根的 `_cuda_dlls/` 是本地备份。

`build.bat` 的 `[2/4]` 步骤会检查，缺失直接报错并给出修复提示。

### 1.4 不想折腾 CUDA？

`.env` 里改用 CPU（慢很多，但一定能跑）：

```ini
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

---

## 2. 配置（`.env`）

### 2.1 两个 `.env` 的位置与优先级

| 位置 | 用途 |
|---|---|
| `<项目根>\.env` | 源码版配置 |
| `<项目根>\dist\.env` | **EXE 版配置**（EXE 同目录）|

`config._load_env_files()` 的顺序是 **「后加载者优先」**
（`load_dotenv` 默认 `override=False`）。冻结版：

```
1. 解包目录里的 .env   ← 打包进去的 .env.example 副本，优先级最低
2. EXE 同目录的 .env   ← 用户配置，优先级最高
```

> ⚠️ **改配置项时两个 `.env` 都要改**，否则会出现「源码版正常、EXE 版不正常」。

### 2.2 完整配置项

`config.py` 读的全部变量（**改这里要同步 `.env.example`**）：

**引擎与凭据**

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRANSLATION_PROVIDER` | `deepseek` | `deepseek` / `anthropic` |
| `DEEPSEEK_API_KEY` | — | **占位符会被当作未配置** |
| `DEEPSEEK_MODEL` | `deepseek-chat` | |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `ANTHROPIC_API_KEY` | — | |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | |

**Whisper**

| 变量 | 默认 | 说明 |
|---|---|---|
| `WHISPER_MODEL` | `large-v3-turbo` | 主模型，决定时间轴骨架 |
| `WHISPER_REFERENCE_MODELS` | `medium` | 逗号分隔，供交叉比对 |
| `WHISPER_DEVICE` | `cuda` | `cuda` / `cpu` |
| `WHISPER_COMPUTE_TYPE` | `float16` | GPU 用 float16，CPU 用 int8 |
| `WHISPER_WORD_TIMESTAMPS` | `1` | 跨说话人切分需要；设 0 省时间 |

**融合 / 上下文 / 校对**

| 变量 | 默认 | 说明 |
|---|---|---|
| `FUSE_PROVIDER` | 空 | 留空 = 与翻译引擎相同 |
| `FUSE_BATCH_SIZE` | `15` | |
| `CONTEXT_WINDOW` | `4` | 跨批次携带的上文条数；0 = 关闭 |
| `ENABLE_REVIEW` | `true` | 是否跑连贯性校对 |
| `REVIEW_PROVIDER` | 空 | 留空 = 与翻译引擎相同 |
| `REVIEW_BATCH_SIZE` | `15` | |

**说话人识别**

| 变量 | 默认 | 说明 |
|---|---|---|
| `DIARIZE_BACKEND` | `sherpa` | `sherpa` / `pyannote` / `auto` / `none` |
| `DIARIZE_SEG_MODEL` | 空 | 留空 = `models/` 优先，否则用打包副本 |
| `DIARIZE_EMB_MODEL` | 空 | 同上 |
| `DIARIZE_NUM_SPEAKERS` | `-1` | 已知人数就填（更准）；-1 = 自动聚类 |
| `DIARIZE_CLUSTER_THRESHOLD` | `0.92` | 越小 → 人越多 |
| `DIARIZE_SPLIT_ON_CHANGE` | `true` | 跨说话人时是否切分 |
| `HF_TOKEN` | 空 | **仅 pyannote 后端需要**，sherpa 不需要 |

**下载（yt-dlp）**

| 变量 | 默认 | 说明 |
|---|---|---|
| `YTDLP_COOKIES_FILE` | 空 | Netscape 格式 cookies.txt 路径，**优先**；相对路径锚定程序目录 |
| `YTDLP_COOKIES_FROM_BROWSER` | 空 | `edge` / `chrome` / `firefox` / …（常因加密或占用失败）|
| `YTDLP_RETRIES` | `3` | 下载失败自动重试次数 |

> YouTube 需要 cookies 才能下载（机器人校验）。Bilibili / Niconico 不需要。
> 留空 = 不发送任何 cookie，行为与加这个功能之前完全一致。

**路径与其他**

| 变量 | 默认 | 说明 |
|---|---|---|
| `OUTPUT_DIR` | 空 | 空 = `<锚定目录>/output` |
| `MODEL_DIR` | 空 | 空 = `<锚定目录>/models` |
| `HF_ENDPOINT` | — | 国内建议 `https://hf-mirror.com` |
| `PORT` | `7860` | 环境变量，不在 `.env` 里读 |

> 🔴 **路径陷阱**：`MODEL_DIR` 一旦写死绝对路径，**项目一移动就失效**。
> 本项目已经从 `E:\大学\AI\jsub-translator-exe` 移到
> `E:\Study\AI\jsub-translator-exe` 一次，当时 `MODEL_DIR` 没跟着改，
> 结果 3.5 GB 模型被无视、程序以为要重新下载。
> **移动项目后必须检查两个 `.env` 的 `MODEL_DIR`。**

---

## 3. 运行

### 3.1 源码版（开发用）

```bash
cd "E:/Study/AI/jsub-translator-exe"
.venv/Scripts/python.exe app.py
```

启动时会打印两项自检 —— **说话人识别**是否可用，以及 **Whisper 模型**是否找得到：

```
============================================================
  [说话人识别] 可用 — sherpa-onnx
============================================================
============================================================
  [Whisper 模型] 就绪 — E:\Study\AI\jsub-translator-exe\models（可用：small, medium, large-v3-turbo）
============================================================
[jsub-translator] Starting...
    http://127.0.0.1:7860
```

模型自检**不通过时会直接指出正确的路径**（`MODEL_DIR` 配错是静默故障，
见 [`TROUBLESHOOTING.md` §8](TROUBLESHOOTING.md#8-移动项目后模型失效)）：

```
  [Whisper 模型] 有问题 — E:\大学\AI\jsub-translator-exe\models 下没有找到任何 Whisper 模型
  -> 但在这些位置找到了模型，很可能是 .env 里的 MODEL_DIR 指错了：
       E:\Study\AI\jsub-translator-exe\models
```

### 3.2 打包版（**推荐给用户**）

```bash
cd "E:/Study/AI/jsub-translator-exe/dist"
./run.bat
```

`run.bat` 把 `TEMP` / `TMP` 重定向到 `dist\.runtime\`，**C 盘零写入**。

> ⚠️ 直接双击 EXE 也能用，但每次启动会往 `C:\...\AppData\Local\Temp`
> 解包约 850 MB。正常退出会自动清理；**被强制结束会残留**一个
> `%TEMP%\_MEIxxxxxx`，需手动删。

> ⚠️ Git Bash 下用 `./run.bat`，**不要用 `cmd //c run.bat`** ——
> 后者不会真正执行脚本，只打印横幅，看起来像脚本坏了。

---

## 4. 测试

### 4.1 全部测试

```bash
cd "E:/Study/AI/jsub-translator-exe"
for t in _test_*.py; do        # 自动包含新增的测试文件，无需改这段
    echo "--- $t"; .venv/Scripts/python.exe "$t" | tail -3
done
```

预期：**399 passed, 0 failed**。

| 文件 | 断言数 | 守什么 |
|---|---|---|
| `_test_ass_alignment.py` | 63 | 说话人样式对齐（5 种模式）；**预览颜色/缩写名与 ASS 同源**、调色板不含纯白；花括号必须转义；配色唯一一份且跨进程稳定 |
| `_test_path_resolution.py` | 47 | `OUTPUT_DIR`/`MODEL_DIR` 不随 CWD 变化（4 个 CWD 验证）；**`.env` 的 `MODEL_DIR` 必须指向真有模型的目录**；**下载的视频只落 OUTPUT_DIR 一份** |
| `_test_diarization_logic.py` | 24 | 说话人归属、跨说话人切分、词级对齐校验（离线，不需要音频）|
| `_test_api_key_resolution.py` | 49 | 占位符识别、`.env` 加载顺序、`/api/*` 必回 JSON、`/api/health` 不泄露 key |
| `_test_downloader_cookies.py` | 44 | 下载模块：cookies 配置与优先级、`cookie_status()` 自检、认证类错误识别与提示；**ffmpeg 查找与 audio 同源**；原有本地文件/URL 判定不受影响 |
| `_test_cache_safety.py` | 27 | **删除路径安全**：盘根/系统目录误配必须拒绝清理、删除不越界、绝不删 OUTPUT_DIR 本身；**网页 accept 与 MEDIA_EXTS 必须一致** |
| `_test_job_lifecycle.py` | 28 | **任务内存回收**：已结束任务按上限淘汰、运行中任务永不淘汰、按结束顺序淘汰；**失败任务必须报 error 而非永远 running**；**SSE 重连补发终结事件** |
| `_test_translate_resilience.py` | 36 | **翻译容错**：401/403/400 不重试、可恢复错误重试到上限、连续失败提前中止、中途恢复不误判；**失败用布尔字段而非字符串匹配** |
| `_test_docs_links.py` | 11 | **文档不腐烂**：跨文件相对链接可解析、文内 `#锚点` 与标题 slug 一致、TROUBLESHOOTING 每个编号章节都已进快速索引 |
| `_test_index_backend.py` | 70 | **本地 Index-Translate 接入**：instTrans prompt 结构、动态 glossary 只注入命中项、按 id 映射（不是下标）、校验器逐条规则、缩批重试、过长压缩、fallback 开关、服务未启动时的人话报错。用**真实 HTTP 服务**跑通全链路（模型是假的） |

### 4.2 手动冒烟（端到端）

改完关键路径后建议跑一遍真实链路：

```bash
# 1. 起服务，确认 health
curl -s http://127.0.0.1:7860/api/health | .venv/Scripts/python.exe -m json.tool

# 2. 上传（用真实音视频文件）
curl -s -X POST http://127.0.0.1:7860/api/upload -F "file=@test.wav"

# 3. 提交任务（payload 写文件，别用内联引号 —— 会被 shell 弄坏）
#    {"file_path":"<上一步返回的 path>","provider":"deepseek",
#     "diarize":false,"context":false,"review":false}
curl -s -X POST http://127.0.0.1:7860/api/start \
     -H "Content-Type: application/json" --data-binary @payload.json

# 4. 查结果
curl -s http://127.0.0.1:7860/api/result/<job_id> | .venv/Scripts/python.exe -m json.tool
```

**成功判据**：`"status": "done"` + 生成 4 个 `.ass` 文件。

> 纯正弦波音频不会有语音，`segments` 为 0 是正常的 —— 只要
> `status: done` 就说明整条链路（含 LLM 调用）通了。

### 4.3 测试打包版时的**必做**前置动作

**先确认 7860 端口没有别的进程。** 两个监听者时 `curl` 会打到先绑定的那个，
`/api/health` 显示的是**另一个**实例的配置，会静默得出错误结论。

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

判据：`/api/health` 的 `env_files_loaded` 应该指向 **`dist\.env`**，
而不是项目根的 `.env`。

---

## 5. 构建

```bash
cd "E:/Study/AI/jsub-translator-exe"
./build.bat > _build_run.log 2>&1; echo "exit=$?"
```

### 5.1 `build.bat` 的四道校验

| 步骤 | 检查什么 | 失败后果 |
|---|---|---|
| `[1/4]` | PyInstaller / ctranslate2 可用 | 退出 |
| `[1b/4]` | 说话人模型是否就位 | 退出 |
| `[2/4]` | **cuBLAS 三个 DLL 是否齐全** | 退出（否则打出来的 EXE 用不了 GPU）|
| `[2b/4]` | **`src/` 下没有备份/临时文件** | 退出（否则会被打进 EXE，R5）|

然后 `[3/4]` 清理旧构建，`[4/4]` 打包（**实测约 2–3 分钟**；
脚本里写的「5–15 分钟」是保守估计）。

### 5.2 构建排错

| 症状 | 原因 |
|---|---|
| **6 秒就结束** | spec 解析失败（如 `AttributeError`），**不是**构建成功 |
| 日志里 `Binary file matches` | 你把输出 pipe 给了 grep。**重定向到文件**，用 `grep -a` |
| `ERROR: Hidden import 'X' not found` | 多半只是噪音。注意 PyAV 的 import 名是 `av` 不是 `PyAV` |

**安全替换**：脚本只把新 EXE 搬到 `dist\`，旧的重命名为 `.bak-prev`。
**绝不 `rmdir` 整个 `dist\`** —— 里面有 `output\` 成品、`models\`、`.env`。

### 5.3 构建后验证（重要）

```bash
# 归档里不应有备份文件
.venv/Scripts/python.exe -c "
from PyInstaller.archive.readers import CArchiveReader
r = CArchiveReader('dist/jsub-translator.exe')
names = list(r.toc.keys() if hasattr(r.toc,'keys') else [n for n,_ in r.toc])
print('文件数:', len(names))
print('备份残留:', [n for n in names if '.bak' in n or '.orig' in n] or '[] ✓')
"
```

然后**实际启动打包版**跑一次，确认 `env_files_loaded` 指向 `dist\.env`、
`api_keys.*.configured` 为 `true`。

---

## 6. `.bat` 文件编码（中文 Windows 必读）

**所有 `.bat` 必须是 GBK + CRLF，且不要加 `chcp 65001`。**

`Write`/`Edit` 工具默认写 UTF-8，直接改 `.bat` 会得到**混合编码**
（旧字节 GBK + 新字节 UTF-8）。症状：按 GBK 能解码，按 UTF-8 失败。

诊断与修复：

```python
from pathlib import Path
raw = Path('build.bat').read_bytes()
try: raw.decode('gbk');   print('gbk ok')
except Exception as e:    print('gbk FAILS', e)
try: raw.decode('utf-8'); print('utf-8 ok')
except Exception as e:    print('utf-8 FAILS → 混合编码，需修复', e)

# 修复：按 GBK 读 → 归一化 CRLF → 按 GBK 写回
t = raw.decode('gbk').replace('\r\n', '\n').replace('\n', '\r\n')
Path('build.bat').write_bytes(t.encode('gbk'))
```

校验：CRLF 数 > 0，裸 LF 数 == 0，按 GBK 解码中文正常。

---

## 7. 发布 / 便携版

`E:\Study\AI\jsub-translator-portable\` 是给最终用户的分发包结构：

```
jsub-translator-portable/
├── jsub-translator.exe     # 主程序
├── .env                    # 配置（含 key）
├── ffmpeg/                 # 随包 FFmpeg，省得用户自己装
├── models/                 # Whisper 模型缓存
├── 启动.bat                # 双击入口
├── 使用说明.txt            # 面向用户的说明
└── 源码/                   # 源码副本
```

同名 `.zip` 是打包后的分发文件（约 3.8 GB）。
**注意**：便携版的 `.env` 同样是**写死绝对路径**的高危位置 —— 换机器/换盘符
后 `MODEL_DIR` 会失效（见 §2.2 的路径陷阱）。

---

## 8. 代码约定

- **注释与用户可见文案用中文**；标识符用英文。
- **新增 `/api/*` 路由必须走 `_json_error()`**（R4）。
- **前端 fetch 一律用 `readJson(resp)`**，不要裸 `resp.json()`。
- **不要用 `Path(相对路径).resolve()`**，用 `config._resolve_dir()`。
- **改字幕样式只改 `ass_writer.py` 顶部的常量。**
- **备份文件放 `_backups/`，不要放 `src/`**（R5）。
- **改配置项要同步三处**：`config.py`、`.env.example`、`.env` / `dist\.env`。
