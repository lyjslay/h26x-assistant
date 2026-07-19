"""每帧原始数据分段：起始码 / NAL 头 / 参数集/片头 / 各宏块 的字节区间。

对齐策略：
- trace 的 NAL 顺序 == 裸流 NAL 顺序，一一对应(按出现序)。
- 起始码、NAL 头、SPS/PPS/SEI 载荷：**字节精确**(来自字节扫描)。
- 片(slice)内每个宏块的字节区间：用 trace 每个宏块的**相对 bit 位置**
  (mb_i.first_bit - mb_0.first_bit)按比例映射到该 slice 的载荷字节区间。
  · CAVLC：相对 bit 是真实比特距离 → **精确**。
  · CABAC：@bit 是符号计数(算术编码不按 bit 对齐) → **近似**(标注 approx)。
每帧对外给出：分段列表 + 该帧涉及的字节数据(hex 由接口层按需截取)。
"""
from pathlib import Path
from typing import Dict, List, Optional

from . import nal_bytes, project, syntax

# 分段类型
KIND_START_CODE = "start_code"
KIND_NAL_HEADER = "nal_header"
KIND_PARAM = "param_set"       # SPS/PPS
KIND_SEI = "sei"
KIND_SLICE_HEADER = "slice_header"
KIND_MB = "mb"
KIND_OTHER = "other"


def _mb_first_bit(mb: Dict) -> Optional[int]:
    fields = mb.get("fields", [])
    if fields:
        b = fields[0].get("bit")
        return b
    res = mb.get("residuals", [])
    if res:
        return res[0].get("bit")
    return None


def _mb_last_bit(mb: Dict) -> Optional[int]:
    last = None
    for f in mb.get("fields", []):
        if f.get("bit") is not None:
            last = f["bit"]
    for r in mb.get("residuals", []):
        if r.get("bit") is not None:
            last = max(last or 0, r["bit"])
    return last


def build_frame_rawmap(project_id: str, decode_index: int, cfg=None) -> Dict[str, object]:
    """构造某解码序帧的原始数据分段。"""
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]

    parsed = syntax._ensure_parsed(project_id, cfg=cfg)  # noqa: SLF001
    frames = parsed["frames"]
    if decode_index < 0 or decode_index >= len(frames):
        raise IndexError("帧索引越界: %d" % decode_index)
    frame = frames[decode_index]

    # 判断熵编码模式(影响 MB 字节映射精度)
    entropy = _entropy_mode(frames)
    approx = (entropy == "cabac")

    stream_nals = nal_bytes.scan_nals(es_path)

    # 该帧的 NAL 在全局 NAL 序中的下标(trace 的 nal_index 即全局序)
    frame_nal_indices = [n["nal_index"] for n in frame.get("nals", [])]

    segments: List[Dict] = []
    byte_lo = None
    byte_hi = None

    for tnal in frame.get("nals", []):
        gi = tnal_gi = tnal.get("nal_index")
        if gi is None or gi >= len(stream_nals):
            continue
        snal = stream_nals[gi]
        if byte_lo is None:
            byte_lo = snal["sc_start"]
        byte_hi = snal["nal_end"]

        # 起始码
        segments.append({
            "kind": KIND_START_CODE, "nal_index": gi,
            "byte_start": snal["sc_start"], "byte_end": snal["nal_start"],
            "label": "起始码", "label_en": "start code",
        })
        # NAL 头(1字节)
        segments.append({
            "kind": KIND_NAL_HEADER, "nal_index": gi,
            "byte_start": snal["nal_start"], "byte_end": snal["payload_start"],
            "label": "NAL头 (type %d %s)" % (snal["nal_unit_type"], tnal.get("name_zh", "")),
            "label_en": "NAL header",
            "nal_unit_type": snal["nal_unit_type"],
        })

        payload_lo = snal["payload_start"]
        payload_hi = snal["nal_end"]
        ntype = snal["nal_unit_type"]

        if ntype in (1, 2, 5, 19, 20):
            # slice：拆 slice_header + 各宏块
            _emit_slice_segments(segments, frame, tnal, gi, payload_lo, payload_hi,
                                 decode_index, approx)
        elif ntype in (7, 8):
            segments.append({
                "kind": KIND_PARAM, "nal_index": gi,
                "byte_start": payload_lo, "byte_end": payload_hi,
                "label": tnal.get("name_zh", "参数集") + " 载荷",
                "label_en": tnal.get("name_en", "param set"),
            })
        elif ntype == 6:
            segments.append({
                "kind": KIND_SEI, "nal_index": gi,
                "byte_start": payload_lo, "byte_end": payload_hi,
                "label": "SEI 载荷", "label_en": "SEI payload",
            })
        else:
            segments.append({
                "kind": KIND_OTHER, "nal_index": gi,
                "byte_start": payload_lo, "byte_end": payload_hi,
                "label": tnal.get("name_zh", "其他"), "label_en": tnal.get("name_en", "other"),
            })

    return {
        "decode_index": decode_index,
        "poc": frame.get("poc"),
        "slice_type": frame.get("slice_type"),
        "entropy": entropy,
        "mb_mapping": "approx" if approx else "exact",
        "byte_start": byte_lo or 0,
        "byte_end": byte_hi or 0,
        "num_segments": len(segments),
        "segments": segments,
    }


