"""
术语词典（Glossary）。

三层来源，**后面的覆盖前面的**：

1. 内置 JPOP 词典（下面的 `JPOP_GLOSSARY`）—— 开箱即用
2. `jpop_glossary.json` —— 单文件扁平覆盖，历史兼容
       {"シングル": "单曲", "推し": "我推/主推"}
3. `dictionary/*.yaml` —— 分主题的结构化词典（本文件新增）

第 3 层的 YAML 支持分节，便于按主题拆分维护：

    terms:            # 一般术语 / 网络词
      推し:
        zh: 推
      レス:
        zh: 饭撒回应
    people:           # 人名（固定译名，优先于模型自由翻译）
      某成员:
        zh: 某成员
    groups:           # 团体名 / 作品名（不擅自汉化）
      iLiFE!:
        zh: iLiFE!

也接受简写 `推し: 推`（值直接给字符串），以及 `zh` 之外的别名字段。

**翻译时只注入本批真正命中的词条**（见 `build_glossary_pairs`）——
把整本词典塞进 prompt 既浪费 token 又会稀释注意力。
"""

import json
import sys
from pathlib import Path

# YAML 是可选依赖：没装时退回内置词典 + JSON，不让术语功能拖垮整个应用。
try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    _yaml = None


# ───────────────────────────────────────────────────────────
# 内置词典：日文 → 中文（JPOP 相关）
# ───────────────────────────────────────────────────────────

JPOP_GLOSSARY = {
    # ── 音乐行业 / 发行 ──
    "シングル": "单曲",
    "アルバム": "专辑",
    "ミニアルバム": "迷你专辑",
    "ベストアルバム": "精选专辑",
    "デビュー": "出道",
    "メジャーデビュー": "正式出道",
    "インディーズ": "独立音乐/地下",
    "リリース": "发行/发布",
    "レコード": "唱片",
    "セールス": "销量",
    "チャート": "榜单",
    "ランキング": "排行榜",
    "オリコン": "Oricon公信榜",
    "ミリオン": "百万",
    "ゴールドディスク": "金唱片",
    "プラチナ": "白金",

    # ── 创作 / 歌曲结构 ──
    "作詞": "作词",
    "作曲": "作曲",
    "編曲": "编曲",
    "歌詞": "歌词",
    "メロディ": "旋律",
    "サビ": "副歌",
    "Aメロ": "主歌",
    "Bメロ": "副歌前段",
    "カバー": "翻唱",
    "コラボ": "合作",
    "タイアップ": "影视/广告联动配歌",
    "主題歌": "主题曲",
    "挿入歌": "插曲",
    "エンディング": "片尾曲",
    "オープニング": "片头曲",
    "サウンドトラック": "原声带",

    # ── 演出 / 现场 ──
    "ライブ": "现场演出/演唱会",
    "ワンマンライブ": "专场演唱会",
    "ツアー": "巡回演出",
    "コンサート": "演唱会",
    "フェス": "音乐节",
    "イベント": "活动",
    "握手会": "握手会",
    "サイン会": "签名会",
    "アンコール": "返场/安可",

    # ── 偶像文化 ──
    "アイドル": "偶像",
    "メンバー": "成员",
    "センター": "中心位/C位",
    "選抜": "选拔成员",
    "総選挙": "总选举",
    "推し": "我推/主推",
    "推しメン": "主推成员",
    "オタク": "粉丝/宅",
    "ファン": "粉丝",
    "ペンライト": "应援棒/荧光棒",
    "コール": "打call/应援口号",
    "応援": "应援",
    "研究生": "练习生/研修生",
    "卒業": "毕业（成员离团）",
    "兼任": "兼任（跨团活动）",
    "チェキ": "拍立得合照",
    "グッズ": "周边商品",
    "特典": "特典/赠品",

    # ── 粉丝 / 网络俚语 ──
    "尊い": "太尊了/绝了",
    "神曲": "神曲",
    "エモい": "走心/催泪",
    "やばい": "厉害/糟了（依语境）",
    "最高": "最棒/太棒了",
    "癒される": "被治愈",
    "泣ける": "催泪/让人想哭",
    "中毒性": "洗脑/上瘾",
    "沼る": "入坑/沉迷",
    "リア充": "现充",
    "一生推す": "一辈子都推",

    # ── 音乐风格 / 类型 ──
    "J-POP": "J-POP/日本流行乐",
    "ロック": "摇滚",
    "バラード": "抒情歌",
    "アップテンポ": "快节奏",
    "アニソン": "动漫歌曲",
    "ボカロ": "VOCALOID",
    "バンド": "乐队",
    "ユニット": "组合/小分队",
    "ソロ": "个人活动/单飞",
}

# 目标语言在词典条目里的键名（可写 zh / zh-CN / zh_CN，也接受直接给字符串）
_TARGET_KEYS = ("zh", "zh-CN", "zh_CN", "zh-cn", "target", "translation")

# 分节名 → 归一化后的类别。people / groups 是「固定译名」，
# 优先级高于 terms：人名团名不允许被模型自由发挥。
_SECTIONS = ("terms", "people", "groups")


