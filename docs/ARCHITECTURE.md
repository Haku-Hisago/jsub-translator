# 架构与数据流

> 配套阅读：[`../AGENTS.md`](../AGENTS.md)（约定）、[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)（故障）
> 最后更新：2026-09-28

---

## 1. 全景

```
                    ┌──────────────────────────────────────────┐
   浏览器 ──HTTP/SSE─▶│  app.py (Flask, 127.0.0.1:7860)         │
                    │   · 路由 / 任务线程 / 进度队列            │
                    └──────────────────┬───────────────────────┘
                                       │ process()  ← 唯一入口
                    ┌──────────────────▼───────────────────────┐
                    │  src/pipeline.py  (阶段编排)              │
                    └──────────────────┬───────────────────────┘
                                       │
   ┌───────────┬───────────┬───────────┼───────────┬───────────┬───────────┐
   ▼           ▼           ▼           ▼           ▼           ▼           ▼
downloader  audio    transcriber   fusion     diarizer   translator  ass_writer
  yt-dlp    ffmpeg  faster-whisper  LLM      sherpa-onnx   LLM        ASS 文件
                     (多模型)      (纠错)     (声纹)    (翻译+校对)
                                       │
                                  ┌────▼────┐
                                  │config.py│  ← 所有模块都依赖它
                                  └─────────┘
```

**关键设计**：`pipeline.process()` 是唯一的流程入口。`app.py` 只负责
「收 HTTP → 开线程 → 调 process → 把进度塞进队列」。

---

## 2. 处理阶段与进度模型

进度是一个 **0.0–1.0 的浮点数**，每个阶段占据固定区间（在 `pipeline.py` 中硬编码）：

| # | stage 标识 | 进度区间 | 做什么 | 失败会怎样 |
|---|---|---|---|---|
| 1 | `download` | 0.00 – 0.05 | 定位/下载视频 | 致命 |
| 2 | `audio` | 0.05 – 0.15 | ffmpeg 提取 16 kHz 音频 | 致命 |
| 3 | `transcribe` | 0.15 – 0.40 | 多 Whisper 模型识别 | **全部失败才致命**；部分成功则继续 |
| 3.5 | `fuse` | 0.40 – 0.45 | LLM 比对多路转写纠错 | 增强步骤，失败降级保留原文 |
| 4 | `diarize` | 0.45 – 0.55 | 说话人分离 + 跨说话人切分 | 增强步骤，失败按单说话人继续 |
| 5 | `translate` | 0.55 – 0.75 | 日→中翻译 | **全部批次失败才致命**；部分失败在字幕里标 `[翻译失败: …]` |
| 5.5 | `review` | 0.75 – 0.87 | LLM 连贯性校对 | 增强步骤，失败保留初译 |
| 6 | `ass` | 0.87 – 0.95 | 生成 ASS | 致命 |
| — | `done` | 1.00 | 完成 | — |

> ⚠️ **不要给某个阶段补一条笼统的「完成」回调。**
> 各阶段的收尾回调已经落在相同进度点上，且会区分「完成」与「部分失败」。
> 补一条笼统的会把刚发出的失败信息覆盖掉（`pipeline.py` 里有对应注释）。

**进度传递链**：
`pipeline.report()` → `app._run_job.progress_callback()` → `queue.Queue`
→ `GET /api/progress/<job_id>`（SSE）→ 浏览器进度条。

---

## 3. 核心数据契约

### 3.1 segment（贯穿全流程的字典）

```python
{
  "start":      0.0,        # float, 秒
  "end":        2.5,        # float, 秒
  "text":       "こんにちは", # str, 日语原文（ASR 结果）
  "translated": "你好",      # str, 中文译文（translate 阶段填入）
  "speaker":    "SPEAKER_00",# str, 说话人标签（"" = 未知）
  "words":      [            # list[dict], 可选，仅 word_timestamps 开启时存在
      {"word": "こん", "start": 0.0, "end": 0.4},
  ],
}
```

**字段由谁写入**：

| 字段 | 写入者 |
|---|---|
| `start` / `end` / `text` / `words` | `transcriber.py` |
| `text`（可能被纠正） | `fusion.py` |
| `speaker` | `diarizer.py` |
| `translated` | `translator.py` |

**注意**：`translate()` 会返回新列表，`pipeline.py` 在之后**手动把 `speaker`
补回**翻译结果（见 `pipeline.py` 第 176–179 行）。改这段逻辑时别丢掉说话人。

