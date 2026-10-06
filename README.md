# jsub-translator

**japanese-subtitle-translator** — 把日语视频变成中文字幕的本地工具。

<p align="center">
  <a href="#简体中文">简体中文</a> · <a href="#繁體中文">繁體中文</a> · <a href="#english">English</a> · <a href="#日本語">日本語</a>
</p>

> 本仓库仅含**源码**。Whisper 模型、构建好的 EXE 与运行产物都已被 `.gitignore` 排除。
> 开发者与 AI agent 请阅读 [`AGENTS.md`](AGENTS.md)；深入文档见 [`docs/`](docs/)。

---

## 简体中文

### 项目简介

jsub-translator 是一款**本地运行**的日语视频字幕翻译工具：把 YouTube / Niconico 链接或本地视频先转写成日文时间轴，再用**本地**大语言模型（Index-Translate-2B）翻译为中文，最终生成带说话人分色的 ASS 字幕文件。默认配置下整个流程**完全在本地完成**，原始视频和字幕都不会离开你的机器。

### 主要功能

- **多模型转录**：默认 `large-v3-turbo` 作为主识别 + `medium` 交叉比对，时间轴由主模型决定、文本由多模型投票融合
- **说话人识别**：基于 sherpa-onnx 的离线 diarization，自动给不同说话人配上区分色
- **本地翻译引擎**：默认 Index-Translate-2B（B站官方模型，vLLM 本地推理，零 API 费用）；也可一键切回 DeepSeek / Anthropic 云端
- **术语表（Glossary）**：`dictionary/*.yaml` 分主题词典，只把本批命中的术语作为**硬约束**下发给模型，人名团名不被擅自改写
- **译文校验**：条数/id 对应/空译文/Markdown/解释文字/术语落实逐条检查，不过则重试、缩批，而不是把错译文静默写进字幕
- **多种字幕格式**：双语合并、双语分轨、日文单轨、中文单轨、说话人分色双语
- **多种输入**：YouTube / Niconico 链接（自动下载）或本地视频文件
- **Web 界面**：Flask 单页 UI，双击启动后浏览器自动打开，无需安装客户端
- **10 套回归测试 / 399 条断言**（参见 `AGENTS.md`）

### 安装与使用

#### 前提条件

1. **Python 3.13+**
2. **FFmpeg**：可执行文件加入 PATH，或安装到 `C:\ffmpeg\`。
   - Windows 推荐：`winget install Gyan.FFmpeg`

#### 安装步骤

```bash
git clone <repo-url>
cd jsub-translator
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

#### 配置

复制 `.env.example` 为 `.env`，填入 DeepSeek 或 Anthropic 的 API Key：

```bash
cp .env.example .env       # macOS/Linux
copy .env.example .env     # Windows
```

#### 启动

```bash
python app.py
```

浏览器自动打开 `http://127.0.0.1:7860`。

**默认引擎是本地 Index-Translate-2B**，需要先起本地服务（WSL2 + vLLM，见 [`docs/index-translate.md`](docs/index-translate.md)）：

```bash
vllm serve IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8000 --max-model-len 32768
```

然后：选视频链接或本地文件 → 点「开始处理」。
不想装本地服务的话，在「翻译引擎」下拉里换成 DeepSeek 并填入 API Key 即可。

### 目录结构

