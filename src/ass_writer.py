r"""
ASS (Advanced SubStation Alpha) subtitle file generator.
Produces bilingual Japanese + Chinese subtitle files for Aegisub.

ASS format reference:
- Colors use ABGR hex format: &HAABBGGRR
- \\N is the hard line break in dialogue text
- Styles have 23 fields
"""

from pathlib import Path
from typing import Optional
import hashlib

from .config import OUTPUT_DIR


# --- SPEAKER COLOR PALETTE (ABGR format) ---
# 这是配色的**唯一权威定义**。用于「说话人分色」版本。
# 刻意避开纯白（&H00FFFFFF）——白色是默认字幕色，
# 若某个说话人恰好分到白色，就等于在分色版本里看不出区别。
#
# ASS 的字节序是 AABBGGRR（蓝在先），和 CSS 的 RRGGBB 相反，手写极易写错。
# 下面每一项都按「反着写」生成，改色时请改这里，网页预览色会自动跟着变。
SPEAKER_COLORS = [
    "&H0000FFFF",  # Yellow    — #ffff00
    "&H00FFFF00",  # Cyan      — #00ffff
    "&H0000FF00",  # Green     — #00ff00
    "&H00FF00FF",  # Magenta   — #ff00ff
    "&H0000A5FF",  # Orange    — #ffa500
    "&H00FFCE87",  # Lt Blue   — #87ceff
    "&H00CBC0FF",  # Pink      — #ffc0cb
    "&H00A0D0E0",  # Khaki     — #e0d0a0
]


def abgr_to_css(abgr: str) -> str:
    """ASS 的 &HAABBGGRR → CSS 的 #rrggbb（红蓝换位）。"""
    h = abgr.replace("&H", "").replace("&", "")[2:]  # 去掉 alpha 两位
    if len(h) != 6:
        raise ValueError(f"不是 ASS 颜色：{abgr!r}")
    return "#" + h[4:6].lower() + h[2:4].lower() + h[0:2].lower()


# 网页预览色**由上面的权威定义推导**，不再单独维护一份 ——
# 之前两份手写表已经漂移（8 项里 5 项对不上，预览颜色和生成的字幕不是一个色）。
SPEAKER_CSS_COLORS = [abgr_to_css(c) for c in SPEAKER_COLORS]


def _speaker_index(speaker_label: str) -> int:
    """同一说话人必须稳定落在同一格（md5 而非 hash()，后者每次进程都变）。"""
    return int(hashlib.md5(speaker_label.encode()).hexdigest(), 16) % len(SPEAKER_COLORS)


def get_speaker_color(speaker_label: str) -> str:
    """Get a consistent color (ABGR) for a speaker label."""
    if not speaker_label:
        return "&H00FFFFFF"
    return SPEAKER_COLORS[_speaker_index(speaker_label)]


def get_speaker_css_color(speaker_label: str) -> str:
    """Get a consistent CSS color for a speaker label."""
    if not speaker_label:
        return "#e8e8e8"
    return SPEAKER_CSS_COLORS[_speaker_index(speaker_label)]


# --- COLOR CONSTANTS (ABGR format) ---
COLOR_WHITE      = "&H00FFFFFF"
COLOR_YELLOW     = "&H0000FFFF"
COLOR_CYAN       = "&H00FFFF00"
COLOR_BLACK      = "&H00000000"
COLOR_SEMI_BLACK = "&H80000000"
COLOR_SHADOW     = "&H64000000"
COLOR_DARK_OUTLINE = "&H00111111"
COLOR_DARK_SHADOW  = "&H80222222"

# Fade in/out duration (milliseconds) applied to every subtitle line
FADE_IN_MS = 300
FADE_OUT_MS = 300

# --- STYLE DEFINITIONS ---
# 23-field format:
# Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,
# Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,
# BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding

STYLES_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding"
)

