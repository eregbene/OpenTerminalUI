from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


COURSE_DIR = Path(r"F:\new faiz")
DOC_DIR = Path("docs/bsi_updated_faiz")
DATA_DIR = Path("data/faiz_updated_course")
TRANSCRIPT_DIR = DATA_DIR / "transcripts" / "raw"
VISUAL_DIR = DATA_DIR / "visual_contact_sheets"
MANIFEST_JSON = DATA_DIR / "faiz_updated_course_manifest.json"

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}

CURRICULUM_SECTIONS = {
    "SMC BASICS": [
        "Introduction",
        "Market Structure",
        "Market Structure Break & Market Structure Shift",
        "MSB & MSS Example",
        "Liquidity",
        "Liquidity Example",
        "Fair Value Gap",
        "Premium & Discount Zone",
        "Orderblocks",
        "Important",
    ],
    "2. NO DAILY BIAS STRATEGIES": [
        "Order Flow Trading Strategy",
        "Order Flow Example For Market Structure Shift (1)",
        "Order Flow Example For Market Structure Shift (2)",
        "Order Flow Example For Market Structure Break (1)",
        "Order Flow Example For Market Structure Break (2)",
        "Asian Session Trading Strategy",
        "Asian Session Trading Strategy Example 1",
        "Asian Session Trading Strategy Example 2",
        "Asian Session Trading Strategy Example 3",
        "ABC Trading Strategy",
        "ABC Trading Strategy Example 1",
        "ABC Trading Strategy Example 2",
        "9:30AM Trading Strategy (Updated)",
        "9:30AM Trading Strategy Example",
        "9:30AM Trading Strategy Example 2",
        "9:30AM Trading Strategy Example 3",
        "Reactionary Block Trading Strategy",
        "Reactionary Block Trading Strategy Example",
        "ABCD Trading Strategy",
        "ABCD Trading Strategy Example",
        "The Juggernaut Model",
        "The Juggernaut Example 1",
        "The Juggernaut Example 2",
        "The Juggernaut Example 3",
        "The Spectre Model",
        "The Spectre Example 1",
        "The Spectre Example 2",
        "The Spectre Example 3",
    ],
    "ICT ESSENTIALS": [
        "SMT Divergence",
        "Valid MSS vs Invalid MSS",
        "Invalid MSS Example",
        "Valid MSS Example",
        "ABC POI Combination",
        "ABC POI Combination Example",
        "Confirmation Entries",
        "Confirmation Entry Example",
        "Entry Patterns",
        "Things To Avoid In ABC Strategy",
        "VOLUME IMBALANCE",
        "BPR",
        "BREAKER BLOCK",
        "BREAKER BLOCK EXAMPLE 1",
        "BREAKER BLOCK EXAMPLE 2",
        "ABCD 101",
        "ABCD 101 EXAMPLE 1",
        "ABCD 101 EXAMPLE 2",
        "ABC 101",
        "ORDERFLOW 101",
        "ORDERFLOW 101 EXAMPLE 1",
        "ORDERFLOW 101 EXAMPLE 2",
        "ASIAN SESSION STRATEGY V2.0",
        "ASIAN SESSION STRATEGY V2.0 EXAMPLE 1",
        "ASIAN SESSION STRATEGY V2.0 EXAMPLE 2",
        "DAILY BIAS MADE EASY",
        "Risk Management & Psychology",
        "Orderblock 2.0",
        "Orderblock 2.0 Example",
        "ICT Silver Bullet",
        "ICT Silver Bullet Example 1",
        "ICT Silver Bullet Example 2",
        "ICT Silver Bullet Example 3",
        "4 Hour OB Trading Strategy",
        "4 Hour OB Trading Strategy",
        "4 Hour OB Trading Strategy Example 2",
        "4 Hour OB Trading Strategy Example 2 (1)",
        "4 Hour OB Trading Strategy Example 3",
    ],
    "ADVANCED TRADING STRATEGIES": [
        "Finding Monthly, Weekly, & Daily Bias",
        "Monthly Bias Example 1",
        "Weekly Bias Example 1",
        "Daily Bias Example 1",
        "Monthly Bias Example 2",
        "Weekly Bias Example 2",
        "Daily Bias Example 2",
        "THE MMXM",
        "THE MMXM 2",
        "THE MMXM EXAMPLE 1",
        "THE MMXM EXAMPLE 2",
        "THE MMXM EXAMPLE 3",
        "MMXM 2ND DISTRIBUTION ENTRY",
        "2ND DISTRIBUTION EXAMPLE 1",
        "2ND DISTRIBUTION EXAMPLE 2",
        "2ND DISTRIBUTION EXAMPLE 3",
        "The Holy Grail",
        "The Holy Grail 2",
        "The Holy Grail Example 1",
        "The Holy Grail Example 2",
        "The Holy Grail Example 3",
        "The Holy Grail Example 4",
        "Risk Management For Prop Firm Accounts",
        "Silver Bullet With Bias",
        "Silver Bullet With Bias Example 1",
        "Silver Bullet With Bias Example 2",
        "FVG LIQUIDITY",
        "FVG LIQUIDITY EXAMPLES",
        "Standard Deviations",
        "Quarterly Theory part 2",
        "Quarterly Theory Example 1",
        "Quarterly Theory Example 2",
        "AR50 Trading Model",
        "AR50 Example 1",
        "AR50 Example 2",
        "Utilizing Monday Range",
        "The Weaver Model",
        "The IFVG Model",
        "Turtle Soups & Ranges Mastery",
        "The Yin Yang Model",
        "4 Hour Candle Ranges",
        "Utilizing SMT With Session Highs & Lows",
        "1 Hour Candle Ranges",
        "The Enigma",
    ],
}