```
.
├── AGENTS.md           开发者 / AI agent 导航、硬规则、常用命令
├── README.md           本文件（四语并排项目介绍）
├── app.py              Flask 入口、路由、SSE 进度推送
├── build.bat           一键打包 EXE（PyInstaller）
├── requirements.txt    Python 依赖
├── jsub-translator.spec            PyInstaller 配置
├── jpop_glossary.json.example      日语术语表示例（复制为 jpop_glossary.json 启用）
├── src/
│   ├── config.py              配置加载、路径解析、provider 校验
│   ├── audio.py               ffmpeg 调用、错误信息收口
│   ├── transcriber.py         Whisper 多模型识别
│   ├── diarizer.py            说话人识别（sherpa-onnx）
│   ├── translator.py          LLM 调用、翻译/校对、重试/容错
│   ├── fusion.py              多模型结果投票融合
│   ├── ass_writer.py          ASS 字幕生成（含配色表权威定义）
│   ├── cache.py               输出目录统计与清理
│   ├── downloader.py          yt-dlp 包装、cookies 自检
│   └── pipeline.py            主流程编排、阶段进度上报
├── web/
│   └── templates/index.html   单页 UI（HTML + CSS + 原生 JS）
├── docs/                深入文档
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   ├── TROUBLESHOOTING.md
│   ├── STATE.md
│   └── screenshots/      UI 截图
├── dictionary/          术语表（*.yaml，分 terms / people / groups 三节）
├── tests/
│   ├── translation/     翻译用例跑批器
│   └── benchmark/       Index vs DeepSeek 对比基准
├── tools/               维护用脚本（ASS 对齐修复等）
├── _test_*.py           10 套回归测试（399 条断言）
└── .env.example         配置模板（不要提交 .env）
```

---

## 繁體中文

### 專案簡介

jsub-translator 是一款**本地運行**的日語影片字幕翻譯工具：將 YouTube / Niconico 連結或本地影片先轉寫為日文時間軸，再用**本地**大型語言模型（Index-Translate-2B）翻譯為中文，最終產生帶說話人分色的 ASS 字幕檔。預設設定下整個流程**完全在本地完成**，原始影片與字幕都不會離開你的機器。

### 主要功能

- **多模型轉寫**：預設 `large-v3-turbo` 主辨識 + `medium` 交叉比對，時間軸由主模型決定、文本由多模型投票融合
- **說話人辨識**：基於 sherpa-onnx 的離線 diarization，自動為不同說話人配上區分色
- **本地翻譯引擎**：預設 Index-Translate-2B（B站官方模型，vLLM 本地推理，零 API 費用）；也可一鍵切回 DeepSeek / Anthropic 雲端
- **術語表（Glossary）**：`dictionary/*.yaml` 分主題詞典，只把本批命中的術語作為**硬約束**下發給模型，人名團名不被擅自改寫
- **譯文校驗**：條數/id 對應/空譯文/Markdown/解釋文字/術語落實逐條檢查，不過則重試、縮批，而不是把錯譯文靜默寫進字幕
- **多種字幕格式**：雙語合併、雙語分軌、日文單軌、中文單軌、說話人分色雙語
- **多種輸入**：YouTube / Niconico 連結（自動下載）或本地影片檔
- **Web 介面**：Flask 單頁 UI，雙擊啟動後瀏覽器自動開啟，無需安裝用戶端
- **10 套回歸測試 / 399 條斷言**（參見 `AGENTS.md`）

### 安裝與使用

#### 必要條件

1. **Python 3.13+**
2. **FFmpeg**：執行檔加入 PATH，或安裝到 `C:\ffmpeg\`。
   - Windows 推薦：`winget install Gyan.FFmpeg`

#### 安裝步驟

```bash
git clone <repo-url>
cd jsub-translator
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

#### 設定

將 `.env.example` 複製為 `.env`，填入 DeepSeek 或 Anthropic 的 API Key：

```bash
cp .env.example .env       # macOS/Linux
copy .env.example .env     # Windows
```

#### 啟動

```bash
python app.py
```

瀏覽器自動開啟 `http://127.0.0.1:7860`。

**預設引擎是本地 Index-Translate-2B**，需先啟動本地服務（WSL2 + vLLM，見 [`docs/index-translate.md`](docs/index-translate.md)）：

```bash
vllm serve IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8000 --max-model-len 32768
```