# Default style — bilingual single-line display
# Alignment 2 = bottom-center (standard subtitle position)
STYLE_DEFAULT = (
    f"Style: Default,Arial,24,{COLOR_WHITE},{COLOR_YELLOW},"
    f"{COLOR_DARK_OUTLINE},{COLOR_SEMI_BLACK},"
    f"0,0,0,0,100,100,0,0,1,3,1,2,10,10,10,1"
)

# Japanese style — top-positioned, white text, slightly larger
# Alignment 8 = top-center
STYLE_JAPANESE = (
    f"Style: Japanese,MS Gothic,26,{COLOR_WHITE},{COLOR_YELLOW},"
    f"{COLOR_DARK_OUTLINE},{COLOR_SEMI_BLACK},"
    f"0,0,0,0,100,100,0,0,1,3,1,8,10,10,20,128"
)

# Chinese style — bottom-positioned, yellow text, slightly smaller
# Alignment 2 = bottom-center
STYLE_CHINESE = (
    f"Style: Chinese,Microsoft YaHei,22,{COLOR_WHITE},{COLOR_WHITE},"
    f"{COLOR_DARK_OUTLINE},{COLOR_SEMI_BLACK},"
    f"0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,134"
)

# --- 说话人 Style 的定位基准 ---
#
# 说话人分色 / 双语默认版本里，每条字幕引用的都是各自的说话人 Style
# （S00/S01/...），而不是 Default / Japanese。因此**说话人 Style 的对齐方式
# 决定了字幕出现在画面上还是画面下**。
#
# 这里必须与日文样式一致：Alignment 8 = 顶部居中。
# 早期版本把说话人 Style 写成 align=2（底部），导致一旦检测到说话人，
# 全部字幕都会跑到画面下方 —— 也就是「原先设在 8 的位置被改掉了」。
#
# 想改字幕整体位置，只改这一个常量（以及上面的 STYLE_JAPANESE）即可。
SPEAKER_ALIGNMENT = 8          # 8 = 顶部居中
SPEAKER_MARGIN_V = 20          # 与 Japanese 一致，避免贴边
SPEAKER_FONT_SIZE = 24
SPEAKER_OUTLINE = 3
SPEAKER_SHADOW = 1
SPEAKER_ENCODING = 1

# 每个说话人 Style 的字段顺序（共 23 项）：
# Name,Fontname,Fontsize,Primary,Secondary,Outline,Back,Bold,Italic,Underline,
# StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,
# Alignment,MarginL,MarginR,MarginV,Encoding
def _speaker_style(name: str, color: str) -> str:
    """构造单个说话人的 Style 行（定位与日文样式一致：顶部居中）。"""
    return (
        f"Style: {name},Arial,{SPEAKER_FONT_SIZE},{color},{COLOR_YELLOW},"
        f"{COLOR_DARK_OUTLINE},{COLOR_SEMI_BLACK},"
        f"0,0,0,0,100,100,0,0,1,{SPEAKER_OUTLINE},{SPEAKER_SHADOW},"
        f"{SPEAKER_ALIGNMENT},10,10,{SPEAKER_MARGIN_V},{SPEAKER_ENCODING}"
    )

# --- SCRIPT INFO ---
SCRIPT_INFO = """\
[Script Info]
; Generated by jsub-translator
; Japanese → Chinese Subtitle Translation Tool
; Open with Aegisub for editing
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
"""

# --- EVENTS FORMAT ---
EVENTS_FORMAT = (
    "Format: Layer, Start, End, Style, Name, "
    "MarginL, MarginR, MarginV, Effect, Text"
)