def _emit_slice_segments(segments, frame, tnal, gi, payload_lo, payload_hi,
                         decode_index, approx):
    """把一个 slice NAL 的载荷拆成 slice_header + 各宏块字节区间。"""
    # 该帧属于此 slice 的宏块：trace 未逐 slice 分组，这里用整帧宏块
    # (单 slice/帧最常见；多 slice 时按 mb.slice 过滤)
    slice_no = None
    # 尝试识别本 NAL 对应的 slice 号：用宏块 slice 字段的集合，按出现顺序
    mbs_all = frame.get("macroblocks", [])
    # 关联：同一帧多个 slice NAL 时，用 nal 在帧内 slice 序号
    slice_nals = [n for n in frame.get("nals", []) if n.get("is_slice")]
    try:
        slice_ord = slice_nals.index(tnal)
    except ValueError:
        slice_ord = 0
    slice_ids = sorted(set(mb.get("slice", 0) for mb in mbs_all))
    target_slice = slice_ids[slice_ord] if slice_ord < len(slice_ids) else (slice_ids[0] if slice_ids else 0)
    mbs = [mb for mb in mbs_all if mb.get("slice", 0) == target_slice] or mbs_all

    # 估计 slice header 字节数：用 trace 的 SH 字段 bit 跨度
    sh_bits = _slice_header_bits(tnal)
    sh_bytes = max(1, (sh_bits + 7) // 8)
    data_lo = min(payload_lo + sh_bytes, payload_hi)

    segments.append({
        "kind": KIND_SLICE_HEADER, "nal_index": gi,
        "byte_start": payload_lo, "byte_end": data_lo,
        "label": "片头 (Slice Header)", "label_en": "slice header",
    })

    if not mbs or data_lo >= payload_hi:
        return

    # 各宏块相对 bit → 比例映射到 [data_lo, payload_hi]
    firsts = [(_mb_first_bit(mb), mb) for mb in mbs]
    firsts = [(b, mb) for (b, mb) in firsts if b is not None]
    if not firsts:
        return
    base = firsts[0][0]
    span = (firsts[-1][0] - base)
    last_end = _mb_last_bit(firsts[-1][1])
    total_bits = (last_end - base) if (last_end and last_end > base) else max(span, 1)
    usable = payload_hi - data_lo

    for idx, (fb, mb) in enumerate(firsts):
        rel0 = fb - base
        rel1 = (firsts[idx + 1][0] - base) if idx + 1 < len(firsts) else total_bits
        bs = data_lo + int(round(rel0 / total_bits * usable))
        be = data_lo + int(round(rel1 / total_bits * usable))
        bs = max(data_lo, min(payload_hi, bs))
        be = max(bs, min(payload_hi, be))
        if idx == len(firsts) - 1:
            be = payload_hi
        segments.append({
            "kind": KIND_MB, "nal_index": gi,
            "mb_index": mb.get("mb_index"),
            "byte_start": bs, "byte_end": be,
            "label": "宏块 %d" % mb.get("mb_index"),
            "label_en": "MB %d" % mb.get("mb_index"),
            "approx": approx,
        })


def _slice_header_bits(tnal: Dict) -> int:
    """由 trace 的 SH 字段估算片头 bit 数(跨度)。"""
    bits = [f.get("bit") for f in tnal.get("fields", []) if f.get("bit") is not None]
    if not bits:
        return 8
    return (max(bits) - min(bits)) + 8  # +8 容纳末字段与对齐


def _entropy_mode(frames) -> str:
    """从 PPS 的 entropy_coding_mode_flag 判 CAVLC/CABAC。"""
    for fr in frames:
        for n in fr.get("nals", []):
            if n.get("nal_unit_type") == 8:
                for f in n.get("fields", []):
                    if (f.get("name_en") or f.get("name")) == "entropy_coding_mode_flag":
                        return "cabac" if f.get("value") == 1 else "cavlc"
    return "unknown"


def read_bytes_range(project_id: str, start: int, end: int, cfg=None) -> bytes:
    """读取裸流指定字节区间(供 hex 视图)。"""
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]
    with open(str(es_path), "rb") as fh:
        fh.seek(max(0, start))
        return fh.read(max(0, end - start))
