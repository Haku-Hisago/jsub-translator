"""
Configuration management for jsub-translator.
Loads settings from environment variables and .env file.

Translation providers: deepseek (default) or anthropic.
"""

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent.parent

# 占位符黑名单：模板里用来提示「请填入你的 key」的假值。
# 一旦这些值被当成真 key 发出去，API 只会回 401，前端只看到「请求失败」，
# 极难定位。所以在这里显式识别并当作「未配置」处理。
_PLACEHOLDER_KEY_MARKERS = (
    "your-deepseek-key",
    "your-anthropic-key",
    "sk-xxx",
    "here",
)


def _env_int(name: str, default: int) -> int:
    """读整数配置；值非法时**退回默认值并警告**，而不是让应用起不来。

    裸写 `int(os.getenv(...))` 的后果：`.env` 里一个笔误（如
    `FUSE_BATCH_SIZE=auto`）会让整个应用在**导入阶段**就抛 ValueError。
    实测报错是 `invalid literal for int() with base 10: 'auto'` 加一段
    config.py 的 traceback —— 打包版被双击启动时用户根本看不到控制台，
    表现为「点了没反应」。

    这类可调参数写错不该让工具打不开：退回默认值 + 明确警告，
    让用户至少能进界面，并在启动日志里看到自己写错了哪一项。
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        print(f"[config] ⚠ {name}={raw!r} 不是整数，已改用默认值 {default}。"
              f"请修正 .env 中的这一项。", file=sys.stderr)
        return default


def _env_float(name: str, default: float) -> float:
    """同 `_env_int`，用于浮点配置。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        print(f"[config] ⚠ {name}={raw!r} 不是数字，已改用默认值 {default}。"
              f"请修正 .env 中的这一项。", file=sys.stderr)
        return default


def _looks_like_placeholder(val: Optional[str]) -> bool:
    """判断一个 key 是否是模板占位符（而非真实凭据）。"""
    if not val:
        return False
    v = val.strip().lower()
    if not v:
        return False
    return any(m in v for m in _PLACEHOLDER_KEY_MARKERS)


def _load_env_files() -> list[Path]:
    """按优先级加载 .env 文件，返回实际生效的路径（用于诊断）。

    顺序很重要：**后加载的优先级更高**，因为 load_dotenv 默认不覆盖
    已存在的变量（override=False）。所以「更具体的配置」必须放在后面。

    冻结版（EXE）的加载顺序：
      1. 解包目录里的 .env —— 打包进 EXE 的模板副本，优先级**最低**
      2. EXE 同目录的 .env —— 用户自己的配置，优先级**最高**

    早期版本把 EXE 目录的 .env 放在**前面**加载，结果 dist/.env 里一份
    没改过的模板占位符 `sk-your-deepseek-key-here` 挡住了项目根 .env 里
    的真实 key —— 表现为「一启动就请求失败」。
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        # 冻结版：ROOT_DIR 指向解包临时目录，那里的 .env 是打包进去的副本，
        # 优先级最低，所以先加载。
        candidates.append(ROOT_DIR / ".env")
        candidates.append(Path(sys.executable).parent / ".env")
    else:
        candidates.append(ROOT_DIR / ".env")

    loaded: list[Path] = []
    for p in candidates:
        if p.is_file():
            load_dotenv(p)
            loaded.append(p)
    return loaded


ENV_FILES = _load_env_files()

# Resource directory for files bundled INTO the EXE (onefile extracts them to
# a temp dir that changes every launch). Bundled diarization models live here;
# user-downloaded Whisper models live next to the EXE instead.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", ROOT_DIR))


def _anchor_dir() -> Path:
    """基准目录：用于解析所有相对路径。

    冻结版用 EXE 所在目录（onefile 的 _MEIPASS 每次启动都变，不能用）；
    源码版用项目根目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT_DIR


def _resolve_dir(val: str, default_name: str) -> Path:
    """把配置里的目录值解析成绝对路径。

    空值/未设置 -> 基准目录/default_name
    相对路径    -> 基准目录/该路径
    绝对路径    -> 原样（归一化后）
    """
    raw = (val or "").strip()
    if not raw:
        return (_anchor_dir() / default_name).resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _anchor_dir() / p
    return p.resolve()


