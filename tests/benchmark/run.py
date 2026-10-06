"""Index-Translate 与 DeepSeek 的对比基准。

任务要求「不要仅凭理论判断 Index 是否更好」—— 所以这里跑同一批日语字幕，
两个引擎各出一份译文，落盘成 `index.json` / `deepseek.json`，
再生成一份可读的对比报告 `report.md`。

⚠️ **这个脚本会真的调用模型**：
  · Index 需要本地 vLLM 已启动
  · DeepSeek 会调用云端 API 并**产生费用**（用少量短句，成本很低）

用法：
    # 两个都跑
    .venv/Scripts/python.exe tests/benchmark/run.py

    # 只跑其中一个
    .venv/Scripts/python.exe tests/benchmark/run.py --only index

    # 用其它用例文件
    .venv/Scripts/python.exe tests/benchmark/run.py --cases tests/translation/cases.yaml

输出目录：tests/benchmark/out/
"""
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ))

import yaml  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
MD = re.compile(r"```|^\s{0,3}#{1,6}\s|\*\*[^*]+\*\*", re.M)
EXPLAIN = re.compile(r"^\s*(以下是|下面是|译文如下|翻译如下|Here is|Translation[:：])", re.I)


def load_cases(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for c in data.get("cases") or []:
        if "segments" not in c:
            c["segments"] = [{"text": c["text"]}]
        cases.append(c)
    return cases


def run_provider(cases: list[dict], provider: str) -> dict:
    from src.translator import translate

    results = {}
    for c in cases:
        segs = [{"start": float(i), "end": float(i + 1), **s}
                for i, s in enumerate(c["segments"])]
        try:
            out = translate(segs, provider=provider)
            results[c["id"]] = {
                "ok": True,
                "pairs": [{"ja": s["text"], "zh": (o.get("translated") or "").strip()}
                          for s, o in zip(segs, out)],
            }
        except Exception as e:  # noqa: BLE001
            results[c["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return results


# ── 客观指标（不看语义质量，只看能自动判定的东西）──

def metrics(cases: list[dict], res: dict) -> dict:
    total = len(cases)
    ok = sum(1 for c in cases if res.get(c["id"], {}).get("ok"))
    lengths, ratios = [], []
    kana_res, md_bad, explain_bad, empty = 0, 0, 0, 0
    glossary_hit = glossary_total = 0

    for c in cases:
        r = res.get(c["id"]) or {}
        if not r.get("ok"):
            continue
        for p in r["pairs"]:
            zh, ja = p["zh"], p["ja"]
            if not zh:
                empty += 1
                continue
            lengths.append(len(zh))
            if ja:
                ratios.append(len(zh) / max(1, len(ja)))
            if len(KANA.findall(zh)) / len(zh) > 0.3:
                kana_res += 1
            if MD.search(zh):
                md_bad += 1
            if EXPLAIN.match(zh):
                explain_bad += 1
        # 术语落实（用词典目标词做子串检查）
        from src.glossary import build_glossary_pairs
        for want in c.get("expect_contains") or []:
            glossary_total += 1
            if want in "".join(p["zh"] for p in r["pairs"]):
                glossary_hit += 1

    return {
        "cases_ok": f"{ok}/{total}",
        "lines": len(lengths),
        "avg_length": round(statistics.mean(lengths), 1) if lengths else 0,
        "avg_length_ratio": round(statistics.mean(ratios), 2) if ratios else 0,
        "max_length_ratio": round(max(ratios), 2) if ratios else 0,
        "empty": empty,
        "kana_residue": kana_res,
        "markdown": md_bad,
        "explanation": explain_bad,
        "glossary_hit": f"{glossary_hit}/{glossary_total}" if glossary_total else "n/a",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(PROJ / "tests/translation/cases.yaml"))
    ap.add_argument("--only", default="", help="只跑某个引擎（index / deepseek）")
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    providers = [args.only] if args.only else ["index", "deepseek"]

    OUT.mkdir(parents=True, exist_ok=True)
    all_res, all_met = {}, {}
    for p in providers:
        print(f"── 跑 {p}（{len(cases)} 条用例）…")
        res = run_provider(cases, p)
        (OUT / f"{p}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        all_res[p] = res
        all_met[p] = metrics(cases, res)
        bad = [k for k, v in res.items() if not v.get("ok")]
        print(f"   完成，失败 {len(bad)} 条" + (f"：{bad}" if bad else ""))

    # ── 生成报告 ──
    lines = ["# Index-Translate vs DeepSeek — 对比报告", ""]
    lines.append("同一批日语字幕，两个引擎各译一遍。")
    lines.append("")
    lines.append("> 本报告只含**可自动判定**的客观指标。")
    lines.append("> 准确性 / 自然度这类需要人看的维度，请直接对照下面的逐条译文自行判断 ——")
    lines.append("> 让脚本给「翻译质量」打分只会得到看起来科学、实际没有依据的数字。")
    lines.append("")

    keys = ["cases_ok", "lines", "avg_length", "avg_length_ratio",
            "max_length_ratio", "empty", "kana_residue", "markdown",
            "explanation", "glossary_hit"]
    labels = {
        "cases_ok": "用例成功", "lines": "译文条数", "avg_length": "平均字数",
        "avg_length_ratio": "平均长度比(中/日)", "max_length_ratio": "最大长度比",
        "empty": "空译文", "kana_residue": "日文残留条数", "markdown": "含 Markdown",
        "explanation": "含解释文字", "glossary_hit": "术语命中",
    }
    lines.append("## 客观指标")
    lines.append("")
    lines.append("| 指标 | " + " | ".join(all_met) + " |")
    lines.append("|---|" + "---|" * len(all_met))
    for k in keys:
        row = [str(all_met[p].get(k, "")) for p in all_met]
        lines.append(f"| {labels.get(k, k)} | " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## 逐条对照")
    lines.append("")
    for c in cases:
        lines.append(f"### {c['name']}（`{c['id']}`）")
        lines.append("")
        pairs_by_p = {}
        for p in all_res:
            r = all_res[p].get(c["id"]) or {}
            pairs_by_p[p] = r.get("pairs") if r.get("ok") else None
        n = len(c["segments"])
        for i in range(n):
            lines.append(f"**原文**：{c['segments'][i]['text']}")
            lines.append("")
            for p in all_res:
                if pairs_by_p[p] is None:
                    lines.append(f"- `{p}`：（失败）")
                else:
                    lines.append(f"- `{p}`：{pairs_by_p[p][i]['zh']}")
            lines.append("")
        if c.get("expect_contains"):
            lines.append(f"要求出现的术语：{'、'.join(c['expect_contains'])}")
            lines.append("")

    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print()
    print("| 指标 | " + " | ".join(all_met) + " |")
    print("|---|" + "---|" * len(all_met))
    for k in keys:
        print(f"| {labels.get(k, k)} | "
              + " | ".join(str(all_met[p].get(k, "")) for p in all_met) + " |")
    print()
    print(f"逐条译文与报告：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