### 3.2 输出文件契约

`generate_all_formats()` 返回 `dict[格式名 → Path]`：

| 键 | 文件名 | 何时生成 |
|---|---|---|
| `bilingual` | `<标题>字幕_双语.ass` | 总是 |
| `bilingual_split` | `<标题>字幕_双语分轨.ass` | 总是 |
| `japanese` | `<标题>字幕_日文.ass` | 总是 |
| `chinese` | `<标题>字幕_中文.ass` | 总是 |
| `bilingual_speaker_colored` | `<标题>字幕_双语_说话人分色.ass` | **仅当检测到说话人** |

文件用 **UTF-8 with BOM**（`utf-8-sig`）写出 —— ASS 含 CJK 时 Aegisub 需要 BOM。

### 3.3 ASS 样式表

| 样式 | Alignment | 位置 | 用在哪 |
|---|---|---|---|
| `Japanese` | **8** | 顶部居中 | `bilingual_split` / `japanese` 的日文行 |
| `S00` / `S01` / … | **8** | 顶部居中 | 有说话人时，所有行都改引用它 |
| `Default` | 2 | 底部居中 | `bilingual` 单行合并、无说话人时 |
| `Chinese` | 2 | 底部居中 | `bilingual_split` 的中文行 |

> 🔴 **`SPEAKER_ALIGNMENT` 必须等于日文样式的 8。**
> 一旦有说话人，**所有** Dialogue 行都改引用 `S0x`，所以说话人样式的对齐
> 直接决定字幕出现在画面上还是画面下。详见 `TROUBLESHOOTING.md`。

颜色用 **ABGR**（`&HAABBGGRR`，R 与 B 与 HTML 相反）。
说话人调色板**刻意避开纯白**，否则分到白色的说话人在分色版里看不出区别。

---

## 4. 模块职责与关键接口

### `src/config.py` — 配置中心

被所有模块依赖，**改动影响面最大**。

| 导出 | 说明 |
|---|---|
| `ROOT_DIR` | 项目根（源码版）|
| `BUNDLE_DIR` | 打包资源目录（`sys._MEIPASS`），每次启动都变 |
| `ENV_FILES` | 实际加载的 `.env` 路径列表（诊断用，`/api/health` 会回显）|
| `OUTPUT_DIR` / `MODEL_DIR` | 已解析为**绝对路径** |
| `resolve_api_key()` | 优先级：运行时传入（网页输入框）> 环境变量 |
| `validate_config()` | 缺 key / provider 非法时 `raise ValueError`，消息面向用户 |
| `validate_diarization()` | 返回 `(可用?, 原因)`，启动时打印 |

**路径解析规则**（`_anchor_dir()` + `_resolve_dir()`）：

| 配置值 | 解析结果 |
|---|---|
| 空 | `<锚定目录>/<默认名>` |
| 相对路径 | `<锚定目录>/<该路径>` |
| 绝对路径 | 原样使用 |

**锚定目录** = 打包版为 **EXE 所在目录**，源码版为 **项目根目录**。
**永远不要**用 `Path(相对路径).resolve()` —— 那会跟着进程 CWD 走。

**`.env` 加载顺序**（重要）：

```python
# load_dotenv 默认 override=False → 「后加载者优先级更高」
冻结版: [解包目录的 .env]      # 打包进去的模板副本，优先级最低
        [EXE 同目录的 .env]    # 用户自己的配置，优先级最高
源码版: [项目根 .env]
```

**占位符防御**：`_looks_like_placeholder()` 会把
`sk-your-deepseek-key-here` 这类模板值识别为「未配置」（归一化为 `""`），
让 `validate_config()` 报出可读提示，而不是发出请求拿 401。

### `src/downloader.py` — 视频获取（yt-dlp）

| 函数 | 说明 |
|---|---|
| `resolve_video_input(src)` | 分派：是 URL 就下载，否则当本地文件返回 |
| `download_video(url)` | yt-dlp 下载，1080p 以内最佳画质，合并为 mp4 |
| `is_url(text)` | 判定是否为 http(s) 链接 |
| `_cookie_opts()` | 把 `.env` 的 cookies 配置翻译成 yt-dlp 选项；**未配置时返回 `{}`** |
| `_looks_like_auth_error(e)` | 判定异常是否属于「需要登录 / 机器人校验」|
| `_auth_help(url, configured)` | 生成可操作的中文修复指引 |

