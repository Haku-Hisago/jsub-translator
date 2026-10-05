"""Repair speaker-style alignment in existing ASS files (Alignment 2 -> 8).

Only touches `Style:` lines whose name matches S<digits>. Timing, text, colours
and every other style are left byte-identical. A backup copy is written first.

Run:  .venv/Scripts/python.exe tools/fix_ass_alignment.py [--dir dist/output] [--apply]
Without --apply it only reports what would change (dry run).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

SPEAKER_STYLE_RE = re.compile(r"^S\d+$")
TARGET_ALIGN = "8"
TARGET_MARGIN_V = "20"


def fix_text(text: str) -> tuple[str, list[str]]:
    """Return (new_text, changes). Only rewrites matching Style lines."""
    out = []
    changes = []
    for line in text.splitlines():
        if line.startswith("Style:"):
            a = line.split(",")
            if len(a) >= 23 and SPEAKER_STYLE_RE.match(a[0].split(": ", 1)[1]):
                name = a[0].split(": ", 1)[1]
                if a[18] != TARGET_ALIGN:
                    changes.append(f"{name}: align {a[18]} -> {TARGET_ALIGN}")
                    a[18] = TARGET_ALIGN
                # 垂直边距与日文样式保持一致，否则贴边/位置不一致
                if a[21] != TARGET_MARGIN_V:
                    changes.append(f"{name}: marginV {a[21]} -> {TARGET_MARGIN_V}")
                    a[21] = TARGET_MARGIN_V
                out.append(",".join(a))
                continue
        out.append(line)
    return "\n".join(out) + "\n", changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="dist/output")
    ap.add_argument("--apply", action="store_true",
                    help="actually write the files (default: dry run)")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"目录不存在: {root}")
        return 1

    total_files = total_changes = 0
    for f in sorted(root.glob("*.ass")):
        try:
            raw = f.read_bytes()
            text = raw.decode("utf-8-sig")
        except OSError as e:
            print(f"  跳过 {f.name}: {e}")
            continue

        new_text, changes = fix_text(text)
        if not changes:
            continue

        total_files += 1
        total_changes += len(changes)
        print(f"\n{f.name}")
        for c in changes:
            print(f"    {c}")

        if args.apply:
            # 先备份，再写回；保持 UTF-8 BOM
            bak = f.with_suffix(f.suffix + ".bak-align")
            if not bak.exists():
                shutil.copy2(f, bak)
            f.write_bytes(new_text.encode("utf-8-sig"))
            print(f"    -> 已写入（备份 {bak.name}）")

    print()
    if total_files == 0:
        print("无需修复。")
    else:
        verb = "已修复" if args.apply else "待修复（dry run）"
        print(f"{verb}: {total_files} 个文件, {total_changes} 处样式")
        if not args.apply:
            print("加 --apply 才会真正写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
