from __future__ import annotations

import json
import re
from pathlib import Path

from faster_whisper import WhisperModel


COURSE_DIR = Path(r"F:\new faiz")
OUT_DIR = Path("data/faiz_updated_course/transcripts/raw")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}
MODEL_NAME = "tiny.en"

PROMPT = (
    "Faiz SMC ICT trading course. Terms: SMC, ICT, liquidity, market structure, "
    "market structure shift, MSS, MSB, fair value gap, FVG, order block, OB, "
    "breaker block, BPR, balanced price range, SMT divergence, buy side liquidity, "
    "sell side liquidity, premium, discount, equilibrium, POI, ABC, ABCD, MMXM, "
    "Silver Bullet, Asian session, New York session, 9:30 AM, prop firm."
)


def stamp(seconds: float, comma: bool = False) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("_")


def write_outputs(path: Path, segments: list[dict], info: dict) -> None:
    stem = safe_stem(path)
    srt_lines: list[str] = []
    txt_lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        srt_lines.extend([
            str(i),
            f"{stamp(seg['start'], True)} --> {stamp(seg['end'], True)}",
            text,
            "",
        ])
        txt_lines.append(f"[{stamp(seg['start']).split('.')[0]}] {text}")

    (OUT_DIR / f"{stem}.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (OUT_DIR / f"{stem}.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    (OUT_DIR / f"{stem}.json").write_text(
        json.dumps({"source": str(path), "info": info, "segments": segments}, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in COURSE_DIR.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda p: (p.name.lower(), p.stat().st_size),
    )
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    for index, path in enumerate(files, start=1):
        stem = safe_stem(path)
        txt_path = OUT_DIR / f"{stem}.txt"
        if txt_path.exists():
            print(f"SKIP {index}/{len(files)} {path.name}", flush=True)
            continue
        print(f"TRANSCRIBE {index}/{len(files)} {path.name}", flush=True)
        raw_segments, info = model.transcribe(
            str(path),
            language="en",
            beam_size=1,
            vad_filter=True,
            initial_prompt=PROMPT,
            condition_on_previous_text=False,
        )
        segments = [
            {"start": float(seg.start), "end": float(seg.end), "text": seg.text}
            for seg in raw_segments
        ]
        write_outputs(
            path,
            segments,
            {
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
                "model": MODEL_NAME,
                "engine": "faster-whisper",
                "compute_type": "int8",
            },
        )


if __name__ == "__main__":
    main()