**为什么需要 cookies**：YouTube 对未登录下载做机器人校验，匿名请求会被拒
（`Sign in to confirm you're not a bot` / `No video formats found!`）。
Bilibili / Niconico 等不受影响 —— **这正好可以用来区分「模块坏了」和
「站点要登录」**。详见 [`TROUBLESHOOTING.md` §16](TROUBLESHOOTING.md#16-通过链接下载失败youtube-要-cookies)。

配置（`.env`，二选一，留空即维持原行为）：

| 变量 | 说明 |
|---|---|
| `YTDLP_COOKIES_FILE` | Netscape 格式 cookies.txt 路径，**优先**；相对路径锚定到程序目录 |
| `YTDLP_COOKIES_FROM_BROWSER` | 浏览器名（`edge`/`chrome`/`firefox`…），常因加密/占用失败 |
| `YTDLP_RETRIES` | 网络重试次数，默认 3 |

> **设计要点：全部是可选项。** `_cookie_opts()` 在未配置时返回 `{}`，
> 因此不配置 cookies 的行为与加这个功能之前**完全一致**；
> 配了不存在的文件则**明确报错**（不静默忽略，否则用户以为生效了）。

### `src/transcriber.py` — 语音识别

| 函数 | 说明 |
|---|---|
| `transcribe_multi(audio, model_sizes)` | 返回 `(transcripts: dict[模型名→segments], errors: dict[模型名→错误])` |
| `_resolve_model_path(size)` | 解析顺序：`MODEL_DIR/<size>-ct2/` → HF 缓存 `models--*/snapshots/` → **裸模型名（触发联网下载）** |
| `_local_model_sizes()` | 本地已完整缓存的模型列表 |
| `diagnose_model_dir()` | **启动自检**：`MODEL_DIR` 里到底有没有模型；没有时会去别处找并指出正确路径 |
| `_register_cuda_dll_dirs()` | 把 cuBLAS/cuDNN 所在目录注册进 DLL 搜索路径 |

**执行策略**：
- **CPU**：多模型**并行**。
- **CUDA**：强制**串行**。两个模型同时加载 CUDA dll 会让 Windows 加载器
  **死锁**（症状：进度卡在「1/2」，CPU/GPU 都 0%）。

**复读守卫**：`compression_ratio_threshold=2.4` + 温度回退链
`[0.0, 0.2, …, 1.0]`。**两者必须同时设置**，只改一个无效。
`log_prob_threshold` / `no_speech_threshold` **保持关闭** —— 对音量偏低的
日语过于苛刻，静音交给 Silero VAD。

### `src/fusion.py` — 多路转写融合

以**主转录**（`WHISPER_MODEL`）为骨架，把参考模型在时间上重叠的文本一起喂给
LLM，让它纠正同音词、助词、断句、漏字。`_overlap_refs()` 用 0.3 s 容差找重叠。

### `src/diarizer.py` — 说话人分离

**核心认知：Whisper 没有声纹能力。** 「谁在说」必须由独立模型解决，再按时间轴
对齐。本项目用 **sherpa-onnx**（ONNX Runtime，纯 CPU）：

- 分割：pyannote `segmentation-3.0` 的 ONNX 导出
- 声纹：3D-Speaker ERes2Net
- **不需要 torch**（省 3 GB）、**不需要 HF Token**、不碰 CUDA 加载路径

| 函数 | 说明 |
|---|---|
| `diarize(audio, segments, num_speakers)` | 主入口；返回带 `speaker` 的 segments |
| `_best_speaker()` | 区间重叠最多的说话人 |
| `_nearest_speaker(max_gap=8.0)` | 完全不重叠时取最近者；超过 8 s 就放弃（宁可留空也不猜）|
| `split_segments_by_speaker()` | 一条字幕横跨两人时，按**词级中点**切分 |
| `_word_timestamps_reliable()` | 词序列能否还原原文，**对齐率 < 95% 就跳过切分** |

**对齐的四种情况**：① 完全落在一段内 → 直接归属；② 跨 ≥2 段 → 按词切分；
③ 不落任何段（静音/音乐）→ `_nearest_speaker` 8 s 内；④ 切分后仍不落 → 回退父段。

**聚类阈值** `DIARIZE_CLUSTER_THRESHOLD` 默认 **0.92**：
值越小 → 说话人越多。实测平台区 **0.88–0.96 → 4 人**，0.92 是**平台正中**
（边缘值如 0.8 换段音频就翻成 5 人）。

### `src/translator.py` — 翻译与校对

| 类/函数 | 说明 |
|---|---|
| `BaseTranslator` | 抽象基类，子类实现 `translate_batch()` |
| `DeepSeekTranslator` / `AnthropicTranslator` | 两个实现 |
| `create_translator(provider)` | 工厂 |
| `translate(segments, …)` | 分批翻译，**全部批次失败会 raise** |
| `review(segments, …)` | 连贯性校对，失败降级 |

`SYSTEM_PROMPT` / `REVIEW_SYSTEM_PROMPT` 是**说话人一致性**的落点 ——
要求 LLM 按角色统一一人称（私/僕/俺）、敬语等级、称谓，并从说话人推断
日语省略的主语。这是「加强人物对话能力」的实际收益所在。

### `src/ass_writer.py` — 字幕输出

见上面「3.3 ASS 样式表」。样式常量集中在文件顶部，**改样式只改这里**。

---

## 5. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 网页界面 |
| GET | `/api/health` | **自检**：生效配置、key 状态、目录可写性、**模型是否找得到**、**下载 cookies 状态**、说话人可用性 |
| POST | `/api/upload` | 上传视频/音频，返回服务端路径 |
| POST | `/api/start` | 提交任务 → `{job_id}`；**入队前先 `validate_config()`** |
| GET | `/api/progress/<job_id>` | **SSE** 进度流，事件 `progress` / `done` / `error` / `ping` |
| GET | `/api/result/<job_id>` | 结果：segments + files |
| GET | `/api/download/<filepath>` | 下载（只取 basename，固定在 `OUTPUT_DIR` 下查找）|
| GET | `/api/cache/stats` | 各缓存类别的数量与体积 |
| POST | `/api/cache/open` | 在资源管理器打开输出目录 |
| POST | `/api/cache/clean` | 清理选中类别 |
| GET | `/api/_selftest_boom` | **仅供测试**：故意抛异常，验证 `/api/*` 返回 JSON |

**错误约定**：所有 `/api/*` 的异常都由全局 `@app.errorhandler(Exception)` +
`_json_error()` 转成 `{"error": "..."}`。**非 `/api/` 路由保持 HTML 默认行为。**

**前端对应**：`web/templates/index.html` 的 `readJson(resp)` 统一兜底非 JSON
响应。**新增 fetch 调用一律用它**，不要直接 `resp.json()`。

**缓存清理的安全约束**：前端**只传类别 key**（`subtitles` / `videos` /
`uploads` / `temp`），不传路径；服务端 `src/cache.py` 二次校验目标必须落在
`OUTPUT_DIR` 或系统临时目录之下。

---

## 6. 打包结构

`jsub-translator.spec` 里三类特殊资源：

1. **说话人模型**（约 44 MB）—— 显式逐个 `(源文件, 目标目录)` 列出。
   传**目录**给 PyInstaller **不会保留层级**，必须逐文件列。
   目标路径要与 `config._bundled_or()` 的查找路径一致：
   `models/sherpa-onnx-pyannote-segmentation-3-0/` 和 `models/`。
2. **cuBLAS**（约 640 MB）—— 必须落在打包后的 `ctranslate2/` 目录内、
   与 `ctranslate2.dll` 同级。CTranslate2 是**运行时动态加载** cuBLAS 的，
   而它的 wheel **只带 cuDNN 不带 cuBLAS**。
3. **`src/` 与 `web/templates/`** —— 整个目录打包。
   ⚠️ 所以 `src/` 里**不能有备份文件**，否则会被一起打进 EXE（R5）。

---

## 7. 扩展点（想加功能看这里）

| 想加 | 改哪 |
|---|---|
| 新的翻译引擎 | `translator.py` 加 `BaseTranslator` 子类 + `create_translator()` 分支 |
| 新的输出格式 | `ass_writer.generate_all_formats()` 的 `formats` 字典 |
| 新的说话人后端 | `diarizer.py` 加 `_diarize_xxx()`，在 `diarize()` 里分发 |
| 新的配置项 | `config.py` + `.env.example`（**记得同步两者**）|
| 新的 API | `app.py`，**必须走 `_json_error()`**（R4）|
| 新术语 | `glossary.py` 的 `JPOP_GLOSSARY` |
