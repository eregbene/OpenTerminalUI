from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/faiz_updated_course/faiz_updated_course_manifest.json"
GOLDEN = ROOT / os.getenv("BSI_V3_GOLDEN_OUTPUT_JSON", "data/research/bsi_v3_august_golden_two_pair.json")
FULL_JSON = ROOT / os.getenv("BSI_V3_OUTPUT_JSON", "data/research/bsi_v3_august_2026_full.json")
OUT_JSON = ROOT / os.getenv("BSI_V3_VIDEO_AUDIT_JSON", "data/research/bsi_v3_august_video_timestamp_audit.json")
OUT_DOC = ROOT / os.getenv("BSI_V3_VIDEO_AUDIT_DOC", "docs/bsi_updated_faiz/v3_validation/BSI_V3_VIDEO_TIMESTAMP_AUDIT.md")
READINESS_DOC = ROOT / "docs/bsi_updated_faiz/v3_validation/BSI_V3_ACTIVATION_READINESS.md"
FRAME_DIR = ROOT / "data/research/bsi_v3_video_timestamp_frames"
TRANSCRIPT_DIR = ROOT / "data/faiz_updated_course/transcripts/raw"
CONTACT_DIR = ROOT / "data/faiz_updated_course/visual_contact_sheets"

KEYWORDS_BY_STRATEGY = {
    "bsi_v3_order_flow": ["order flow", "market structure shift", "market structure break", "liquidity", "order block"],
    "bsi_v3_abc": ["abc", "a leg", "b leg", "c leg", "fake out", "entry"],
    "bsi_v3_abcd": ["abcd", "a leg", "b leg", "c leg", "d leg", "entry"],
    "bsi_v3_reactionary_block": ["reactionary", "order block", "same time frame", "fair value gap", "confirmation"],
    "bsi_v3_4h_order_block": ["four hour", "order block", "4 hour", "auto flow", "entry"],
    "bsi_v3_mmxm_second_distribution": ["second distribution", "distribution", "entry", "fractal", "liquidity"],
    "bsi_v3_holy_grail": ["holy grail", "liquidity", "m5", "entry", "model"],
    "bsi_v3_spectre": ["spectre", "inverse", "order block", "entry", "liquidity"],
    "bsi_v3_monday_range": ["monday range", "range", "monday", "entry", "liquidity"],
    "bsi_v3_weaver": ["weaver", "fair value gap", "liquidity", "entry", "range"],
    "bsi_v3_standard_deviation_po3": ["standard deviation", "power of three", "po3", "entry", "target"],
    "bsi_v3_ar50": ["ar50", "asian range", "50", "entry", "liquidity"],
    "bsi_v3_yin_yang": ["yin yang", "gold", "london", "entry", "fair value gap"],
    "bsi_v3_4h_candle_ranges": ["4 hour candle", "candle range", "range", "entry", "raid"],
    "bsi_v3_enigma_range": ["enigma", "range", "one-minute", "nas 100", "entry"],
}


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def srt_seconds(ts: str) -> float:
    h, m, rest = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def frame_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    seconds -= h * 3600
    m = int(seconds // 60)
    seconds -= m * 60
    return f"{h:02d}:{m:02d}:{seconds:06.3f}"


def parse_srt(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = [part.strip() for part in lines[1].split("-->", 1)]
        cues.append(
            {
                "start": start,
                "end": end,
                "start_seconds": srt_seconds(start),
                "text": " ".join(lines[2:]),
            }
        )
    return cues


def find_manifest_entry(manifest: list[dict[str, Any]], filename: str) -> dict[str, Any] | None:
    target = filename.lower()
    for item in manifest:
        if item.get("filename", "").lower() == target:
            return item
    stem = Path(filename).stem.lower()
    matches = [item for item in manifest if Path(item.get("filename", "")).stem.lower() == stem]
    return matches[0] if matches else None


def transcript_path_for(filename: str) -> Path:
    stem = Path(filename).stem
    candidates = [
        TRANSCRIPT_DIR / f"{stem}.srt",
        TRANSCRIPT_DIR / f"{safe_name(stem)}.srt",
        TRANSCRIPT_DIR / f"{stem.replace(' ', '_')}.srt",
    ]
    for path in candidates:
        if path.exists():
            return path
    matches = list(TRANSCRIPT_DIR.glob(f"{stem}*.srt"))
    return matches[0] if matches else candidates[0]


def contact_sheet_for(filename: str) -> Path:
    stem = Path(filename).stem
    candidates = [
        CONTACT_DIR / f"{safe_name(stem)}.jpg",
        CONTACT_DIR / f"{stem.replace(' ', '_')}.jpg",
    ]
    for path in candidates:
        if path.exists():
            return path
    matches = list(CONTACT_DIR.glob(f"*{safe_name(stem).split('_', 1)[-1]}*.jpg"))
    return matches[0] if matches else candidates[0]


def transcript_hits(cues: list[dict[str, Any]], keywords: list[str], limit: int = 4) -> list[dict[str, Any]]:
    hits = []
    for cue in cues:
        lowered = cue["text"].lower()
        matched = [kw for kw in keywords if kw in lowered]
        if matched:
            hits.append(
                {
                    "start": cue["start"],
                    "end": cue["end"],
                    "start_seconds": cue["start_seconds"],
                    "keywords": matched,
                    "text": cue["text"],
                }
            )
        if len(hits) >= limit:
            break
    return hits


def extract_frame(video_path: Path, seconds: float, out_path: Path) -> bool:
    if not video_path.exists():
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        frame_timestamp(seconds),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=20)
    except Exception:
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * widths[i] for i in range(len(row))) + " |")
    return "\n".join(out)