def _format_time(seconds: float) -> str:
    """
    Format seconds to ASS time format: H:MM:SS.cc

    Args:
        seconds: Time in seconds.

    Returns:
        ASS-formatted time string (centisecond precision).
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _escape_ass(text: str) -> str:
    """
    Prepare text for ASS dialogue.

    ASS handles most Unicode natively, but **not** curly braces: in ASS, `{...}`
    is an override block, so any literal `{`/`}` in the subtitle text makes the
    renderer eat the braces *and everything between them*.

    实测（用 libass 渲染成图片逐张比对）：
      · `A{これは} B` 渲染结果与 `A B` **逐像素相同** —— `{これは}` 整段消失
      · `A\\{これは\\} B` 同样消失 —— 反斜杠转义在 libass 下无效
      · `A｛これは｝ B` 正常显示 —— 全角花括号不是特殊字符

    所以这里把半角花括号换成**全角**的（U+FF5B / U+FF5D）：
    字形几乎一样，但不会被当成样式块。这是唯一既保住内容、
    又不依赖特定渲染器扩展的做法。

    Args:
        text: Raw subtitle text.

    Returns:
        ASS-safe text.
    """
    # Replace newlines with ASS hard line break
    text = text.replace("\n", "\\N").replace("\r", "")
    # 半角花括号会被当成 override block，换成全角
    text = text.replace("{", "\uff5b").replace("}", "\uff5d")
    return text


def _make_dialogue(
    layer: int,
    start: float,
    end: float,
    style: str,
    text: str,
    name: str = "",
    margin_l: int = 0,
    margin_r: int = 0,
    margin_v: int = 0,
    effect: str = "",
) -> str:
    """
    Build a single Dialogue line.

    ASS format:
    Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    """
    fade_tag = f"{{\\fad({FADE_IN_MS},{FADE_OUT_MS})}}"
    return (
        f"Dialogue: {layer},"
        f"{_format_time(start)},"
        f"{_format_time(end)},"
        f"{style},"
        f"{name},"
        f"{margin_l:04d},{margin_r:04d},{margin_v:04d},"
        f"{effect},"
        f"{fade_tag}{_escape_ass(text)}"
    )


def _short_speaker(speaker: str) -> str:
    """'SPEAKER_00' → 'S00'. Other labels pass through unchanged."""
    if not speaker:
        return ""
    return speaker.replace("SPEAKER_", "S") if speaker.startswith("SPEAKER_") else speaker


def generate_ass(
    segments: list[dict],
    output_path: Optional[Path] = None,
    title: str = "Japanese Subtitle Translation",
    mode: str = "bilingual",
    speaker_colors: bool = False,
) -> Path:
    """
    Generate an ASS subtitle file from translated segments.

    Args:
        segments: List of dicts with 'start', 'end', 'text' (Japanese),
                  and optionally 'translated' (Chinese) and 'speaker'.
        output_path: Output file path. Auto-generated if not specified.
        title: Title for the ASS file metadata.
        mode: Output mode:
            - "bilingual": Japanese + Chinese in one line (uses \\N)
            - "bilingual_split": Two separate Dialogue lines per segment
            - "japanese": Japanese only
            - "chinese": Chinese only
        speaker_colors: When True each speaker gets its own colored style.
            When False (default) all speakers use white text — speaker identity
            is still recorded in the Dialogue `Name` field so it stays
            selectable in Aegisub without changing how the subtitle looks.

    Returns:
        Path to the generated ASS file.
    """
    if output_path is None:
        output_path = OUTPUT_DIR / f"{title}.ass"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []

    # --- [Script Info] ---
    lines.append(SCRIPT_INFO.format(title=title))

    # --- [V4+ Styles] ---
    lines.append("[V4+ Styles]")
    lines.append(STYLES_FORMAT)

    # Detect speakers in segments
    unique_speakers = sorted(set(
        s.get("speaker", "") for s in segments if s.get("speaker", "")
    ))
    has_speakers = len(unique_speakers) > 0

    if has_speakers:
        # Generate speaker-specific styles.
        # 定位（顶部居中 / Alignment 8）由 _speaker_style 统一决定，与日文样式
        # 保持一致；否则一旦启用说话人识别，字幕会整体掉到画面下方。
        for spk in unique_speakers:
            # 默认全白，保证成片可读性；分色版本才用调色板
            color = get_speaker_color(spk) if speaker_colors else COLOR_WHITE
            lines.append(_speaker_style(_short_speaker(spk), color))

    lines.append(STYLE_DEFAULT)
    if mode in ("bilingual_split", "japanese"):
        lines.append(STYLE_JAPANESE)
    if mode in ("bilingual_split", "chinese"):
        lines.append(STYLE_CHINESE)

    # --- [Events] ---
    lines.append("")
    lines.append("[Events]")
    lines.append(EVENTS_FORMAT)

    # --- Dialogue lines ---
    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        jp_text = seg.get("text", "")
        cn_text = seg.get("translated", "")
        speaker = seg.get("speaker", "")

        # Determine style based on speaker
        spk_name = _short_speaker(speaker) if (has_speakers and speaker) else ""

        if mode == "bilingual":
            # Single line: JP on top, CN below (no color; fade applied in _make_dialogue)
            if cn_text:
                combined = f"{_escape_ass(jp_text)}\\N{_escape_ass(cn_text)}"
            else:
                combined = _escape_ass(jp_text)
            # 分色版本：直接引用各说话人的彩色 Style；默认版本：白色 Style
            style = spk_name if spk_name else "Default"
            lines.append(_make_dialogue(0, start, end, style, combined, name=spk_name))

        elif mode == "bilingual_split":
            # Two separate lines: JP on top, CN below
            jp_style = spk_name if spk_name else "Japanese"
            lines.append(_make_dialogue(1, start, end, jp_style, jp_text, name=spk_name))
            if cn_text:
                # 中文行沿用说话人 Style，保证分色版本中文也上色
                cn_style = spk_name if (speaker_colors and spk_name) else "Chinese"
                lines.append(_make_dialogue(0, start, end, cn_style, cn_text, name=spk_name))

        elif mode == "japanese":
            style = spk_name if spk_name else "Japanese"
            lines.append(_make_dialogue(0, start, end, style, jp_text, name=spk_name))

        elif mode == "chinese":
            if cn_text:
                cn_style = spk_name if (speaker_colors and spk_name) else "Chinese"
                lines.append(_make_dialogue(0, start, end, cn_style, cn_text, name=spk_name))

    # Write file with UTF-8 BOM (recommended for CJK content in ASS)
    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")

    return output_path


def generate_all_formats(
    segments: list[dict],
    output_dir: Optional[Path] = None,
    title: str = "Japanese Subtitle Translation",
) -> dict[str, Path]:
    """
    Generate multiple ASS files in different formats:
    - Bilingual (single-line with \\N)
    - Bilingual split (separate JP/CN lines — best for Aegisub editing)
    - Japanese only
    - Chinese only
    - Bilingual with per-speaker colors (only when speakers were detected)

    Args:
        segments: Translated segments.
        output_dir: Output directory.
        title: Base title for the files.

    Returns:
        Dict mapping format name to file path.
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize title for filename
    safe_title = "".join(
        c for c in title if c.isalnum() or c in " _-（）()[]【】"
    ).strip()[:80]

    formats = {
        "bilingual":        f"{safe_title}字幕_双语.ass",
        "bilingual_split":  f"{safe_title}字幕_双语分轨.ass",
        "japanese":         f"{safe_title}字幕_日文.ass",
        "chinese":          f"{safe_title}字幕_中文.ass",
    }

    result = {}
    for mode, filename in formats.items():
        path = generate_ass(
            segments,
            output_dir / filename,
            title=title,
            mode=mode,
        )
        result[mode] = path

    # 额外输出一个「说话人分色」版本；仅在真的检测到说话人时生成，
    # 避免在没有说话人信息时多出一个与「双语」完全相同的文件。
    if any(s.get("speaker", "") for s in segments):
        path = generate_ass(
            segments,
            output_dir / f"{safe_title}字幕_双语_说话人分色.ass",
            title=title,
            mode="bilingual",
            speaker_colors=True,
        )
        result["bilingual_speaker_colored"] = path

    return result