然後：選擇影片連結或本地檔案 → 點「開始處理」。
不想裝本地服務的話，在「翻譯引擎」下拉選單換成 DeepSeek 並填入 API Key 即可。

### 目錄結構

```
.
├── AGENTS.md           開發者 / AI agent 導覽、硬規則、常用命令
├── README.md           本檔（四語並排專案介紹）
├── app.py              Flask 入口、路由、SSE 進度推送
├── build.bat           一鍵打包 EXE（PyInstaller）
├── requirements.txt    Python 依賴
├── jsub-translator.spec            PyInstaller 設定
├── jpop_glossary.json.example      日語術語表範例（複製為 jpop_glossary.json 啟用）
├── src/
│   ├── config.py              設定載入、路徑解析、provider 校驗
│   ├── audio.py               ffmpeg 呼叫、錯誤訊息收口
│   ├── transcriber.py         Whisper 多模型辨識
│   ├── diarizer.py            說話人辨識（sherpa-onnx）
│   ├── translator.py          LLM 呼叫、翻譯/校對、重試/容錯
│   ├── fusion.py              多模型結果投票融合
│   ├── ass_writer.py          ASS 字幕產生（含配色表權威定義）
│   ├── cache.py               輸出目錄統計與清理
│   ├── downloader.py          yt-dlp 包裝、cookies 自檢
│   └── pipeline.py            主流程編排、階段進度上報
├── web/
│   └── templates/index.html   單頁 UI（HTML + CSS + 原生 JS）
├── docs/                深入文件
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   ├── TROUBLESHOOTING.md
│   ├── STATE.md
│   └── screenshots/      UI 截圖
├── dictionary/          術語表（*.yaml，分 terms / people / groups 三節）
├── tests/
│   ├── translation/     翻譯用例跑批器
│   └── benchmark/       Index vs DeepSeek 對比基準
├── tools/               維護用腳本（ASS 對齊修復等）
├── _test_*.py           10 套回歸測試（399 條斷言）
└── .env.example         設定範本（不要提交 .env）
```

---

## English

### Project Overview

jsub-translator is a **locally-run** subtitle translator for Japanese videos. It transcribes YouTube / Niconico links or local files into Japanese timings, then translates them into Chinese with a **local** large language model (Index-Translate-2B), and finally writes ASS subtitle files with per-speaker colors. With the default configuration the whole pipeline runs **entirely on your machine** — neither the video nor the subtitles ever leave your computer.

### Key Features

