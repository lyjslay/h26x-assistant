"""码率分析：逐帧、逐秒、按 GOP 平均 + 波动指标。

数据来源：ffprobe -show_packets (无需解码，秒级完成)。
- 逐帧码率：每包 size*8 bits。
- 逐秒码率：按 floor(pts_time) 分桶求和 -> bps。
- GOP：以关键帧(flags 含 'K')切分，每 GOP 内 Σbits / GOP时长；
  再算波动指标(峰值/均值/标准差/峰均比)。
"""
import json
import math
import os
import subprocess
from statistics import mean, pstdev
from typing import Dict, List, Optional

from . import config, project


def _probe_packets(ffprobe: str, es_path: str, codec: str) -> List[Dict[str, object]]:
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,dts_time,duration_time,size,flags",
        "-of", "json", es_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    return data.get("packets", [])


def _num(v, default=0.0) -> float:
    try:
        if v is None or v == "N/A":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def analyze_bitrate(project_id: str, cfg: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    if cfg is None:
        cfg = config.load_config()
    ffprobe = cfg.get("ffprobe", "")
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    es_path = str(proj.dir / meta["elementary_stream"])
    codec = meta["codec"]
    fps = float(meta.get("fps") or 0.0)

    packets = _probe_packets(ffprobe, es_path, codec)
    if not packets:
        raise RuntimeError("ffprobe 未解析到任何视频包")

    # ---- 逐帧 ----
    # 裸流 pts_time 常为 N/A，用 帧号/fps 兜底
    per_frame = []
    for i, pk in enumerate(packets):
        size = int(_num(pk.get("size"), 0))
        pts = _num(pk.get("pts_time"), None if pk.get("pts_time") in (None, "N/A") else 0.0)
        if pts is None:
            pts = (i / fps) if fps > 0 else float(i)
        is_key = "K" in (pk.get("flags") or "")
        per_frame.append({
            "idx": i,
            "pts": round(pts, 6),
            "size": size,
            "bits": size * 8,
            "key": is_key,
        })

    # 每帧时长：优先 duration_time，否则 1/fps
    default_dt = (1.0 / fps) if fps > 0 else 1.0

    # ---- 逐秒分桶 ----
    per_second_map = {}
    for f in per_frame:
        sec = int(math.floor(f["pts"]))
        per_second_map[sec] = per_second_map.get(sec, 0) + f["bits"]
    per_second = [
        {"second": s, "bitrate_bps": per_second_map[s]}
        for s in sorted(per_second_map)
    ]

    # ---- GOP 切分(相邻关键帧之间) ----
    gops = []
    cur_start = 0
    for i, f in enumerate(per_frame):
        if f["key"] and i != 0:
            gops.append((cur_start, i - 1))
            cur_start = i
    gops.append((cur_start, len(per_frame) - 1))

    per_gop = []
    for gi, (a, b) in enumerate(gops):
        frames = per_frame[a:b + 1]
        total_bits = sum(x["bits"] for x in frames)
        n = len(frames)
        # GOP 时长：末帧 pts - 首帧 pts + 一帧时长
        span = (frames[-1]["pts"] - frames[0]["pts"]) + default_dt
        if span <= 0:
            span = n * default_dt
        per_gop.append({
            "gop": gi,
            "start_frame": a,
            "end_frame": b,
            "num_frames": n,
            "total_bits": total_bits,
            "duration": round(span, 6),
            "avg_bitrate_bps": round(total_bits / span, 2) if span else 0,
            "peak_frame_bits": max(x["bits"] for x in frames),
        })

    # ---- 汇总/波动 ----
    frame_bits = [f["bits"] for f in per_frame]
    gop_avgs = [g["avg_bitrate_bps"] for g in per_gop]
    total_bits = sum(frame_bits)
    total_dur = (per_frame[-1]["pts"] - per_frame[0]["pts"]) + default_dt
    overall_bps = total_bits / total_dur if total_dur > 0 else 0.0

    type_counts = {"key": sum(1 for f in per_frame if f["key"]),
                   "non_key": sum(1 for f in per_frame if not f["key"])}

    def _safe(fn, seq):
        return round(fn(seq), 2) if seq else 0.0

    summary = {
        "num_frames": len(per_frame),
        "num_gops": len(per_gop),
        "duration": round(total_dur, 4),
        "overall_bitrate_bps": round(overall_bps, 2),
        "frame_bits_mean": _safe(mean, frame_bits),
        "frame_bits_max": max(frame_bits) if frame_bits else 0,
        "frame_bits_min": min(frame_bits) if frame_bits else 0,
        "gop_avg_mean": _safe(mean, gop_avgs),
        "gop_avg_max": max(gop_avgs) if gop_avgs else 0,
        "gop_avg_min": min(gop_avgs) if gop_avgs else 0,
        "gop_avg_std": _safe(pstdev, gop_avgs) if len(gop_avgs) > 1 else 0.0,
        "gop_peak_to_mean": round(
            (max(gop_avgs) / mean(gop_avgs)) if gop_avgs and mean(gop_avgs) else 0.0, 3),
    }

    result = {
        "project_id": project_id,
        "codec": codec,
        "fps": fps,
        "per_frame": per_frame,
        "per_second": per_second,
        "per_gop": per_gop,
        "type_counts": type_counts,
        "summary": summary,
    }
    # 缓存
    (proj.dir / "bitrate.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    return result