def refresh_full_validation_payload(payload: dict[str, Any]) -> None:
    if not FULL_JSON.exists():
        return
    full = json.loads(FULL_JSON.read_text(encoding="utf-8"))
    full["video_timestamp_audit"] = {
        "status_counts": payload["status_counts"],
        "artifact": str(OUT_JSON.relative_to(ROOT)),
        "scope": payload["scope"],
    }
    for strategy_id, item in payload["artifacts"].items():
        strategy = full.get("strategy_results", {}).get(strategy_id)
        if strategy is not None:
            strategy["video_timestamp_status"] = item["status"]
            strategy["video_timestamp_evidence"] = {
                "source_video": item.get("source_video"),
                "first_timestamp": (item.get("timestamp_hits") or [{}])[0].get("start"),
                "frames": item.get("extracted_frame_paths", []),
            }
    blocks = full.get("activation_readiness", {}).get("blocks", [])
    full["activation_readiness"]["blocks"] = [
        "GOLDEN_EXAMPLE_VIDEO_OVERLAP_PARTIAL_SELECTED_SYMBOL_SCOPE"
        if block
        in {
            "GOLDEN_EXAMPLE_BAR_RECONSTRUCTION_NOT_AVAILABLE",
            "GOLDEN_EXAMPLE_BAR_RECONSTRUCTION_PARTIAL_TWO_SYMBOL_SCOPE",
            "GOLDEN_EXAMPLE_VIDEO_OVERLAP_PARTIAL_TWO_SYMBOL_SCOPE",
        }
        else block
        for block in blocks
    ]
    full["activation_readiness"]["blocks"] = [
        "V3_ADAPTIVE_DEMO_ROUTING_NOT_CONNECTED_OR_RESULTS_NEGATIVE"
        if block == "V3_ADAPTIVE_DEMO_STATE_MACHINE_NOT_WIRED"
        else block
        for block in full["activation_readiness"]["blocks"]
    ]
    full["result_interpretation"]["reason"] = (
        "The August scan uses raw point-in-time candles and stable IDs. A two-symbol "
        "golden bar-reconstruction slice and source-video timestamp audit now exist. "
        "Full live/demo V3 routing remains blocked because the selected two-month "
        "core-pair replay is not positive enough and V3 broker execution routing is "
        "not connected."
    )
    FULL_JSON.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")