def _default_model_dir() -> Path:
    """Where user-downloaded models (Whisper) are stored and looked up.

    In a frozen build ``ROOT_DIR`` points inside the onefile temp extraction
    dir — a fresh path every launch, so nothing cached there survives. The
    EXE's own folder is the stable place, and that is where the previous
    builds already keep ``models/``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "models"
    return ROOT_DIR / "models"

# --- Translation Provider ---
# "index"（本地 Index-Translate-2B，默认）| "deepseek" | "anthropic"
#
# 默认已改为 index：本地推理、零 API 费用、术语可控。
# 若本地服务没起，不会静默失败 —— 会给出明确提示（见 validate_config）。
# 想切回云端，在 .env 里写一行 TRANSLATION_PROVIDER=deepseek 即可。
TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "index")

# 可用的翻译引擎。**只在这里定义一次。**
# 之前这份清单被抄了四份：resolve_api_key / validate_config 各写一遍 if/elif，
# translator.create_translator 又写一遍，网页的 <option> 还是手写死的 ——
# 加第三个引擎要改四处，漏一处就是「后端支持但界面选不到」或「校验过了却拿不到翻译器」。
VALID_PROVIDERS = ("index", "deepseek", "anthropic")

# --- Index-Translate (本地 vLLM) ---
#
# 官方仓库：https://github.com/bilibili/Index-Translate
# 模型：IndexTeam/Index-Translate-2B（基于 Qwen3.5）
#
# 起服务（WSL2 / Linux，需带 Qwen3.5 支持的 vLLM）：
#     vllm serve IndexTeam/Index-Translate-2B \
#         --host 127.0.0.1 --port 8000 --max-model-len 4096
# 官方默认 4096 是短文本示例值；本项目按批翻译、上下文较长，
# 建议按官方部署预设提到 32768（见 docs/index-translate.md）。
#
# 服务是 OpenAI 兼容接口，因此直接复用 openai SDK，不需要额外依赖。
INDEX_BASE_URL = os.getenv("INDEX_BASE_URL", "http://127.0.0.1:8000/v1").strip()

# 本地 vLLM 不校验 key，官方约定填 EMPTY。**这不是密钥**，不要往里填真 key。
INDEX_API_KEY = os.getenv("INDEX_API_KEY", "EMPTY").strip() or "EMPTY"

# 模型 ID 必须与服务端 /v1/models 返回的一致。
# 官方 serve 命令用的就是 IndexTeam/Index-Translate-2B。
# 若你部署的是量化版（如 -FP8），改这里即可。
INDEX_MODEL = os.getenv("INDEX_MODEL", "IndexTeam/Index-Translate-2B").strip()

# 官方默认 max_tokens=1024（Translate 系列）；temperature=0 贪心解码。
INDEX_MAX_TOKENS = _env_int("INDEX_MAX_TOKENS", 1024)
INDEX_TEMPERATURE = _env_float("INDEX_TEMPERATURE", 0.0)

# 本地推理可能较慢（尤其首次加载/长批），超时给宽一些
INDEX_TIMEOUT = _env_int("INDEX_TIMEOUT", 300)

# 是否要求模型跳过思维链。官方默认 enable_thinking=False。
INDEX_ENABLE_THINKING = os.getenv("INDEX_ENABLE_THINKING", "false").lower() in (
    "1", "true", "yes", "on",
)

# 每批字幕条数。官方未限定；10~30 是准确性与吞吐的平衡区。
INDEX_BATCH_SIZE = _env_int("INDEX_BATCH_SIZE", 20)

# 批与批之间携带的上下文条数（只用于理解语境，模型不翻译它们）。
# before = 已翻好的前文；after = 尚未翻译的后文（只给模型看，不要求它翻）。
INDEX_CONTEXT_BEFORE = _env_int("INDEX_CONTEXT_BEFORE", 3)
INDEX_CONTEXT_AFTER = _env_int("INDEX_CONTEXT_AFTER", 3)

# 术语表（dynamic glossary）
INDEX_GLOSSARY_ENABLED = os.getenv("INDEX_GLOSSARY_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# 词典目录（相对基准目录解析）。目录下所有 *.yaml / *.yml 都会被加载。
INDEX_GLOSSARY_DIRS = os.getenv("INDEX_GLOSSARY_DIRS", "dictionary").strip()

# 译文校验与重试
INDEX_VALIDATION_ENABLED = os.getenv("INDEX_VALIDATION_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# 校验不通过时的重试次数（0 = 只校验不重试）
INDEX_VALIDATION_RETRY = _env_int("INDEX_VALIDATION_RETRY", 2)

# 字幕长度上限（相对日文原文的字符数倍率）。超过则请求模型压缩。
# 中文通常比日文短，1.8 倍已相当宽松，只拦「明显啰嗦」的情况。
INDEX_MAX_LENGTH_RATIO = _env_float("INDEX_MAX_LENGTH_RATIO", 1.8)

# --- Fallback（翻译失败时改用的备用引擎）---
# 默认**关闭**：只有用户主动打开才会去调用云端 API（会产生费用）。
FALLBACK_ENABLED = os.getenv("FALLBACK_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "deepseek").strip().lower()

# --- DeepSeek API ---
# 注意：占位符（sk-your-deepseek-key-here）会被当成「未配置」处理为 ""，
# 这样 validate_config 会给出「请填 key」的明确提示，而不是发出请求拿到 401。
_raw_ds_key = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_KEY = "" if _looks_like_placeholder(_raw_ds_key) else _raw_ds_key.strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# --- Anthropic API (optional) ---
_raw_an_key = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_KEY = "" if _looks_like_placeholder(_raw_an_key) else _raw_an_key.strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# --- Whisper Settings ---
# 主转录模型：负责字幕的时间轴/分段骨架，应选质量最高者。
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")

# 参考转录模型（逗号分隔）：与主模型并行识别，供大模型交叉比对验证。
WHISPER_REFERENCE_MODELS = [
    m.strip() for m in os.getenv("WHISPER_REFERENCE_MODELS", "medium").split(",") if m.strip()
]

MODEL_DIR = _resolve_dir(os.getenv("MODEL_DIR", ""), "models")


def _bundled_or(model_dir_path: Path, bundled_name: str) -> str:
    """Prefer a model that ships inside the EXE, else look in MODEL_DIR.

    Lets a user override by dropping their own copy in ``models/`` while the
    normal case needs no configuration at all.
    """
    local = model_dir_path / bundled_name
    if local.exists():
        return str(local)
    return str(BUNDLE_DIR / "models" / bundled_name)

# --- Whisper 推理设备 ---
# WHISPER_DEVICE: "cuda"（NVIDIA GPU）或 "cpu"
# WHISPER_COMPUTE_TYPE: GPU 用 "float16"；CPU 用 "int8"
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

# --- Multi-ASR Fusion ---
# 多路转写融合（比对验证）使用的 LLM 引擎；留空则与翻译引擎相同。
FUSE_PROVIDER = os.getenv("FUSE_PROVIDER", "").strip()
# 融合阶段每次 API 调用的字幕条数
FUSE_BATCH_SIZE = _env_int("FUSE_BATCH_SIZE", 15)

# --- Output ---
#
# 输出目录必须锚定到一个**稳定**的位置，不能依赖进程的当前工作目录（CWD）。
#
# 之前写成 Path(os.getenv("OUTPUT_DIR", ROOT_DIR / "output")).resolve()，
# 而 .env 里配的是相对路径 OUTPUT_DIR=./output —— 于是实际解析结果跟着
# CWD 走：
#     用 run.bat 启动（CWD=dist）   -> dist\output        ← 成品在这里
#     直接双击 EXE（CWD 不确定）    -> 另一个目录
#     从项目根目录跑（CWD=根）      -> output\（空的）
# 结果就是「打开输出文件夹」打开的不是放着成品的那一个；若 CWD 指向
# 只读位置（如 C:\Windows\System32），mkdir 还会失败报错。
#
# 现在：相对路径一律相对于**基准目录**解析，基准目录 =
#   冻结版：EXE 所在目录（稳定，且是用户实际存放产物的位置）
#   源码版：项目根目录
# 绝对路径仍然照用（向后兼容）。
OUTPUT_DIR = _resolve_dir(os.getenv("OUTPUT_DIR", ""), "output")

# --- Language ---
SOURCE_LANG = "ja"       # Japanese
TARGET_LANG = "zh"       # Chinese

# --- Downloader (yt-dlp) ---
#
# 为什么要配 cookies：YouTube 从 2024 年起对未登录的下载做机器人校验，
# 匿名请求会直接失败（报 "Sign in to confirm you're not a bot" 或
# "No video formats found!"）。Bilibili / Niconico 等站点通常不受影响。
# 带上浏览器 cookies（哪怕只是"已登录过 YouTube"的会话）就能通过校验。
#
# 两种给 cookies 的方式，**二选一**，都留空则维持原行为（不发送 cookies）：
#
#   1) YTDLP_COOKIES_FILE —— 指向一个 Netscape 格式的 cookies.txt
#      最可靠。用浏览器扩展导出（推荐 "Get cookies.txt LOCALLY"）。
#      相对路径相对「程序所在目录」解析。
#
#   2) YTDLP_COOKIES_FROM_BROWSER —— 直接读本机浏览器的 cookie 库
#      可填：edge / chrome / firefox / brave / chromium / opera / vivaldi / whale
#      注意：Chrome/Edge 新版启用了 App-Bound 加密，且浏览器运行时 cookie
#      库会被锁定，读取经常失败（报 DPAPI / Could not copy 错误）。
#      能用就用，不能用请改用方式 1。
#
# YTDLP_COOKIES_FILE 优先级高于 YTDLP_COOKIES_FROM_BROWSER。
_YTDLP_COOKIES_FILE_RAW = os.getenv("YTDLP_COOKIES_FILE", "").strip()
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip().lower()

# 解析成绝对路径（与 OUTPUT_DIR 同样的锚定规则），文件不存在时给出提示而不静默忽略
YTDLP_COOKIES_FILE = ""
if _YTDLP_COOKIES_FILE_RAW:
    _p = Path(_YTDLP_COOKIES_FILE_RAW).expanduser()
    if not _p.is_absolute():
        _p = _anchor_dir() / _p
    YTDLP_COOKIES_FILE = str(_p.resolve())

# yt-dlp 网络重试次数（站点偶发抽风时自动重试，避免整单失败）
YTDLP_RETRIES = _env_int("YTDLP_RETRIES", 3)

# --- Speaker Diarization ---
#
# 说话人识别（声纹分离）配置。
#
# 原理说明：Whisper 本身没有声纹能力，只能给出「说了什么 + 什么时候说」。
# 「谁在说」必须由独立的声纹模型解决，再按时间轴与字幕对齐。
#
# 默认后端 sherpa = sherpa-onnx（ONNX Runtime，纯 CPU）：
#   - 新增依赖仅约 20 MB + 模型 44 MB，不需要 torch（省下 3 GB）
#   - 复用 pyannote 的 segmentation-3.0 模型（ONNX 版），效果同源
#   - 模型放在 GitHub Releases，无需 HuggingFace Token，无 gated 门槛
#   - 纯 CPU 推理，不与 Whisper 争抢 8 GB 显存
# 备用后端 pyannote = pyannote.audio（需要额外安装，体积大且需 HF Token）。
#
# DIARIZE_BACKEND: "sherpa"（默认）| "pyannote" | "auto"（依次尝试）| "none"（关闭）
DIARIZE_BACKEND = os.getenv("DIARIZE_BACKEND", "sherpa").strip().lower()

# 说话人分割模型（sherpa-onnx 格式的 pyannote segmentation-3.0）
# 留空则优先用 models/ 下的副本，找不到就用随 EXE 打包的那份。
DIARIZE_SEG_MODEL = os.getenv(
    "DIARIZE_SEG_MODEL",
    _bundled_or(MODEL_DIR, "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"),
)

# 声纹嵌入模型（3D-Speaker ERes2Net，中/日文场景通用）
DIARIZE_EMB_MODEL = os.getenv(
    "DIARIZE_EMB_MODEL",
    _bundled_or(
        MODEL_DIR, "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    ),
)

# 已知说话人数量时直接指定（-1 = 自动聚类）
DIARIZE_NUM_SPEAKERS = _env_int("DIARIZE_NUM_SPEAKERS", -1)

# 自动聚类阈值（仅当 DIARIZE_NUM_SPEAKERS = -1 时生效）
# 值越小 → 聚类越多 → 说话人越多；值越大 → 说话人越少。
#
# 默认 0.92（而非 sherpa 示例里的 0.5）：实测在官方 4 人测试音频上
# （turns 恒为 10，只有聚类结果随阈值变化）：
#     0.50 -> 7 人   0.60 -> 7 人   0.70 -> 5 人   0.80~0.86 -> 5 人
#     0.88~0.96 -> 4 人 ✓             1.00 -> 3 人
# 取 0.92 是有意落在实测平台区（0.88~0.96）的**正中**，
# 而不是边缘 —— 边缘值（如 0.8）在不同音频上会翻到 5 人。
# 若发现角色被拆得太碎，往上调到 0.96；若不同角色被合并，往下调到 0.88。
DIARIZE_CLUSTER_THRESHOLD = _env_float("DIARIZE_CLUSTER_THRESHOLD", 0.92)

# 一条字幕横跨两个说话人时，是否用词级时间戳切成两条。
# 需要 transcriber 打开 word_timestamps；若词级时间戳与原文对不齐
# （large-v3-turbo 的 DTW 精度较粗），会自动跳过切分。
DIARIZE_SPLIT_ON_CHANGE = os.getenv("DIARIZE_SPLIT_ON_CHANGE", "true").lower() in (
    "1", "true", "yes", "on",
)

# HuggingFace token —— 仅 `pyannote` 后端需要，sherpa 后端不需要。
HF_TOKEN = os.getenv("HF_TOKEN", "")

# --- Audio Settings ---
AUDIO_SAMPLE_RATE = 16000  # 16kHz for Whisper

# --- Translation ---
TRANSLATION_BATCH_SIZE = 15  # Number of segments per API call

# --- Context & Coherence ---
# 跨批次携带的「上文语境」条数（帮助解决指代/话题/术语跨批次一致）。
# 0 或负数 = 关闭上下文携带。
CONTEXT_WINDOW = _env_int("CONTEXT_WINDOW", 4)

# 是否在翻译完成后运行一次 LLM 连贯性校对（去翻译腔、统一指代与术语）。
ENABLE_REVIEW = os.getenv("ENABLE_REVIEW", "true").lower() in ("1", "true", "yes", "on")

# 校对阶段使用的引擎；留空则与翻译引擎相同。
# 可填 "deepseek" / "anthropic"（例如：deepseek 粗翻 + anthropic 校对）。
REVIEW_PROVIDER = os.getenv("REVIEW_PROVIDER", "").strip()

# 校对阶段每次 API 调用的字幕条数
REVIEW_BATCH_SIZE = _env_int("REVIEW_BATCH_SIZE", 15)


def ensure_dirs():
    """Create necessary directories if they don't exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