@dataclass
class VideoRow:
    sequence: int
    number: int | None
    title: str
    filename: str
    full_path: str
    bytes: int
    duration: str | None
    seconds: float | None
    resolution: str | None
    has_audio: bool
    has_video: bool
    readable: bool
    transcript_status: str
    visual_audit_status: str
    section: str
    lesson_or_example: str
    strategy_relationship: str
    source_status: str


def norm(value: str) -> str:
    value = value.lower()
    value = value.replace("9:30", "930")
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"^\s*\d+\s*\.?\s*", "", stem).strip()
    return stem


def ffmpeg_probe(path: Path) -> tuple[str | None, float | None, str | None, bool, bool, bool]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    duration = None
    seconds = None
    resolution = None
    has_audio = "Audio:" in text
    has_video = "Video:" in text
    readable = not re.search(
        r"Invalid data found|moov atom not found|Error opening input|could not find codec parameters",
        text,
        re.I,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if match:
        h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
        seconds = h * 3600 + m * 60 + s
        duration = f"{h:02d}:{m:02d}:{s:05.2f}"
    match = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", text)
    if match:
        resolution = f"{match.group(1)}x{match.group(2)}"
    return duration, seconds, resolution, has_audio, has_video, readable


def classify(title: str) -> tuple[str, str, str]:
    t = norm(title)
    for section, titles in CURRICULUM_SECTIONS.items():
        for course_title in titles:
            if norm(course_title) == t:
                lesson_type = "example" if "example" in t else "lesson"
                relationship = infer_relationship(course_title)
                return section, lesson_type, relationship
    return "UNMAPPED / EXTRA", "example" if "example" in t else "lesson", infer_relationship(title)


def infer_relationship(title: str) -> str:
    t = norm(title)
    if any(x in t for x in ["example", "trade breakdown"]):
        return "mentor example"
    if any(x in t for x in ["risk", "psychology"]):
        return "risk model"
    if any(x in t for x in ["bias", "market structure", "liquidity", "fvg", "premium", "discount", "orderblocks", "valid mss", "smt", "volume imbalance", "bpr", "breaker", "confirmation", "entry patterns"]):
        return "primitive/confluence"
    return "strategy/model"


def transcript_exists(title: str) -> bool:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_")
    for ext in [".srt", ".vtt", ".txt", ".tsv", ".json"]:
        if (TRANSCRIPT_DIR / f"{title}{ext}").exists() or (TRANSCRIPT_DIR / f"{safe}{ext}").exists():
            return True
    return False


def visual_exists(title: str) -> bool:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_")
    return (VISUAL_DIR / f"{safe}.jpg").exists()


def find_video_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda p: (str(p.parent).lower(), p.name.lower(), p.stat().st_size),
    )


def build_rows() -> list[VideoRow]:
    files = find_video_files(COURSE_DIR)
    rows: list[VideoRow] = []
    for idx, path in enumerate(files, start=1):
        title = title_from_filename(path.name)
        number = None
        m = re.match(r"^\s*(\d+)\s*[\.]", path.name)
        if m:
            number = int(m.group(1))
        duration, seconds, resolution, has_audio, has_video, readable = ffmpeg_probe(path)
        section, lesson_type, relationship = classify(title)
        rows.append(
            VideoRow(
                sequence=idx,
                number=number,
                title=title,
                filename=path.name,
                full_path=str(path),
                bytes=path.stat().st_size,
                duration=duration,
                seconds=seconds,
                resolution=resolution,
                has_audio=has_audio,
                has_video=has_video,
                readable=readable,
                transcript_status="DONE" if transcript_exists(path.stem) else "PENDING",
                visual_audit_status="CONTACT_SHEET_DONE" if visual_exists(path.stem) else "PENDING",
                section=section,
                lesson_or_example=lesson_type,
                strategy_relationship=relationship,
                source_status="UPDATED_FOLDER",
            )
        )
    return rows


