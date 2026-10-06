"""翻译用例跑批器。

对每条用例调用真实的翻译链路（默认 Index），检查：
  · 译文非空
  · `expect_contains` 里的术语是否出现（硬约束是否落实）
  · 译文是否异常变长（字幕可读性）
  · 是否残留大量日文
  · 是否出现 Markdown / 解释性文字

用法：
    # 默认用 Index（需要本地 vLLM 已启动）
    .venv/Scripts/python.exe tests/translation/run.py

    # 指定引擎
    .venv/Scripts/python.exe tests/translation/run.py --provider deepseek

    # 只看用例，不调用模型（检查用例文件本身没问题）
    .venv/Scripts/python.exe tests/translation/run.py --dry-run
"""
import argparse
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ))

import yaml  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases.yaml"
KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
MD = re.compile(r"```|^\s{0,3}#{1,6}\s|\*\*[^*]+\*\*", re.M)


def load_cases() -> list[dict]:
    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    out = []
    for c in data.get("cases") or []:
        # 单句用例 → 统一成 segments 形式
        if "segments" not in c:
            c["segments"] = [{"text": c["text"]}]
        out.append(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="index")
    ap.add_argument("--max-length-ratio", type=float, default=1.8)
    ap.add_argument("--dry-run", action="store_true",
                    help="只加载并校验用例文件，不调用模型")
    ap.add_argument("--json-out", default="", help="把结果写到这个 JSON 文件")
    args = ap.parse_args()

    cases = load_cases()
    print(f"用例数: {len(cases)}  引擎: {args.provider}")
    if args.dry_run:
        for c in cases:
            n = len(c["segments"])
            print(f"  [{c['id']}] {c['name']}  ({n} 条字幕)")
        print("\n（--dry-run：未调用模型）")
        return 0

    from src.translator import translate

    results = []
    passed = failed = 0
    for c in cases:
        segs = [{"start": float(i), "end": float(i + 1), **s}
                for i, s in enumerate(c["segments"])]
        try:
            out = translate(segs, provider=args.provider)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL [{c['id']}] 调用失败：{type(e).__name__}: {e}")
            failed += 1
            results.append({"id": c["id"], "ok": False, "error": str(e)})
            continue

        problems = []
        for src, seg in zip(segs, out):
            zh = (seg.get("translated") or "").strip()
            ja = src["text"]
            if not zh:
                problems.append("译文为空")
                continue
            if MD.search(zh):
                problems.append("含 Markdown")
            if len(zh) > len(ja) * args.max_length_ratio and len(zh) > 12:
                problems.append(f"过长（{len(zh)} 字 vs 原文 {len(ja)} 字）")
            if ja and len(KANA.findall(zh)) / len(zh) > 0.3:
                problems.append("日文残留较多")

        joined = "".join((s.get("translated") or "") for s in out)
        for want in c.get("expect_contains") or []:
            if want not in joined:
                problems.append(f"缺少术语「{want}」")

        ok = not problems
        passed += ok
        failed += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} [{c['id']}] {c['name']}")
        for src, seg in zip(segs, out):
            print(f"        {src['text']}")
            print(f"     →  {(seg.get('translated') or '').strip()}")
        if problems:
            print(f"        ⚠ {'；'.join(problems)}")

        results.append({"id": c["id"], "ok": ok, "problems": problems,
                        "pairs": [{"ja": s["text"], "zh": (o.get("translated") or "")}
                                  for s, o in zip(segs, out)]})

    print(f"\n{'=' * 54}\n  {passed} passed, {failed} failed\n{'=' * 54}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  结果已写入 {args.json_out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