- **Multi-model transcription** — `large-v3-turbo` is the primary model, with `medium` cross-checking in parallel. The primary model owns timings; the text is fused from multiple model votes
- **Speaker diarization** — offline diarization powered by sherpa-onnx, with a deterministic palette that distinguishes each speaker
- **Local translation engine** — Index-Translate-2B by default (Bilibili's official model, served locally via vLLM, zero API cost); switch back to DeepSeek / Anthropic in one click
- **Glossary** — topic-split YAML dictionaries under `dictionary/`; only the terms actually present in a batch are sent to the model, as **hard constraints**, so names and group names are never rewritten
- **Translation validation** — count / id mapping / empty output / Markdown / explanatory text / glossary compliance are all checked; failures trigger a retry and batch shrinking instead of silently writing bad subtitles
- **Multiple subtitle formats** — bilingual merged, bilingual split (separate JP / CN lines), Japanese-only, Chinese-only, and per-speaker colored bilingual
- **Flexible input** — YouTube / Niconico links (auto-downloaded) or local video files
- **Web UI** — Flask single-page UI; browser opens automatically when launched, no client install required
- **10 regression suites / 399 assertions** (see `AGENTS.md`)

### Installation & Usage

#### Prerequisites

1. **Python 3.13+**
2. **FFmpeg** — add the executable to PATH, or install into `C:\ffmpeg\`.
   - Windows recommended: `winget install Gyan.FFmpeg`

#### Setup

```bash
git clone <repo-url>
cd jsub-translator
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

#### Configuration

Copy `.env.example` to `.env` and fill in your DeepSeek or Anthropic API key:

```bash
cp .env.example .env       # macOS/Linux
copy .env.example .env     # Windows
```

#### Launch

```bash
python app.py
```

Your browser opens `http://127.0.0.1:7860` automatically.

**The default engine is the local Index-Translate-2B**, so start the local server first (WSL2 + vLLM, see [`docs/index-translate.md`](docs/index-translate.md)):

```bash
vllm serve IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8000 --max-model-len 32768
```

Then choose a video link or a local file and click "Start".
If you would rather not run a local server, pick DeepSeek in the "Translation Backend" dropdown and enter an API Key instead.

### Directory Structure

```
.
├── AGENTS.md           Navigation for developers / AI agents, hard rules, common commands
├── README.md           This file (project intro in four languages, side by side)
├── app.py              Flask entry, routes, SSE progress stream
├── build.bat           One-shot EXE build (PyInstaller)
├── requirements.txt    Python dependencies
├── jsub-translator.spec            PyInstaller spec
├── jpop_glossary.json.example      Japanese glossary example (copy to jpop_glossary.json to enable)
├── src/
│   ├── config.py              Config loading, path resolution, provider validation
│   ├── audio.py               ffmpeg wrapper, stderr cleanup
│   ├── transcriber.py         Whisper multi-model recognition
│   ├── diarizer.py            Speaker diarization (sherpa-onnx)
│   ├── translator.py          LLM calls, translation / review, retry / resilience
│   ├── fusion.py              Multi-model result vote fusion
│   ├── ass_writer.py          ASS subtitle generation (authoritative color palette)
│   ├── cache.py               Output directory stats and cleanup
│   ├── downloader.py          yt-dlp wrapper, cookies self-check
│   └── pipeline.py            Pipeline orchestration, phase progress reporting
├── web/
│   └── templates/index.html   Single-page UI (HTML + CSS + vanilla JS)
├── docs/                Deep-dive documentation
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   ├── TROUBLESHOOTING.md
│   ├── STATE.md
│   └── screenshots/      UI screenshots
├── dictionary/          Glossaries (*.yaml with terms / people / groups sections)
├── tests/
│   ├── translation/     Translation case runner
│   └── benchmark/       Index vs DeepSeek benchmark
├── tools/               Maintenance scripts (ASS alignment repair, etc.)
├── _test_*.py           10 regression suites (399 assertions)
└── .env.example         Config template (do NOT commit .env)
```

---

## 日本語

### プロジェクト概要

jsub-translator は**ローカル実行型**の日本語動画字幕翻訳ツールです。YouTube / Niconico のリンクまたはローカル動画を、まず日本語のタイミングへ文字起こしし、続いて**ローカル**の大規模言語モデル（Index-Translate-2B）で中国語へ翻訳、最後に話者別の色分け付き ASS 字幕ファイルを生成します。既定の設定では工程のすべてが**完全にローカル**で実行され、元動画も字幕も外部へ送信されません。

### 主な機能

- **複数モデルでの文字起こし** — 主モデル `large-v3-turbo` と並列の `medium` で相互検証。タイミングは主モデル、テキストは複数モデルの投票で統合
- **話者識別** — sherpa-onnx によるオフライン diarization。話者ごとに区別しやすい色を決定論的に割り当て
- **ローカル翻訳エンジン** — 既定は Index-Translate-2B（Bilibili 公式モデル、vLLM でローカル推論、API 費用ゼロ）。DeepSeek / Anthropic へワンクリックで切り替え可能
- **用語集（Glossary）** — `dictionary/*.yaml` の分野別辞書。そのバッチに実際に出現した用語だけを**ハード制約**としてモデルへ渡すため、人名・グループ名が勝手に書き換わりません
- **訳文バリデーション** — 件数 / id 対応 / 空訳 / Markdown / 説明文 / 用語遵守を検査し、不合格なら再試行とバッチ縮小を行います（誤った字幕を黙って書き出しません）
- **複数の字幕形式** — 二語統合、二語分割（日中別行）、日本語のみ、中国語のみ、話者別色分け二語
- **多様な入力** — YouTube / Niconico のリンク（自動ダウンロード）またはローカル動画ファイル
- **Web UI** — Flask の単一ページ UI。起動時にブラウザが自動で開き、クライアントのインストールは不要
- **回帰テスト 10 スイート / 399 アサーション**（`AGENTS.md` を参照）

### インストールと使い方

#### 前提条件

1. **Python 3.13+**
2. **FFmpeg** — 実行ファイルを PATH に追加するか、`C:\ffmpeg\` にインストール。
   - Windows 推奨：`winget install Gyan.FFmpeg`

#### セットアップ

```bash
git clone <repo-url>
cd jsub-translator
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

#### 設定

`.env.example` を `.env` にコピーし、DeepSeek または Anthropic の API Key を記入：

```bash
cp .env.example .env       # macOS/Linux
copy .env.example .env     # Windows
```

#### 起動

```bash
python app.py
```

ブラウザが自動で `http://127.0.0.1:7860` を開きます。

**既定のエンジンはローカルの Index-Translate-2B** です。先にローカルサービスを起動してください（WSL2 + vLLM、[`docs/index-translate.md`](docs/index-translate.md) 参照）：

```bash
vllm serve IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8000 --max-model-len 32768
```

その後、動画リンクかローカルファイルを選択 →「開始処理」をクリック。
ローカルサービスを用意しない場合は、「翻訳エンジン」のドロップダウンで DeepSeek を選び API Key を入力してください。

### ディレクトリ構成

```
.
├── AGENTS.md           開発者 / AI agent 向け案内、必須ルール、よく使うコマンド
├── README.md           本ファイル（4 言語並列のプロジェクト紹介）
├── app.py              Flask エントリ、ルーティング、SSE 進捗配信
├── build.bat           EXE ワンクリックビルド（PyInstaller）
├── requirements.txt    Python 依存パッケージ
├── jsub-translator.spec            PyInstaller 設定
├── jpop_glossary.json.example      日本語用語集サンプル（jpop_glossary.json にコピーして有効化）
├── src/
│   ├── config.py              設定読み込み、パス解決、プロバイダ検証
│   ├── audio.py               ffmpeg 呼び出し、エラーメッセージ整理
│   ├── transcriber.py         Whisper 複数モデル認識
│   ├── diarizer.py            話者識別（sherpa-onnx）
│   ├── translator.py          LLM 呼び出し、翻訳 / 校正、リトライ / 耐性
│   ├── fusion.py              複数モデル結果の投票統合
│   ├── ass_writer.py          ASS 字幕生成（配色の唯一の権威定義）
│   ├── cache.py               出力ディレクトリの統計とクリーンアップ
│   ├── downloader.py          yt-dlp ラッパ、cookies 自己診断
│   └── pipeline.py            パイプライン調整、段階進捗報告
├── web/
│   └── templates/index.html   単一ページ UI（HTML + CSS + 素の JS）
├── docs/                詳細ドキュメント
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   ├── TROUBLESHOOTING.md
│   ├── STATE.md
│   └── screenshots/      UI スクリーンショット
├── dictionary/          用語集（*.yaml、terms / people / groups の3節）
├── tests/
│   ├── translation/     翻訳ケース実行スクリプト
│   └── benchmark/       Index vs DeepSeek 比較ベンチマーク
├── tools/               メンテナンス用スクリプト（ASS 位置修正など）
├── _test_*.py           回帰テスト 10 スイート（399 アサーション）
└── .env.example         設定テンプレート（.env はコミットしない）
```

---

## License

[MIT](LICENSE) © 2026 Haku-Hisago
