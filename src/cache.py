"""
缓存清理（独立模块）。

统计并删除运行过程中产生的、可以安全清理的产物：字幕文件、输出目录里的
视频、上传的原始文件、以及 %TEMP% 下的临时音频目录。

设计约束（重要）：
  * 本模块**不依赖** pipeline / transcriber，也不改动原有处理流程，
    只是一个旁路的「统计 + 删除」工具。
  * 所有真实路径都在**服务端**由 `_categories()` 解析，前端只能传类别 key
    （"subtitles" / "videos" / "uploads" / "temp"）。这样不会出现
    「前端传什么路径就删什么」的任意文件删除漏洞。
  * 删除前会对每个路径做「必须落在允许根目录之下」的二次校验。
  * Whisper 模型**不在此处清理**：删掉后需要重新下载，国内很容易失败。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import OUTPUT_DIR


# 视为「视频/音频」的扩展名（output 根目录下非字幕文件）
MEDIA_EXTS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}

# %TEMP% 下本程序创建的临时目录前缀（见 pipeline.py: f"jsub_{run_id}"）
TEMP_PREFIX = "jsub_"


# ────────────────────────── 内部工具 ──────────────────────────

def _iter_files(path: Path) -> list[Path]:
    """展开一个路径下的所有文件（文件本身 / 目录递归）。"""
    try:
        if path.is_file():
            return [path]
        if path.is_dir():
            return [p for p in path.rglob("*") if p.is_file()]
    except OSError:
        pass
    return []


def _scan(paths: list[Path]) -> tuple[int, int]:
    """返回 (文件数, 总字节数)。统计失败的文件按 0 字节计。"""
    count = 0
    size = 0
    for p in paths:
        for f in _iter_files(p):
            count += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return count, size


def _is_within(path: Path, root: Path) -> bool:
    """path 是否位于 root 之下（防目录穿越）。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _allowed_roots() -> list[Path]:
    return [OUTPUT_DIR.resolve(), Path(tempfile.gettempdir()).resolve()]


# 盘根的直接子目录里，这些是系统目录，绝不能当作输出目录
_SYSTEM_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "programdata",
    "users", "system volume information", "recovery", "$recycle.bin",
    "perflogs", "msocache", "intel", "amd", "nvidia",
}


def is_dangerous_root(path: Path) -> bool:
    """该目录是否危险到不该被当作「清理范围」。

    `_categories()` 用 `OUTPUT_DIR.glob("*")` 找视频文件。如果 .env 里把
    OUTPUT_DIR 误写成**盘根**（`E:/`）或**系统目录**，这个 glob 就会匹配到
    整个盘/系统目录里的媒体文件 —— 「清理缓存」于是变成删用户自己的文件。
    实测：盘根下 `glob("*")` 能枚举出全部子目录，媒体文件也会被一并选中。

    这里只挡最危险的几类；正常输出目录（`E:/xxx/output`）不受影响。
    """
    try:
        r = path.resolve()
    except OSError:
        return True  # 解析不了就别删

    # 1) 盘根，如 E:\ 、C:\ （盘根的 parent 就是它自己）
    if r.parent == r:
        return True

    # 2) 盘根的直接子目录，且名字是已知系统目录
    if r.parent.parent == r.parent and r.name.lower() in _SYSTEM_DIR_NAMES:
        return True

    # 3) 用户主目录本身
    try:
        if r == Path.home().resolve():
            return True
    except (OSError, RuntimeError):
        pass

    return False


def _categories() -> dict[str, dict]:
    """类别 key → {label, desc, paths}。路径只在这里解析。"""
    temp_dirs = sorted(
        p for p in Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*") if p.is_dir()
    )

    subtitles = sorted(OUTPUT_DIR.glob("*.ass"))
    media = sorted(
        p for p in OUTPUT_DIR.glob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    )

    return {
        "subtitles": {
            "label": "字幕文件 (.ass)",
            "desc": "已生成的中文 / 日文 / 双语字幕。删除后需重新处理视频才能再得到。",
            "paths": subtitles,
        },
        "videos": {
            "label": "输出目录中的视频",
            "desc": "处理时下载或复制过来的视频文件。删除后「原视频/音频」下载链接会失效。",
            "paths": media,
        },
        "uploads": {
            "label": "上传的原始文件",
            "desc": "通过网页上传的源视频 / 音频。删除不影响已生成的字幕。",
            "paths": [OUTPUT_DIR / "uploads"],
        },
        "temp": {
            "label": "临时音频目录",
            "desc": "处理过程中提取的音频（系统临时目录下的 jsub_* 文件夹），可安全删除。",
            "paths": temp_dirs,
        },
    }


# ────────────────────────── 对外接口 ──────────────────────────