def _anchor_dir() -> Path:
    """基准目录：冻结版=EXE 所在目录，源码版=项目根。

    与 config._anchor_dir 同样的规则 —— 词典要能被用户放在 EXE 旁边覆盖，
    而不是埋在 onefile 的临时解包目录里（那个目录每次启动都变）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _user_glossary_path() -> Path:
    """用户覆盖词典的位置（打包版=exe 同目录，开发版=项目根）。"""
    return _anchor_dir() / "jpop_glossary.json"


def _normalize_entry(value) -> str:
    """把一条词典值归一化成中文字符串。无法识别时返回 ""（该条被忽略）。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for k in _TARGET_KEYS:
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _load_yaml_file(path: Path) -> dict:
    """读一个 YAML 词典文件，返回 {日文: 中文}。

    支持两种写法：
        terms:
          推し: 推                 # 简写
          レス:
            zh: 饭撒回应          # 完整写法
    顶层直接写 `推し: 推`（不分节）也接受。
    """
    if _yaml is None:
        return {}
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        # 词典文件损坏不该让翻译挂掉：跳过这一个文件，其余照常加载。
        print(f"[glossary] ⚠ 无法解析 {path.name}，已跳过该词典。", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, str] = {}
    has_sections = any(k in data for k in _SECTIONS)

    if has_sections:
        for section in _SECTIONS:
            block = data.get(section)
            if isinstance(block, dict):
                for src, val in block.items():
                    tgt = _normalize_entry(val)
                    if tgt:
                        out[str(src)] = tgt
    else:
        # 无分节：整个文件就是一个扁平词典
        for src, val in data.items():
            tgt = _normalize_entry(val)
            if tgt:
                out[str(src)] = tgt
    return out


def glossary_dirs() -> list[Path]:
    """要扫描的词典目录（来自 config，避免与 config 循环导入故延迟读取）。"""
    try:
        from .config import INDEX_GLOSSARY_DIRS, _resolve_dir
    except Exception:  # pragma: no cover
        return []
    if not INDEX_GLOSSARY_DIRS:
        return []
    # 允许用 ; 或 , 分隔多个目录
    parts = [p.strip() for p in INDEX_GLOSSARY_DIRS.replace(",", ";").split(";") if p.strip()]
    return [_resolve_dir(p, p) for p in parts]


def load_dictionaries() -> dict:
    """合并全部词典来源，返回 {日文: 中文}。

    覆盖顺序（后者优先）：内置 JPOP → dictionary/*.yaml → jpop_glossary.json。
    让 JSON 最后加载，是为了不改变老用户「JSON 覆盖一切」的既有预期。
    """
    merged = dict(JPOP_GLOSSARY)

    # dictionary/*.yaml（按文件名排序，custom.yaml 靠后即自然最后生效）
    for d in glossary_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.y*ml")):
            merged.update(_load_yaml_file(f))

    # jpop_glossary.json —— 历史来源，优先级最高
    path = _user_glossary_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    tgt = _normalize_entry(v)
                    if tgt:
                        merged[str(k)] = tgt
    except Exception:
        pass  # 用户词典损坏时静默忽略，回退到已加载的部分

    return merged


# 翻译时实际使用的合并词典
GLOSSARY = load_dictionaries()


def reload_glossary() -> dict:
    """重新加载词典（用户改了 YAML 后不必重启）。"""
    global GLOSSARY
    GLOSSARY = load_dictionaries()
    return GLOSSARY


def match_terms(source_text: str, glossary: dict = None) -> dict:
    """从源文本中找出命中的词条，返回 {日文: 中文}（无命中则空 dict）。

    这是「动态 glossary」的核心：只把本批真正出现的术语交给模型。
    """
    glossary = GLOSSARY if glossary is None else glossary
    return {k: v for k, v in glossary.items() if k and k in source_text}


def build_glossary_hint(source_text: str, glossary: dict = None) -> str:
    """命中的术语 → 通用提示词片段（deepseek / anthropic 使用）。

    无命中时返回空字符串（不向请求注入任何内容）。
    """
    hits = match_terms(source_text, glossary)
    if not hits:
        return ""
    lines = [f"{k} → {v}" for k, v in hits.items()]
    return "术语参考（优先采用以下译法）：\n" + "\n".join(lines)


def build_glossary_pairs(source_text: str, glossary: dict = None) -> dict:
    """命中的术语 → {源词: 目标词}（Index 的 instTrans 硬约束使用）。

    与 `build_glossary_hint` 的区别：这里返回结构化数据，
    由 index_prompt 组装成官方要求的【硬性要求】格式。
    """
    return match_terms(source_text, glossary)


def glossary_stats() -> dict:
    """词典诊断信息（给 /api/health 用，方便确认 YAML 到底有没有被加载）。"""
    dirs = glossary_dirs()
    files: list[str] = []
    for d in dirs:
        if d.is_dir():
            files.extend(str(f) for f in sorted(d.glob("*.y*ml")))
    return {
        "entries": len(GLOSSARY),
        "dirs": [str(d) for d in dirs],
        "files": files,
        "yaml_available": _yaml is not None,
    }