def curriculum_map(rows: list[VideoRow]) -> dict[str, list[dict[str, str]]]:
    updated_by_title: dict[str, list[VideoRow]] = {}
    for row in rows:
        updated_by_title.setdefault(norm(row.title), []).append(row)

    mapped: dict[str, list[dict[str, str]]] = {}
    for section, titles in CURRICULUM_SECTIONS.items():
        mapped[section] = []
        for title in titles:
            key = norm(title)
            found = updated_by_title.get(key, [])
            if found:
                status = "FOUND_UPDATED"
                files = " | ".join(r.filename for r in found)
            else:
                status = "MISSING"
                files = ""
            mapped[section].append({"title": title, "status": status, "files": files})
    return mapped


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    out.append("| " + " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(rows[0])) + " |")
    out.append("| " + " | ".join("-" * widths[i] for i in range(len(rows[0]))) + " |")
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return "\n".join(out)


def write_manifest(rows: list[VideoRow]) -> None:
    nums = [r.number for r in rows if r.number is not None]
    missing = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    same_size: dict[int, list[str]] = {}
    for row in rows:
        same_size.setdefault(row.bytes, []).append(row.filename)
    same_size_dupes = {size: names for size, names in same_size.items() if len(names) > 1}
    lines = [
        "# Faiz Updated Course Video Manifest",
        "",
        "- Source folder: `F:\\new faiz`",
        f"- Total video files: `{len(rows)}`",
        f"- First numbered video: `{min(nums)}`",
        f"- Final numbered video: `{max(nums)}`",
        f"- Missing numbered lessons: `{', '.join(map(str, missing)) if missing else 'none'}`",
        f"- Unreadable/corrupt/no-audio videos: `{'none' if all(r.readable and r.has_audio and r.has_video for r in rows) else 'see table'}`",
        "- Note: duplicate lesson numbers are expected because the updated folder is a flat copy of multiple sections.",
        "",
        "## Same-Size Duplicate Files",
        "",
    ]
    if same_size_dupes:
        for size, names in same_size_dupes.items():
            lines.append(f"- `{size}` bytes: " + " | ".join(f"`{name}`" for name in names))
    else:
        lines.append("- None")
    lines.extend(["", "## Canonical Manifest", ""])
    table_rows = [["Seq", "No.", "Title", "Section", "Duration", "Resolution", "Audio", "Transcript", "Visual", "Relationship"]]
    for row in rows:
        table_rows.append([
            str(row.sequence),
            "" if row.number is None else str(row.number),
            row.title,
            row.section,
            row.duration or "",
            row.resolution or "",
            "yes" if row.has_audio else "no",
            row.transcript_status,
            row.visual_audit_status,
            row.strategy_relationship,
        ])
    lines.append(markdown_table(table_rows))
    lines.extend(["", "## Curriculum Section Map", ""])
    for section, entries in curriculum_map(rows).items():
        lines.extend([f"### {section}", ""])
        mrows = [["Course Title", "Status", "File(s)"]]
        for entry in entries:
            mrows.append([entry["title"], entry["status"], entry["files"]])
        lines.append(markdown_table(mrows))
        lines.append("")
    (DOC_DIR / "01_FAIZ_UPDATED_COURSE_VIDEO_MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_indices(rows: list[VideoRow]) -> None:
    transcript_lines = [
        "# Faiz Updated Complete Transcript Index",
        "",
        "Transcription source is local Whisper. Files are stored in `data/faiz_updated_course/transcripts/raw/`.",
        "",
    ]
    visual_lines = [
        "# Faiz Updated Visual Audit Index",
        "",
        "Contact sheets are stored in `data/faiz_updated_course/visual_contact_sheets/` and sample each video through the full runtime.",
        "",
    ]
    trows = [["Seq", "Title", "Status", "Primary Transcript File"]]
    vrows = [["Seq", "Title", "Status", "Contact Sheet"]]
    for row in rows:
        stem = Path(row.filename).stem
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")
        trows.append([str(row.sequence), row.title, row.transcript_status, f"`{safe}.srt`"])
        vrows.append([str(row.sequence), row.title, row.visual_audit_status, f"`{safe}.jpg`"])
    transcript_lines.append(markdown_table(trows))
    visual_lines.append(markdown_table(vrows))
    (DOC_DIR / "02_FAIZ_UPDATED_COMPLETE_TRANSCRIPT_INDEX.md").write_text("\n".join(transcript_lines) + "\n", encoding="utf-8")
    (DOC_DIR / "03_FAIZ_UPDATED_VISUAL_AUDIT_INDEX.md").write_text("\n".join(visual_lines) + "\n", encoding="utf-8")


def write_stubs(rows: list[VideoRow]) -> None:
    stubs = {
        "04_FAIZ_UPDATED_ICT_SMC_PRIMITIVES.md": "# Faiz Updated ICT/SMC Primitives\n\nStatus: pending transcript + visual rule extraction.\n",
        "05_FAIZ_UPDATED_BIAS_MODEL.md": "# Faiz Updated Bias Model\n\nStatus: pending transcript + visual rule extraction.\n",
        "06_FAIZ_UPDATED_ENTRY_MODELS.md": "# Faiz Updated Entry Models\n\nStatus: pending transcript + visual rule extraction.\n",
        "07_FAIZ_UPDATED_STRATEGY_RULEBOOK.md": "# Faiz Updated Strategy Rulebook\n\nStatus: pending transcript + visual rule extraction.\n",
        "08_FAIZ_UPDATED_STRATEGY_VERSION_LINEAGE.md": "# Faiz Updated Strategy Version Lineage\n\nStatus: pending transcript + visual rule extraction.\n",
        "09_FAIZ_UPDATED_TRENDLINE_AUDIT.md": "# Faiz Updated Trendline Audit\n\nStatus: pending transcript + visual search.\n",
        "10_FAIZ_UPDATED_RISK_MANAGEMENT.md": "# Faiz Updated Risk Management\n\nStatus: pending transcript + visual rule extraction.\n",
        "11_FAIZ_UPDATED_COURSE_TO_BSI_V2_GAP.md": "# Faiz Updated Course To BSI V2 Gap\n\nStatus: pending completed V3 methodology.\n",
        "12_BSI_V3_UPDATED_FAIZ_IMPLEMENTATION_SPEC.md": "# BSI V3 Updated Faiz Implementation Spec\n\nMethodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.\n\nStatus: pending completed reconstruction. V3 is not deployed.\n",
        "13_BSI_V3_RAW_EXTRACTION_SPEC.md": "# BSI V3 Raw Extraction Spec\n\nRequired path: raw bars -> swings -> structure -> liquidity -> PD/bias -> POI/arrays -> strategy state machine -> entry opportunity -> execution.\n\nStatus: pending completed reconstruction.\n",
        "14_BSI_V3_TIMEFRAME_MATRIX.md": "# BSI V3 Timeframe Matrix\n\nStatus: pending evidence classification.\n",
        "15_BSI_V3_INSTRUMENT_MATRIX.md": "# BSI V3 Instrument Matrix\n\nStatus: pending visual/spoken instrument extraction.\n",
        "16_BSI_V3_GOLDEN_VISUAL_TEST_PLAN.md": "# BSI V3 Golden Visual Test Plan\n\nStatus: pending example-by-example reconstruction.\n",
        "17_BSI_V3_REPLAY_PLAN.md": "# BSI V3 Replay Plan\n\nStatus: pending completed methodology and golden visual tests.\n",
        "18_BSI_V3_ADAPTIVE_MANAGER_SPEC.md": "# BSI V3 Adaptive Manager Spec\n\nStatus: pending mentor management extraction. Do not activate before replay validation.\n",
        "19_BSI_V3_RULE_SOURCE_INDEX.md": "# BSI V3 Rule Source Index\n\nStatus: pending source trace extraction.\n",
        "20_BSI_V3_FINAL_RECONSTRUCTION_REPORT.md": "# BSI V3 Final Reconstruction Report\n\nStatus: pending completion of transcription, visual audit, and methodology reconstruction.\n",
    }
    for name, body in stubs.items():
        path = DOC_DIR / name
        if not path.exists():
            path.write_text(body, encoding="utf-8")


def main() -> None:
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    MANIFEST_JSON.write_text(json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8")
    write_manifest(rows)
    write_indices(rows)
    write_stubs(rows)
    print(f"wrote {MANIFEST_JSON}")
    print(f"wrote docs under {DOC_DIR}")


if __name__ == "__main__":
    main()