def cache_stats() -> dict:
    """各缓存类别的数量与占用，供网页展示。"""
    cats = _categories()
    out = []
    for key, c in cats.items():
        count, size = _scan(c["paths"])
        out.append({
            "key": key,
            "label": c["label"],
            "desc": c["desc"],
            "count": count,
            "bytes": size,
        })
    return {
        "output_dir": str(OUTPUT_DIR),
        "temp_dir": str(Path(tempfile.gettempdir())),
        "categories": out,
        "total_count": sum(x["count"] for x in out),
        "total_bytes": sum(x["bytes"] for x in out),
        # 输出目录被误配成盘根/系统目录时给出警告：
        # 此时上面的统计会把与字幕无关的文件也算进来，而且清理会被拒绝。
        "warning": (
            f"输出目录 {OUTPUT_DIR} 看起来是盘根或系统目录，"
            f"统计结果可能包含与字幕无关的文件；清理功能已被禁用。"
            f"请把 .env 的 OUTPUT_DIR 指向专用目录。"
            if is_dangerous_root(OUTPUT_DIR) else None
        ),
    }


def clean_cache(keys: list[str]) -> dict:
    """删除指定类别的缓存。

    Args:
        keys: 类别 key 列表，只能是 _categories() 的键。

    Returns:
        {"results": [{key, label, removed, freed, errors}], "stats": {...}}

    Raises:
        ValueError: key 非法，或未选择任何类别。
    """
    cats = _categories()
    unknown = [k for k in keys if k not in cats]
    if unknown:
        raise ValueError(f"未知的清理类别: {', '.join(unknown)}")
    if not keys:
        raise ValueError("未选择任何要清理的内容")

    # 兜底：输出目录本身要是个正经目录。若被误配成盘根/系统目录，
    # glob("*") 会圈进整盘的文件，删除就等于删用户数据 —— 宁可不删。
    if is_dangerous_root(OUTPUT_DIR):
        raise ValueError(
            f"输出目录被配置为 {OUTPUT_DIR}，它看起来是盘根或系统目录。\n"
            f"  在该目录下「清理缓存」会波及与字幕无关的文件，因此已拒绝执行。\n"
            f"  请把 .env 里的 OUTPUT_DIR 指向一个专用目录，"
            f"例如 OUTPUT_DIR=E:/jsub-output"
        )

    roots = _allowed_roots()
    output_root = OUTPUT_DIR.resolve()
    results = []

    for key in keys:
        cat = cats[key]
        removed = 0
        freed = 0
        errors: list[str] = []

        for target in cat["paths"]:
            try:
                resolved = target.resolve()
            except OSError:
                continue

            # 二次保险：只允许删 OUTPUT_DIR 或 %TEMP% 之下的东西
            if not any(_is_within(resolved, r) for r in roots):
                errors.append(f"{target.name}: 路径不在允许范围内，已跳过")
                continue

            for f in _iter_files(resolved):
                try:
                    size = f.stat().st_size
                except OSError:
                    size = 0
                try:
                    f.unlink()
                    removed += 1
                    freed += size
                except OSError as e:
                    errors.append(f"{f.name}: {e.strerror or e}")

            # 清空后顺手删掉空目录；绝不删 OUTPUT_DIR 本身
            if resolved.is_dir() and resolved != output_root:
                try:
                    if not any(resolved.iterdir()):
                        resolved.rmdir()
                except OSError:
                    pass

        results.append({
            "key": key,
            "label": cat["label"],
            "removed": removed,
            "freed": freed,
            "errors": errors[:8],
        })

    return {"results": results, "stats": cache_stats()}


def open_cache_folder(which: str = "output") -> str:
    """在系统文件管理器里打开目录。

    路径固定（只允许 output / temp），不接受任意路径，避免被当成任意程序调用入口。

    Returns:
        实际打开的路径。

    Raises:
        ValueError: which 非法。
        RuntimeError: 目录无法创建，或系统调用失败。
    """
    if which == "output":
        path = OUTPUT_DIR
    elif which == "temp":
        path = Path(tempfile.gettempdir())
    else:
        raise ValueError(f"未知的目录: {which}")

    # 目录不存在就建。之前这里 `except OSError: pass` 把失败吞掉了，
    # 于是 startfile 拿到一个不存在的路径，报出的错误跟真实原因无关
    # （典型症状就是「打开输出文件夹」失败但不告诉你为什么）。
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"无法创建目录 {path}：{e.strerror or e}。"
            f"请检查该位置是否可写，或在 .env 里把 OUTPUT_DIR 指向一个可写的目录。"
        ) from e

    if not path.is_dir():
        raise RuntimeError(f"目录不可用（不是文件夹或无法访问）：{path}")

    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606 - 路径由服务端固定，非用户输入
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:  # noqa: BLE001 - 交给上层转成 HTTP 错误
        raise RuntimeError(f"无法打开文件夹 {path}：{e}") from e

    return str(path)
