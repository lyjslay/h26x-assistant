"""帧预览：提取逐帧 PNG，并建立 显示序 ↔ 解码序(trace) ↔ POC 的映射。

关键事实(实测)：
- JM 写出的 decoded.yuv 与 ffmpeg 解出的图像都是**显示序**(POC 排序)。
- 我们的 trace 解析是**解码序**，每帧带 poc。
- 多 GOP 时 POC 在每个 IDR 处重置为 0，因此显示序映射需
  **按 IDR 分段(GOP)，段内按 POC 升序，段间保持先后**——这正是 JM 输出 YUV 的顺序。

图像用 ffmpeg 从裸流按显示序逐帧导出 PNG(惰性、单帧按需)。
"""
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import config, decoder, project, syntax

FRAMES_DIRNAME = "frames_png"


def _is_idr(frame: Dict) -> bool:
    for n in frame.get("nals", []):
        if n.get("nal_unit_type") == 5:
            return True
    return False


def build_frame_map(project_id: str, cfg=None) -> Dict[str, object]:
    """建立显示序映射。返回 {display: [ {display_index, decode_index, poc, slice_type} ], ...}。

    需要 trace 解析结果(解码序 + poc + 每帧 NAL 用于判 IDR)。
    """
    parsed = syntax._ensure_parsed(project_id, cfg=cfg)  # noqa: SLF001 复用缓存
    dec_frames = parsed["frames"]

    # 分段：以 IDR(nal_type 5) 或首帧起新段
    segments: List[List[int]] = []
    for i, fr in enumerate(dec_frames):
        if i == 0 or _is_idr(fr):
            segments.append([i])
        else:
            segments[-1].append(i)

    display_order: List[Dict] = []
    for seg in segments:
        # 段内按 POC 升序(POC 为 None 的排后)
        seg_sorted = sorted(
            seg, key=lambda di: (dec_frames[di]["poc"] is None,
                                 dec_frames[di]["poc"] if dec_frames[di]["poc"] is not None else 0))
        for di in seg_sorted:
            fr = dec_frames[di]
            display_order.append({
                "display_index": len(display_order),
                "decode_index": fr["index"],
                "poc": fr["poc"],
                "slice_type": fr["slice_type"],
                "frame_num": fr["frame_num"],
            })

    dec_to_disp = {d["decode_index"]: d["display_index"] for d in display_order}
    return {
        "num_frames": len(display_order),
        "display_order": display_order,
        "decode_to_display": dec_to_disp,
    }


def _frames_dir(proj: "project.Project") -> Path:
    return proj.dir / FRAMES_DIRNAME


def ensure_frame_png(project_id: str, display_index: int, cfg=None) -> Path:
    """按需导出某显示序帧的 PNG(惰性、缓存)。"""
    if cfg is None:
        cfg = config.load_config()
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    ffmpeg = cfg.get("ffmpeg", "")
    if not ffmpeg or not os.path.exists(ffmpeg):
        raise RuntimeError("未配置 ffmpeg")

    fdir = _frames_dir(proj)
    fdir.mkdir(parents=True, exist_ok=True)
    png = fdir / ("disp_%05d.png" % display_index)
    if png.exists() and png.stat().st_size > 0:
        return png

    es_path = proj.dir / meta["elementary_stream"]
    # 用 select 抽取第 display_index 帧(ffmpeg 输出为显示序)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(es_path),
        "-vf", "select=eq(n\\,%d)" % display_index,
        "-frames:v", "1", "-vsync", "0",
        str(png),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if proc.returncode != 0 or not png.exists():
        err = proc.stderr.decode("utf-8", errors="replace")[-400:]
        raise RuntimeError("帧图像导出失败: %s" % err)
    return png


def frame_map_for(project_id: str, cfg=None) -> Dict[str, object]:
    """带磁盘缓存的帧映射。"""
    proj = project.Project(project_id)
    cache = proj.dir / "frame_map.json"
    stamp = proj.dir / ".trace_stamp"
    mstamp = proj.dir / ".framemap_stamp"
    cur = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    import json
    if cache.exists() and mstamp.exists() and \
            mstamp.read_text(encoding="utf-8").strip() == cur:
        return json.loads(cache.read_text(encoding="utf-8"))
    fm = build_frame_map(project_id, cfg=cfg)
    cache.write_text(json.dumps(fm, ensure_ascii=False), encoding="utf-8")
    mstamp.write_text(cur, encoding="utf-8")
    return fm