def resolve_api_key(provider: Optional[str] = None, api_key: Optional[str] = None) -> str:
    """Resolve the effective API key for a provider.

    Priority: explicit runtime key (e.g. typed into the web UI) > the
    provider's environment variable. Returns "" when neither is present.

    Args:
        provider: "deepseek" / "anthropic". Defaults to TRANSLATION_PROVIDER.
        api_key: Runtime key supplied by the user.
    """
    provider = (provider or TRANSLATION_PROVIDER).lower().strip()
    if api_key and api_key.strip():
        return api_key.strip()
    if provider == "index":
        return INDEX_API_KEY
    if provider == "deepseek":
        return DEEPSEEK_API_KEY
    if provider == "anthropic":
        return ANTHROPIC_API_KEY
    return ""


def probe_index_service(timeout: float = 3.0) -> tuple[bool, str, list[str]]:
    """探测本地 Index-Translate 服务是否在跑。

    返回 (可用, 说明, 模型ID列表)。

    为什么要单独探一次：本地服务没起时，openai SDK 抛的是
    `APIConnectionError` / `Connection refused`，用户看到这行字完全不知道
    该怎么办。这里提前问一句 `/v1/models`，把「连不上」翻译成
    「你的本地服务没启动，请先跑 vllm serve ...」。

    用标准库 urllib 而不是 openai/httpx：这只是个健康检查，
    不该为此付出 import 依赖的代价，也不该受 SDK 重试策略影响。
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = INDEX_BASE_URL.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {INDEX_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return False, f"服务有响应但返回 HTTP {e.code}", []
    except Exception as e:  # noqa: BLE001 - 连接类错误种类多，统一成一句人话
        return False, f"无法连接（{type(e).__name__}）", []

    ids: list[str] = []
    if isinstance(payload, dict):
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    return True, "服务在线", ids


def validate_config(provider: Optional[str] = None, api_key: Optional[str] = None):
    """Validate that required configuration is present for the active provider.

    Args:
        provider: Override for TRANSLATION_PROVIDER (e.g. from the web UI).
        api_key: Runtime key supplied by the user (web UI). Takes precedence
                 over the provider's environment variable.
    """
    provider = (provider or TRANSLATION_PROVIDER).lower().strip()

    if provider == "index":
        # 本地服务不需要 API Key，但**必须真的在跑**。
        # 这里不抛 Connection refused 那种天书，而是给出可直接照做的提示。
        ok, detail, model_ids = probe_index_service()
        if not ok:
            raise ValueError(
                "Index-Translate service is not running.\n"
                f"  预期地址：{INDEX_BASE_URL}\n"
                f"  探测结果：{detail}\n"
                "  请先启动本地 Index-Translate-2B 服务：\n"
                "    vllm serve IndexTeam/Index-Translate-2B "
                "--host 127.0.0.1 --port 8000 --max-model-len 32768\n"
                "  部署步骤见 docs/index-translate.md。\n"
                "  若想改用云端引擎，在 .env 里设置 TRANSLATION_PROVIDER=deepseek。"
            )
        # 服务在线但模型名对不上 → 这是最容易踩的坑（模型名写错时
        # 请求会回 404 model not found，同样很难懂）。顺手校验一下。
        if model_ids and INDEX_MODEL not in model_ids:
            raise ValueError(
                f"本地服务已启动，但没有名为 '{INDEX_MODEL}' 的模型。\n"
                f"  服务实际提供：{', '.join(model_ids)}\n"
                f"  请把 .env 里的 INDEX_MODEL 改成上面之一，"
                f"或按该名称重新部署模型。"
            )
    elif provider == "deepseek":
        if not resolve_api_key(provider, api_key):
            raise ValueError(
                "未配置 DeepSeek API Key。请在网页「API Key」输入框填入，"
                "或写入 .env：\n"
                "  1. 注册 DeepSeek: https://platform.deepseek.com/\n"
                "  2. 获取 API Key: https://platform.deepseek.com/api_keys\n"
                "  3. 填入 DEEPSEEK_API_KEY=sk-xxx"
            )
    elif provider == "anthropic":
        if not resolve_api_key(provider, api_key):
            raise ValueError(
                "未配置 Anthropic API Key。请在网页「API Key」输入框填入，"
                "或写入 .env：\n"
                "  1. 注册 Anthropic: https://console.anthropic.com/\n"
                "  2. 获取 API Key\n"
                "  3. 填入 ANTHROPIC_API_KEY=sk-ant-xxx"
            )
    else:
        raise ValueError(
            f"Unknown TRANSLATION_PROVIDER '{provider}'. "
            f"Valid options: {', '.join(VALID_PROVIDERS)}"
        )

    ensure_dirs()


def validate_diarization() -> tuple[bool, str]:
    """Check whether the configured diarization backend is usable.

    Returns:
        (available, reason) — `reason` is a human-readable Chinese message
        describing why diarization is unavailable when `available` is False.
    """
    if DIARIZE_BACKEND == "none":
        return False, "说话人识别已通过 DIARIZE_BACKEND=none 关闭"

    if DIARIZE_BACKEND in ("sherpa", "auto"):
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            if DIARIZE_BACKEND == "sherpa":
                return False, "sherpa-onnx 未安装（pip install sherpa-onnx）"
        else:
            missing = [
                p for p in (Path(DIARIZE_SEG_MODEL), Path(DIARIZE_EMB_MODEL))
                if not p.exists()
            ]
            if not missing:
                return True, "sherpa-onnx"
            if DIARIZE_BACKEND == "sherpa":
                names = "、".join(p.name for p in missing)
                return False, f"缺少说话人模型文件：{names}"

    if DIARIZE_BACKEND in ("pyannote", "auto"):
        try:
            import pyannote.audio  # noqa: F401
        except ImportError:
            if DIARIZE_BACKEND == "pyannote":
                return False, "pyannote.audio 未安装"
        else:
            if HF_TOKEN:
                return True, "pyannote.audio"
            if DIARIZE_BACKEND == "pyannote":
                return False, "pyannote 后端需要 HF_TOKEN"

    return False, "没有可用的说话人识别后端"
