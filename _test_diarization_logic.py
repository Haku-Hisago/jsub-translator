"""Regression checks for the speaker-diarization logic (offline, no audio).

Run:  .venv/Scripts/python.exe _test_diarization_logic.py
All checks must pass before rebuilding the EXE.
"""
import sys

from src.diarizer import (
    _find_best_speaker,
    _nearest_speaker,
    split_segments_by_speaker,
    _word_timestamps_reliable,
)
from src.ass_writer import SPEAKER_COLORS, _short_speaker
from src.config import DIARIZE_CLUSTER_THRESHOLD, validate_diarization

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


# ---------------------------------------------------------------- turns
TURNS = [
    (0.0, 5.0, "SPEAKER_00"),
    (5.0, 10.0, "SPEAKER_01"),
    (10.0, 15.0, "SPEAKER_00"),
]
print("\n[overlap vote]")
check("full overlap picks the containing turn",
      _find_best_speaker(1.0, 4.0, TURNS) == "SPEAKER_00")
check("60/40 split picks the dominant speaker",
      _find_best_speaker(4.0, 6.0, TURNS) == "SPEAKER_00")
check("40/60 split picks the other speaker",
      _find_best_speaker(4.5, 6.5, TURNS) == "SPEAKER_01")

print("\n[nearest-speaker fallback]")
check("gap inside 8s is resolved",
      _nearest_speaker(15.5, 16.0, TURNS) == "SPEAKER_00")
check("gap beyond 8s refuses to guess",
      _nearest_speaker(100.0, 101.0, TURNS) == "")
check("touching turn resolves with zero gap",
      _nearest_speaker(5.0, 5.0, TURNS) != "")

print("\n[word-timestamp reliability gate]")
good = [{"text": "こんにちは", "words": [
    {"word": "こん", "start": 0, "end": 1},
    {"word": "にちは", "start": 1, "end": 2}]}]
bad = [{"text": "こんにちはありがとう", "words": [
    {"word": "XYZ", "start": 0, "end": 1},
    {"word": "QQQ", "start": 1, "end": 2}]}]
check("aligned words pass", _word_timestamps_reliable(good)[0] is True)
check("misaligned words fail", _word_timestamps_reliable(bad)[0] is False)
check("no words at all fails", _word_timestamps_reliable(
    [{"text": "abc"}])[0] is False)
reason = _word_timestamps_reliable(bad)[1]
check("failure reason is populated", bool(reason), repr(reason))

print("\n[speaker splitting]")
segs = [{"start": 0.0, "end": 12.0, "text": "ABCDEFGHIJKL",
         "words": [{"word": c, "start": float(i), "end": float(i + 1)}
                   for i, c in enumerate("ABCDEFGHIJKL")]}]
out = split_segments_by_speaker(segs, TURNS)
check("segment spanning a change is split", len(out) > 1, f"got {len(out)}")
check("no piece is empty-texted", all(p["text"] for p in out))
check("text is not lost overall",
      "".join(p["text"] for p in out) == "ABCDEFGHIJKL")
check("every piece has a speaker", all(p.get("speaker") for p in out))

no_words = [{"start": 0.0, "end": 3.0, "text": "x"}]
check("segment without words is untouched",
      len(split_segments_by_speaker(no_words, TURNS)) == 1)

print("\n[short speaker labels]")
check("SPEAKER_00 -> S00", _short_speaker("SPEAKER_00") == "S00")
check("SPEAKER_12 -> S12", _short_speaker("SPEAKER_12") == "S12")
check("empty stays empty", _short_speaker("") == "")

print("\n[colour palette]")
check("no pure white in palette",
      "&H00FFFFFF" not in SPEAKER_COLORS, str(SPEAKER_COLORS))
check("palette has >= 2 distinct colours", len(set(SPEAKER_COLORS)) >= 2)
check("palette covers at least 4 speakers", len(SPEAKER_COLORS) >= 4)

print("\n[config + availability]")
check("cluster threshold default is 0.92",
      abs(DIARIZE_CLUSTER_THRESHOLD - 0.92) < 1e-9,
      f"got {DIARIZE_CLUSTER_THRESHOLD}")
ok, why = validate_diarization()
check("diarization reports available", ok is True, why)
check("availability reason is non-empty", bool(why))

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
