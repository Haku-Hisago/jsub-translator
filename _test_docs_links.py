"""Regression test for documentation links and in-doc anchors.

Guards against silently rotting docs: a heading gets reworded and every
`#anchor` pointing at it dies, or a file gets moved and the relative link
breaks. Both were found broken in the wild (TROUBLESHOOTING.md §4 / §5).

Run:  .venv/Scripts/python.exe _test_docs_links.py
"""
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


DOCS = [
    "AGENTS.md",
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT.md",
    "docs/TROUBLESHOOTING.md",
    "docs/STATE.md",
    "docs/index-translate.md",
]


def slug(heading: str) -> str:
    """Approximate GitHub's slugger: lowercase, drop punctuation, spaces -> '-'."""
    h = heading.strip().lower()
    h = re.sub(r"[^\w\u4e00-\u9fff -]", "", h)
    return h.replace(" ", "-")


print("=== 文档存在性 ===")
existing = []
for rel in DOCS:
    p = PROJ / rel
    check(f"{rel} 存在", p.is_file(), str(p))
    if p.is_file():
        existing.append(p)

print("=== 相对链接（跨文件） ===")
broken_rel = []
for p in existing:
    text = p.read_text(encoding="utf-8")
    for link in re.findall(r"\]\(([^)]*)\)", text):
        if link.startswith(("http", "mailto", "#")):
            continue
        target = link.split("#", 1)[0]
        if not target:
            continue
        if not (p.parent / target).resolve().exists():
            broken_rel.append(f"{p.name} -> {link}")
check("跨文件相对链接全部有效", not broken_rel, "; ".join(broken_rel))

print("=== 文内锚点 ===")
broken_anchor = []
total_anchors = 0
for p in existing:
    text = p.read_text(encoding="utf-8")
    heads = {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)}
    for link in re.findall(r"\]\((#[^)]*)\)", text):
        total_anchors += 1
        if link[1:] not in heads:
            broken_anchor.append(f"{p.name} -> {link}")
check(f"文内锚点全部有效（共 {total_anchors} 条）", not broken_anchor,
      "; ".join(broken_anchor))

print("=== 索引覆盖（TROUBLESHOOTING 快速索引） ===")
ts = PROJ / "docs" / "TROUBLESHOOTING.md"
if ts.is_file():
    text = ts.read_text(encoding="utf-8")
    heads = {slug(m.group(1)) for m in re.finditer(r"^#{2}\s+(.+)$", text, re.M)}
    indexed = set(re.findall(r"\]\((#[^)]*)\)", text))
    # 索引区内的锚点必须都能解析
    index_block = text.split("## 1.", 1)[0]
    idx_links = re.findall(r"\]\((#[^)]*)\)", index_block)
    check(f"快速索引锚点可解析（{len(idx_links)} 条）",
          all(l[1:] in heads for l in idx_links),
          "; ".join(l for l in idx_links if l[1:] not in heads))
    # 每个编号章节都应被索引收录
    numbered = [h for h in heads if re.match(r"^\d+-", h)]
    check(f"所有编号章节已进入索引（{len(numbered)} 章）",
          all(f"#{h}" in indexed for h in numbered),
          "; ".join(f"#{h}" for h in numbered if f"#{h}" not in indexed))

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
