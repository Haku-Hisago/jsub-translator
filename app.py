"""
jsub-translator Standalone EXE Application
Flask web app bundled into a single executable via PyInstaller.

Usage:
    jsub-translator.exe          # Double-click to launch
    Browser opens http://127.0.0.1:7860
"""

import sys
import os
import json
import queue
import threading
import uuid
import webbrowser
from pathlib import Path


# ── PyInstaller path resolution ──
def _base_dir():
    """Get the base directory (works both dev and PyInstaller bundled)."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _template_dir():
    """Get the templates directory."""
    return _base_dir() / "web" / "templates"


# Add src to path
sys.path.insert(0, str(_base_dir()))

from flask import Flask, render_template, request, jsonify, Response, send_file
from flask import stream_with_context

from src.config import (
    WHISPER_MODEL, WHISPER_REFERENCE_MODELS, TRANSLATION_PROVIDER,
    VALID_PROVIDERS, ENABLE_REVIEW, ensure_dirs, OUTPUT_DIR,
    validate_diarization, validate_config,
)
from src.pipeline import process
from src.cache import cache_stats, clean_cache, open_cache_folder
from src.ass_writer import get_speaker_css_color as _speaker_css_color
from src.ass_writer import _short_speaker

# Create Flask app with correct template folder
app = Flask(__name__, template_folder=str(_template_dir()))

# Store active jobs
_jobs = {}
_lock = threading.Lock()

# 只保留最近这么多个「已结束」的任务。
# _jobs 若只增不减，每个已完成任务的完整结果（含全部字幕段落）会一直留在
# 内存里 —— 桌面端连续处理多个长视频就是持续增长的占用。运行中的任务永不淘汰。
_MAX_FINISHED_JOBS = 10
_finished_order: list[str] = []


def _mark_finished(job_id: str) -> None:
    """把任务记为「已结束」，并按上限淘汰最早的已结束任务。

    按**结束顺序**而非创建顺序淘汰：先创建但后结束的任务不应被优先丢掉。
    """
    with _lock:
        if job_id not in _finished_order:
            _finished_order.append(job_id)
        while len(_finished_order) > _MAX_FINISHED_JOBS:
            stale = _finished_order.pop(0)
            _jobs.pop(stale, None)


def _json_error(message: str, code: int = 500):
    """统一的 JSON 错误返回。

    Flask 默认把未捕获异常渲染成 HTML 错误页。前端拿到 HTML 后
    `resp.json()` 会抛 `Unexpected token '<'`，真正的异常信息被完全吞掉，
    页面上只剩一句无从下手的「请求失败」。所有 /api/* 路由都必须走这里。
    """
    app.logger.error("[API %s] %s", code, message)
    return jsonify({"error": message}), code


@app.errorhandler(Exception)
def _handle_uncaught(exc):
    """把任何未捕获异常转成 JSON（仅对 /api/* 生效，其余走默认 HTML 页）。"""
    from werkzeug.exceptions import HTTPException

    if request.path.startswith("/api/"):
        if isinstance(exc, HTTPException):
            return _json_error(exc.description or str(exc), exc.code or 500)
        import traceback
        traceback.print_exc()
        return _json_error(f"{type(exc).__name__}: {exc}", 500)
    # 非 API 路由：交回 Flask 默认处理
    if isinstance(exc, HTTPException):
        return exc
    raise exc


@app.route("/")
def index():
    return render_template("index.html",
        default_provider=TRANSLATION_PROVIDER,
        # 可选引擎由 config.VALID_PROVIDERS 单点定义，界面不再手写一份
        providers=list(VALID_PROVIDERS),
        whisper_models=[WHISPER_MODEL] + WHISPER_REFERENCE_MODELS,
    )


@app.route("/api/health")
def api_health():
    """自检端点：一键回答「为什么请求失败」。

    浏览器打开 http://127.0.0.1:7860/api/health 就能看到当前生效的配置、
    key 是否已配置（只回真假，不回明文）、以及输出目录是否可写。
    排查「请求失败」时先看这里，比翻日志快。
    """
    import os as _os
    from src import config as _cfg

    def key_status(provider):
        k = _cfg.resolve_api_key(provider)
        if not k:
            return {"configured": False, "reason": "未配置"}
        if _cfg._looks_like_placeholder(k):
            return {"configured": False, "reason": "仍是模板占位符，请填入真实 Key"}
        return {"configured": True, "prefix": k[:8] + "...", "length": len(k)}

    out = OUTPUT_DIR
    writable = None
    try:
        out.mkdir(parents=True, exist_ok=True)
        writable = _os.access(out, _os.W_OK)
    except OSError:
        writable = False

    diarize_ok, diarize_reason = validate_diarization()

    # Whisper 模型是否找得到 —— 路径配错时这里能直接看出来
    try:
        from src.transcriber import diagnose_model_dir, _local_model_sizes
        _models_ok, _models_msg = diagnose_model_dir()
        models_info = {
            "ok": _models_ok,
            "detail": _models_msg,
            "cached": _local_model_sizes(),
            "needed": [WHISPER_MODEL] + WHISPER_REFERENCE_MODELS,
        }
    except Exception as e:  # noqa: BLE001
        models_info = {"ok": None, "detail": f"检查失败: {e}"}

    # 下载用的 cookies 状态 —— YouTube 需要，Bilibili/Niconico 不需要。
    # 放在自检里，用户配完 cookies 不用真跑一次任务就能确认有没有生效。
    try:
        from src.downloader import cookie_status
        cookies_info = cookie_status()
    except Exception as e:  # noqa: BLE001
        cookies_info = {"configured": None, "hint": f"检查失败: {e}"}

    return jsonify({
        "provider": TRANSLATION_PROVIDER,
        "env_files_loaded": [str(p) for p in getattr(_cfg, "ENV_FILES", [])],
        "api_keys": {
            "deepseek": key_status("deepseek"),
            "anthropic": key_status("anthropic"),
        },
        "output_dir": str(out),
        "output_dir_writable": writable,
        "model_dir": str(_cfg.MODEL_DIR),
        "models": models_info,
        "download_cookies": cookies_info,
        "diarization": {"available": diarize_ok, "reason": diarize_reason},
        "whisper": {"model": WHISPER_MODEL, "device": _cfg.DEVICE,
                    "compute_type": _cfg.COMPUTE_TYPE},
    })


@app.route("/api/_selftest_boom")
def _selftest_boom():
    """仅供回归测试：验证 /api/* 的未捕获异常会变成 JSON 而不是 HTML 500。"""
    raise RuntimeError("selftest: intentional failure")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Accept a local video file upload and return its saved path."""
    f = request.files.get("file")
    if f is None or f.filename == "":
        return jsonify({"error": "未选择文件"}), 400

    ext = Path(f.filename).suffix.lower() or ".mp4"
    upload_dir = OUTPUT_DIR / "uploads"
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _json_error(
            f"无法创建上传目录 {upload_dir}：{e.strerror or e}。"
            f"请检查该位置是否可写，或在 .env 里把 OUTPUT_DIR 指向可写目录。"
        )
    if not upload_dir.is_dir():
        return _json_error(f"上传目录不可用（不是文件夹或无法访问）：{upload_dir}")

    dest = upload_dir / f"{uuid.uuid4().hex}{ext}"
    try:
        f.save(dest)
    except OSError as e:
        return _json_error(
            f"保存上传文件失败（{dest}）：{e.strerror or e}。"
            f"请确认磁盘空间充足且目录可写。"
        )

    return jsonify({"path": str(dest), "filename": f.filename})


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json() or {}
    input_source = (data.get("url") or "").strip()
    if not input_source:
        input_source = (data.get("file_path") or "").strip()
    provider = data.get("provider", TRANSLATION_PROVIDER)
    api_key = (data.get("api_key") or "").strip()
    diarize = data.get("diarize", True)
    # 可选：已知说话人数量（0 / 缺省 = 自动聚类）
    try:
        speakers = int(data.get("speakers") or 0)
    except (TypeError, ValueError):
        speakers = 0
    if speakers < 1:
        speakers = 0
    context = data.get("context", True)
    review_enabled = data.get("review", ENABLE_REVIEW)

    if not input_source:
        return jsonify({"error": "请提供视频链接或上传本地视频/音频文件"}), 400

    # 提前校验 key：把配置问题挡在入队之前，用户能立刻看到可操作的提示，
    # 而不是任务跑起来之后在进度流里炸掉。
    try:
        validate_config(provider, api_key)
    except ValueError as e:
        app.logger.error("[API] 配置校验失败: %s", e)
        return _json_error(str(e), 400)

    job_id = uuid.uuid4().hex[:12]
    progress_queue = queue.Queue()

    with _lock:
        _jobs[job_id] = {"queue": progress_queue, "status": "running", "result": None}

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, input_source, provider, api_key, diarize, speakers, context, review_enabled),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id):
    def generate():
        with _lock:
            job = _jobs.get(job_id)
        if job is None:
            yield f"data: {json.dumps({'type': 'error', 'message': '任务不存在'})}\n\n"
            return

        # 任务在客户端重连之前就已经结束了 —— 直接把终结事件补发一次。
        # 进度队列是**一次性消费**的：done/error 消息早被上一个（已断开的）
        # 连接取走了，重连后只会一直收到 ping，客户端永远等不到结果。
        status = job.get("status")
        if status == "error":
            payload = {"type": "error", "message": job.get("error") or "任务失败"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return
        if job.get("result") is not None:
            res = job["result"]
            payload = {"type": "done", "title": res.get("title", ""),
                       "count": len(res.get("segments", []))}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return

        q = job["queue"]
        while True:
            try:
                msg = q.get(timeout=30)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg["type"] in ("done", "error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/result/<job_id>")
def api_result(job_id):
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "任务不存在"}), 404

    # 失败的任务必须报 "error"，不能报 "running"。
    # 否则：SSE 连接一断（用户刷新页面 / 网络抖动），前端就只能反复轮询，
    # 永远看到 "running"，转圈转到天荒地老 —— 错误信息只存在于那条已断的流里。
    if job.get("status") == "error":
        return jsonify({
            "status": "error",
            "error": job.get("error") or "任务失败（未记录到具体原因）",
        })

    if job["result"] is None:
        return jsonify({"status": "running"})

    result = job["result"]
    segments = result["segments"]
    files = result["files"]

    return jsonify({
        "status": "done",
        "title": result["title"],
        "count": len(segments),
        "video_path": str(result.get("video_path", "")),
        "speakers": list(result.get("speakers", [])),
        "segments": [
            {
                "start": s["start"],
                "end": s["end"],
                "text": s.get("text", ""),
                "translated": s.get("translated", ""),
                # 失败标记：布尔字段，前端据此判断，不再去匹配译文文本。
                # 早先是后端写 `[翻译失败: ...]`、前端再 startsWith('[翻译失败')
                # —— 同一个约定两处硬编码，后端一改文案前端就静默失效。
                "failed": bool(s.get("failed", False)),
                "speaker": s.get("speaker", ""),
                # 缩写名同样由服务端下发，前端不再自己 replace。
                # 与 color 同理：同一个转换写两遍，迟早有一边先改。
                "speaker_short": _short_speaker(s.get("speaker", "")),
                # 颜色由服务端算好下发，前端不再自己算。
                # 早先前端用 djb2 哈希 + 另一套调色板，与 ass_writer 的 md5 +
                # 调色板都不一致 —— 同一个说话人在预览里和在字幕文件里颜色不同，
                # 而且前端调色板把纯白排在第一位，导致 0 号说话人预览是白色
                # （正是 ass_writer 特意避开的「分到白色就看不出区别」）。
                "color": _speaker_css_color(s.get("speaker", "")),
            }
            for s in segments
        ],
        "files": {k: str(v) for k, v in files.items()},
    })


@app.route("/api/download/<path:filepath>")
def api_download(filepath):
    # 只取文件名，在固定的绝对 OUTPUT_DIR 下查找，避免相对路径 / CWD 歧义
    # （同时天然挡住 ../ 穿越：Path(...).name 会剥掉所有目录部分）
    p = (OUTPUT_DIR / Path(filepath).name).resolve()
    if not p.exists():
        # R4：/api/* 一律回 JSON，不要回纯文本/HTML
        return _json_error(f"文件不存在：{p.name}", 404)
    # 显式指定 MIME：Python 的 mimetypes 把 .ass 猜成 audio/aac（字幕文件被当成音频），
    # 语义错误且可能被安全软件误判。既然是下载（as_attachment），
    # 用 application/octet-stream 最稳妥。
    return send_file(p, as_attachment=True, download_name=p.name,
                     mimetype="application/octet-stream")


# ─────────────────────────────────────────────────────────────
# 缓存清理（独立板块，与上面的处理流程完全解耦）
# 说明：这里只做「参数校验 + 调用 src/cache.py」，真正的路径解析和
# 删除都在 src/cache.py 内完成，前端无法传入任意路径。
# ─────────────────────────────────────────────────────────────

@app.route("/api/cache/stats")
def api_cache_stats():
    """各缓存类别的文件数与占用体积。"""
    return jsonify(cache_stats())


@app.route("/api/cache/open", methods=["POST"])
def api_cache_open():
    """在系统文件管理器里打开输出目录（或临时目录）。"""
    data = request.get_json(silent=True) or {}
    which = data.get("which", "output")
    try:
        path = open_cache_folder(which)
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "path": path})


@app.route("/api/cache/clean", methods=["POST"])
def api_cache_clean():
    """删除选中的缓存类别。前端只传类别 key，不传路径。"""
    data = request.get_json(silent=True) or {}
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        return jsonify({"error": "未选择任何要清理的内容"}), 400
    try:
        result = clean_cache([str(t) for t in targets])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001 - 兜底，避免 500 页面
        print(f"[jsub-translator] 缓存清理失败: {e}")
        return jsonify({"error": f"清理失败: {e}"}), 500
    return jsonify(result)


def _ensure_video_in_output(video_path) -> str:
    """确保视频位于 OUTPUT_DIR 下，返回最终路径。

    两种情况：
      · URL 下载 —— 已经直接落在 OUTPUT_DIR，src == dest，**不复制**。
      · 本地文件 / 网页上传 —— 在别处，复制一份进来，让「原视频」下载链接可用。

    比较必须用 Path 对 Path。早先写的是 `Path != str`，恒为 True，
    于是连「源和目标就是同一个文件」也会去 copy，直接抛 SameFileError。
    """
    src = Path(video_path).resolve()
    dest = (OUTPUT_DIR / src.name).resolve()
    if src != dest:
        import shutil
        shutil.copy2(src, dest)
    return str(dest)


def _run_job(job_id: str, input_source: str, provider: str, api_key: str = "",
             diarize: bool = True, speakers: int = 0, context: bool = True,
             review_enabled: bool = True):
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    q = job["queue"]

    def progress_callback(pct: float, stage: str, message: str):
        q.put({"type": "progress", "stage": stage, "pct": round(pct * 100), "message": message})

    try:
        q.put({"type": "progress", "stage": "init", "pct": 0, "message": "Starting..."})

        result = process(
            input_source=input_source,
            provider=provider,
            api_key=api_key,
            enable_diarization=diarize,
            num_speakers=speakers or None,
            enable_context=context,
            enable_review=review_enabled,
            progress_callback=progress_callback,
            cleanup_temp=False,
        )

        if result.get("video_path") and Path(result["video_path"]).exists():
            result["video_path"] = _ensure_video_in_output(result["video_path"])

        with _lock:
            _jobs[job_id]["result"] = result
            _jobs[job_id]["status"] = "done"

        # 注意在锁外调用 —— _mark_finished 内部自己取锁（Lock 不可重入）
        _mark_finished(job_id)

        q.put({"type": "done", "title": result["title"], "count": len(result["segments"])})

    except Exception as e:
        if isinstance(e, ValueError):
            # 配置类错误（如未填 API Key）属预期情况，只打印一行提示
            print(f"[jsub-translator] {e}")
        else:
            import traceback
            traceback.print_exc()
        with _lock:
            _jobs[job_id]["status"] = "error"
            # 存下原因，让 /api/result 也能取到（SSE 断了就靠它）
            _jobs[job_id]["error"] = str(e)
        _mark_finished(job_id)   # 同样在锁外
        q.put({"type": "error", "message": str(e)})


def main():
    ensure_dirs()

    # Determine where .env should live (next to EXE, or cwd for dev)
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(os.getcwd())

    env_file = exe_dir / ".env"

    # Auto-create .env from template on first run.
    # Non-fatal: the web UI can also supply the API key, so the server must
    # still start so the browser page is reachable.
    if not env_file.exists():
        template = _base_dir() / ".env.example"
        if template.exists():
            import shutil
            shutil.copy(template, env_file)
            print("=" * 60)
            print("  [First run] Created .env config file")
            print(f"  Location: {env_file}")
            print("  You can enter your API Key in the web UI, or edit")
            print("  this file and restart.")
            print("=" * 60)

    # Validate config — warn but keep going so the UI is reachable and the
    # user can enter the key there.
    try:
        from src.config import validate_config
        validate_config()
    except ValueError as e:
        print("=" * 60)
        print("  [Warning] 尚未配置可用的 API Key")
        print(f"  {e}")
        print("  -> 稍后可在网页界面的「API Key」输入框中填写")
        print("=" * 60)

    port = int(os.environ.get("PORT", 7860))

    # Report speaker-diarization availability up front. If it is unavailable we
    # say so explicitly instead of silently producing unlabelled subtitles.
    _dia_ok, _dia_reason = validate_diarization()
    print("=" * 60)
    if _dia_ok:
        print(f"  [说话人识别] 可用 — {_dia_reason}")
    else:
        print(f"  [说话人识别] 不可用 — {_dia_reason}")
        print("  -> 字幕仍会正常生成，但不会区分说话人")
    print("=" * 60)

    # 启动时就检查 Whisper 模型是否找得到。
    # 移动/重命名项目后 .env 里写死的 MODEL_DIR 会失效，而那是**静默**的：
    # 程序照常启动，直到识别阶段才发现没模型、转去联网下载、最后连接超时。
    # 提前报出来能省掉一整轮无谓等待。
    try:
        from src.transcriber import diagnose_model_dir
        _md_ok, _md_msg = diagnose_model_dir()
        print("=" * 60)
        if _md_ok:
            print(f"  [Whisper 模型] 就绪 — {_md_msg}")
        else:
            print(f"  [Whisper 模型] 有问题 — {_md_msg}")
            print("  -> 不修正的话，识别阶段会尝试联网下载并很可能超时失败")
        print("=" * 60)
    except Exception as _e:  # noqa: BLE001 - 诊断失败不应阻止启动
        print(f"  [Whisper 模型] 检查跳过（{_e}）")

    print(f"[jsub-translator] Starting...")
    print(f"    http://127.0.0.1:{port}")

    # Open browser after a short delay
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