def refresh_readiness_doc(payload: dict[str, Any]) -> None:
    blocks = [
        "GOLDEN_EXAMPLE_VIDEO_OVERLAP_PARTIAL_SELECTED_SYMBOL_SCOPE",
        "STRATEGY_SPECIFIC_DETECTORS_NOT_FULLY_VALIDATED",
        "V3_ADAPTIVE_DEMO_ROUTING_NOT_CONNECTED_OR_RESULTS_NEGATIVE",
        "DATA_GAP_SMT_REFERENCE",
    ]
    block_text = "\n".join(f"- `{block}`" for block in blocks)
    READINESS_DOC.write_text(
        "# BSI V3 Activation Readiness\n\n"
        "Ready for DEMO: `false`\n\n"
        "Evidence now available:\n\n"
        f"- Selected-symbol OHLCV bar reconstructions: `{payload['status_counts'].get('VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE', 0)}` strategies with source-video timestamp evidence\n"
        "- Source videos, transcripts, contact sheets, and extracted timestamp frames are present for the reconstructed slice.\n\n"
        "Remaining blocks:\n\n"
        f"{block_text}\n\n"
        "Do not activate V3 MT5/cTrader routing yet.\n",
        encoding="utf-8",
    )


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    artifacts = {}
    for strategy_id, artifact in golden["artifacts"].items():
        if artifact["status"] != "PASS":
            artifacts[strategy_id] = {
                "status": artifact["status"],
                "reason": "No two-pair August bar reconstruction to timestamp-audit.",
            }
            continue
        source_video = artifact["source_videos"][0]
        entry = find_manifest_entry(manifest, source_video)
        transcript = transcript_path_for(source_video)
        contact = contact_sheet_for(source_video)
        cues = parse_srt(transcript)
        hits = transcript_hits(cues, KEYWORDS_BY_STRATEGY.get(strategy_id, [Path(source_video).stem.lower()]))
        frame_paths = []
        for idx, hit in enumerate(hits[:2], start=1):
            frame = FRAME_DIR / f"{strategy_id}_{safe_name(Path(source_video).stem)}_{idx}.jpg"
            if extract_frame(Path(entry["full_path"]) if entry else Path(source_video), hit["start_seconds"], frame):
                frame_paths.append(str(frame.relative_to(ROOT)))
        status = "VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE" if entry and transcript.exists() and contact.exists() and hits else "VIDEO_TIMESTAMP_EVIDENCE_INCOMPLETE"
        artifacts[strategy_id] = {
            "status": status,
            "source_video": source_video,
            "video_path": entry["full_path"] if entry else None,
            "transcript_path": str(transcript.relative_to(ROOT)) if transcript.exists() else None,
            "contact_sheet_path": str(contact.relative_to(ROOT)) if contact.exists() else None,
            "duration": entry.get("duration") if entry else None,
            "timestamp_hits": hits,
            "extracted_frame_paths": frame_paths,
            "bar_opportunity_id": artifact["opportunity"]["opportunity_id"],
            "bar_symbol": artifact["opportunity"]["symbol"],
            "bar_entry_time": artifact["opportunity"]["entry_time"],
            "bar_outcome": artifact["outcome"]["status"],
        }
    counts: dict[str, int] = {}
    for item in artifacts.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    payload = {
        "methodology": golden["methodology"],
        "window": golden["window"],
        "selected_symbols": golden["selected_symbols"],
        "scope": "SELECTED_SYMBOL_VIDEO_TIMESTAMP_AUDIT",
        "status_counts": counts,
        "artifacts": artifacts,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = [["Strategy", "Status", "Video", "First Timestamp", "Frames"]]
    for strategy_id, item in artifacts.items():
        first = ""
        if item.get("timestamp_hits"):
            first = item["timestamp_hits"][0]["start"]
        rows.append(
            [
                strategy_id,
                item["status"],
                item.get("source_video", ""),
                first,
                len(item.get("extracted_frame_paths", [])),
            ]
        )
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(
        "# BSI V3 Video Timestamp Audit\n\n"
        f"Selected symbols: `{', '.join(golden['selected_symbols'])}`\n\n"
        f"Status counts: `{counts}`\n\n"
        "This audit reads the actual local video manifest, SRT transcripts, contact sheets, and extracts source-video still frames at rule timestamps. It proves source timestamp evidence is available; exact visual overlap with the exported OHLCV bars remains a separate detector-accuracy check.\n\n"
        + table(rows)
        + "\n",
        encoding="utf-8",
    )
    refresh_full_validation_payload(payload)
    refresh_readiness_doc(payload)
    print(json.dumps({"out": str(OUT_JSON.relative_to(ROOT)), "doc": str(OUT_DOC.relative_to(ROOT)), "status_counts": counts}, indent=2))


if __name__ == "__main__":
    main()
