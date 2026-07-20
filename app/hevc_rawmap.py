"""HEVC 原始数据分段：起始码 / NAL 头(2字节) / 各 NAL 载荷。

与 H.264 rawmap 的区别：
- HEVC NAL 头是 **2 字节**，nal_unit_type = (byte0 >> 1) & 0x3F。
- HEVC 用 CABAC，且 HM 的 cu_dump 无 bit 位置 → **无法把 CU 精确映射到字节**。
  因此 HEVC 原始数据页只做到 **NAL/头部级字节精确分段**(起始码/NAL头/参数集/片/SEI)，
  不提供 CU 级字节区间(诚实标注)。

CU↔预览的双向联动在 HEVC 下不可用(无字节映射)，前端会相应说明。
"""
from pathlib import Path
from typing import Dict, List

from . import project

# HEVC nal_unit_type(6bit) → (英文, 中文)
HEVC_NAL_TYPES = {
    0: ("TRAIL_N", "非参考尾随片"), 1: ("TRAIL_R", "参考尾随片"),
    2: ("TSA_N", "时域子层接入(非参考)"), 3: ("TSA_R", "时域子层接入(参考)"),
    4: ("STSA_N", "步进时域接入(非参考)"), 5: ("STSA_R", "步进时域接入(参考)"),
    6: ("RADL_N", "可解码前导(非参考)"), 7: ("RADL_R", "可解码前导(参考)"),
    8: ("RASL_N", "可跳过前导(非参考)"), 9: ("RASL_R", "可跳过前导(参考)"),
    16: ("BLA_W_LP", "断链接入(带前导)"), 17: ("BLA_W_RADL", "断链接入(带RADL)"),
    18: ("BLA_N_LP", "断链接入(无前导)"),
    19: ("IDR_W_RADL", "IDR(带RADL)"), 20: ("IDR_N_LP", "IDR(无前导)"),
    21: ("CRA", "纯随机接入"),
    32: ("VPS", "视频参数集"), 33: ("SPS", "序列参数集"), 34: ("PPS", "图像参数集"),
    35: ("AUD", "访问单元分隔符"), 36: ("EOS", "序列结束"), 37: ("EOB", "流结束"),
    38: ("FD", "填充数据"), 39: ("PREFIX_SEI", "前缀SEI"), 40: ("SUFFIX_SEI", "后缀SEI"),
}

SLICE_TYPES = set(range(0, 22))  # 0..21 都是 VCL 片
PARAM_TYPES = {32, 33, 34}
SEI_TYPES = {39, 40}


def scan_hevc_nals(es_path: Path) -> List[Dict]:
    """扫描 Annex-B，解析 HEVC 2 字节 NAL 头。"""
    data = es_path.read_bytes()
    n = len(data)
    starts = []
    i = 0
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                starts.append((i, 3)); i += 3; continue
            if i + 4 <= n and data[i + 2] == 0 and data[i + 3] == 1:
                starts.append((i, 4)); i += 4; continue
        i += 1

    nals = []
    for k, (sc_pos, sc_len) in enumerate(starts):
        nal_start = sc_pos + sc_len
        nal_end = starts[k + 1][0] if k + 1 < len(starts) else n
        if nal_start + 2 > nal_end:
            continue
        b0 = data[nal_start]
        ntype = (b0 >> 1) & 0x3F
        en, zh = HEVC_NAL_TYPES.get(ntype, ("type %d" % ntype, "类型%d" % ntype))
        nals.append({
            "index": len(nals),
            "sc_start": sc_pos, "sc_len": sc_len,
            "nal_start": nal_start, "header_len": 2,
            "payload_start": nal_start + 2, "nal_end": nal_end,
            "nal_unit_type": ntype, "name_en": en, "name_zh": zh,
            "total_bytes": nal_end - sc_pos,
        })
    return nals


def build_frame_rawmap(project_id: str, decode_index: int, cfg=None) -> Dict[str, object]:
    """HEVC 帧原始数据分段。

    HEVC 无 CU 级字节映射；这里按**整个流的 NAL 序**给出分段。因 HM trace 不含
    每 NAL 归属的帧号，且 HEVC 常一帧一片，采用近似：第 decode_index 个 VCL 片
    NAL 作为该帧，其前的非 VCL(VPS/SPS/PPS/SEI)归入首帧。
    """
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]
    nals = scan_hevc_nals(es_path)

    # 找出 VCL 片 NAL 的序号
    vcl_indices = [i for i, na in enumerate(nals) if na["nal_unit_type"] in SLICE_TYPES]
    if decode_index < 0 or decode_index >= len(vcl_indices):
        raise IndexError("帧索引越界: %d (共 %d 帧)" % (decode_index, len(vcl_indices)))

    target_vcl = vcl_indices[decode_index]
    # 该帧涉及的 NAL：第 decode_index 个 VCL；若为首帧，把它之前的非 VCL 一并纳入
    if decode_index == 0:
        involved = list(range(0, target_vcl + 1))
    else:
        prev_vcl = vcl_indices[decode_index - 1]
        involved = list(range(prev_vcl + 1, target_vcl + 1))

    segments: List[Dict] = []
    byte_lo = byte_hi = None
    for gi in involved:
        na = nals[gi]
        if byte_lo is None:
            byte_lo = na["sc_start"]
        byte_hi = na["nal_end"]
        segments.append({
            "kind": "start_code", "nal_index": gi,
            "byte_start": na["sc_start"], "byte_end": na["nal_start"],
            "label": "起始码", "label_en": "start code",
        })
        segments.append({
            "kind": "nal_header", "nal_index": gi,
            "byte_start": na["nal_start"], "byte_end": na["payload_start"],
            "label": "NAL头 (type %d %s)" % (na["nal_unit_type"], na["name_zh"]),
            "label_en": "NAL header", "nal_unit_type": na["nal_unit_type"],
        })
        ntype = na["nal_unit_type"]
        if ntype in PARAM_TYPES:
            kind, label = "param_set", na["name_zh"] + " 载荷"
        elif ntype in SEI_TYPES:
            kind, label = "sei", "SEI 载荷"
        elif ntype in SLICE_TYPES:
            kind, label = "slice_payload", na["name_zh"] + " 片数据"
        else:
            kind, label = "other", na["name_zh"]
        segments.append({
            "kind": kind, "nal_index": gi,
            "byte_start": na["payload_start"], "byte_end": na["nal_end"],
            "label": label, "label_en": na["name_en"],
        })

    return {
        "decode_index": decode_index, "poc": None,
        "slice_type": None, "entropy": "cabac",
        "mb_mapping": "none",  # HEVC 不提供 CU 级字节映射
        "cu_level": False,
        "byte_start": byte_lo or 0, "byte_end": byte_hi or 0,
        "num_segments": len(segments), "segments": segments,
    }


def read_bytes_range(project_id: str, start: int, end: int, cfg=None) -> bytes:
    meta = project.get_project(project_id)
    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]
    with open(str(es_path), "rb") as fh:
        fh.seek(max(0, start))
        return fh.read(max(0, end - start))
