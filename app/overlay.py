"""每帧叠加数据：分割(子块配色) + QP + MV + 帧内方向 + 帧间参考关系。

供 P3 前端分层渲染。坐标单位为像素(整帧绝对坐标)。

数据来源：全部来自 JM trace 解析结果(解码序)，经宏块几何还原(mb_partition)
与 QP/MV 推导组合。帧间参考关系来自 slice header 的参考列表推导 + ref_idx。
"""
from typing import Dict, List, Optional

from . import mb_partition, syntax

MB_SIZE = 16


def _field_values(mb: Dict, name: str) -> List:
    out = []
    for f in mb.get("fields", []):
        if (f.get("name_en") or f.get("name")) == name:
            out.append(f.get("value"))
    return out


def _first(mb: Dict, name: str, default=None):
    v = _field_values(mb, name)
    return v[0] if v else default


def _slice_qp_of(frame: Dict, pic_init_qp: int) -> int:
    """SliceQP = 26 + pic_init_qp_minus26 + slice_qp_delta (H.264 7.4.3)。

    注意：PPS(含 pic_init_qp_minus26)通常只在 IDR 帧携带，之后的 P/B 帧
    不重复传，故 pic_init_qp 必须由**流级 PPS 状态**提供，不能只在当前帧的
    NAL 里找(否则 P/B 帧退化为 0，导致 SliceQP 系统性偏低)。
    """
    slice_delta = None
    for n in frame.get("nals", []):
        for f in n.get("fields", []):
            nm = f.get("name_en") or f.get("name")
            if nm == "slice_qp_delta" and slice_delta is None:
                slice_delta = f.get("value")
    return 26 + (pic_init_qp or 0) + (slice_delta or 0)


def _active_pic_init_qp(project_id: str, decode_index: int, cfg=None) -> int:
    """取到解码序 decode_index 为止、最近一次 PPS 的 pic_init_qp_minus26。

    多 PPS 场景下应按 slice 引用的 pic_parameter_set_id 精确匹配；此处采用
    "最近出现的 PPS" 近似(绝大多数码流单 PPS 或 PPS 值相同)。
    """
    parsed = syntax._ensure_parsed(project_id, cfg=cfg)  # noqa: SLF001
    frames = parsed["frames"]
    last = 0
    for i in range(0, min(decode_index, len(frames) - 1) + 1):
        for n in frames[i].get("nals", []):
            if n.get("nal_unit_type") == 8:  # PPS
                for f in n.get("fields", []):
                    if (f.get("name_en") or f.get("name")) == "pic_init_qp_minus26":
                        last = f.get("value") or 0
    return last


def _ref_lists(frame: Dict) -> Dict[str, List[int]]:
    """从 slice header 推导参考帧 POC 列表(近似)。

    JM trace 不直接给完整 RefPicList，这里用 num_ref_idx + 已解码帧的
    POC 邻近关系做近似(P: 之前最近的参考帧；B: 前后)。真实列表在 P5/后续增强。
    仅用于"参考关系"可视化的定性展示。
    """
    return {"L0": [], "L1": []}


def build_frame_overlay(project_id: str, decode_index: int, cfg=None) -> Dict[str, object]:
    """构造某解码序帧的叠加数据。"""
    meta = syntax.project.get_project(project_id)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    mbw = (width + 15) // 16

    fs = syntax.frame_syntax(project_id, decode_index, cfg=cfg)
    pic_init_qp = _active_pic_init_qp(project_id, decode_index, cfg=cfg)
    slice_qp = _slice_qp_of(fs, pic_init_qp)

    blocks: List[Dict] = []
    cur_qp = slice_qp
    qp_min, qp_max = 52, 0

    for mb in fs["macroblocks"]:
        mb_idx = mb["mb_index"]
        mbx = (mb_idx % mbw) * MB_SIZE
        mby = (mb_idx // mbw) * MB_SIZE
        slice_kind = mb.get("slice_kind", fs["slice_type"])

        # QP 累加：H.264 mb_qp_delta 按模 52 环绕(spec 7.4.5, 8位深 QpBdOffset=0)
        #   QPY = (QPY_prev + mb_qp_delta + 52) % 52
        # skip 宏块无 mb_qp_delta，视为 0(QP 不变)。
        dq = _first(mb, "mb_qp_delta", 0) or 0
        cur_qp = (cur_qp + dq + 52) % 52
        qp_min = min(qp_min, cur_qp)
        qp_max = max(qp_max, cur_qp)

        recon = mb_partition.reconstruct(mb, slice_kind)

        # 运动矢量：从 trace 的 mvd 累积(近似为 mvd 本身，真实 MV 需预测重建)
        mvds = []
        mvd_x = _field_values(mb, "mvd0_l") + _field_values(mb, "mvd_l0") \
            + _field_values(mb, "mvd0_l0")
        mvd_y = _field_values(mb, "mvd1_l") + _field_values(mb, "mvd1_l0")
        ref_idx = _field_values(mb, "ref_idx_l0") + _field_values(mb, "ref_idx_l")

        parts_out = []
        for i, p in enumerate(recon["partitions"]):
            part = {
                "x": mbx + p["x"], "y": mby + p["y"],
                "w": p["w"], "h": p["h"],
                "kind": p["kind"], "pred": p["pred"],
                "intra_mode": p.get("intra_mode"),
            }
            # 给 inter 分割配一个近似 MV(取按序对应的 mvd 分量)
            if p["pred"] in ("L0", "L1", "Bi", "Direct") and mvd_x:
                dx = mvd_x[i] if i < len(mvd_x) else (mvd_x[0] if mvd_x else 0)
                dy = mvd_y[i] if i < len(mvd_y) else (mvd_y[0] if mvd_y else 0)
                part["mvd"] = [dx or 0, dy or 0]
                part["ref_idx"] = ref_idx[i] if i < len(ref_idx) else (ref_idx[0] if ref_idx else 0)
            parts_out.append(part)

        blocks.append({
            "mb_index": mb_idx,
            "x": mbx, "y": mby, "w": MB_SIZE, "h": MB_SIZE,
            "mb_kind": recon["mb_kind"],
            "qp": cur_qp,
            "partitions": parts_out,
        })

    return {
        "decode_index": decode_index,
        "poc": fs["poc"],
        "slice_type": fs["slice_type"],
        "width": width, "height": height,
        "mb_width": mbw, "mb_height": (height + 15) // 16,
        "slice_qp": slice_qp,
        "qp_range": [qp_min if blocks else slice_qp, qp_max if blocks else slice_qp],
        "num_blocks": len(blocks),
        "blocks": blocks,
    }


def build_reference_graph(project_id: str, cfg=None) -> Dict[str, object]:
    """帧间参考关系图(所有帧)：每帧 → 其参考帧 POC。

    近似推导：非 IDR 帧的参考取解码序在其之前、且为参考帧的最近若干帧。
    真实 RefPicList 的精确重建留待后续增强；此处满足"参考关系可视化"需求。
    """
    parsed = syntax._ensure_parsed(project_id, cfg=cfg)  # noqa: SLF001
    frames = parsed["frames"]

    nodes = []
    for fr in frames:
        is_idr = any(n.get("nal_unit_type") == 5 for n in fr.get("nals", []))
        nodes.append({
            "decode_index": fr["index"],
            "poc": fr["poc"],
            "slice_type": fr["slice_type"],
            "frame_num": fr["frame_num"],
            "is_idr": is_idr,
        })

    # 近似参考边：P 参考之前最近参考帧；B 参考前后各一
    edges = []
    for idx, fr in enumerate(frames):
        st = fr["slice_type"]
        if st == "I" or fr["poc"] is None:
            continue
        poc = fr["poc"]
        # 候选：同段(GOP)内、已解码(解码序在前)的帧
        seg_start = 0
        for j in range(idx, -1, -1):
            if any(n.get("nal_unit_type") == 5 for n in frames[j].get("nals", [])):
                seg_start = j
                break
        prior = [frames[j] for j in range(seg_start, idx)
                 if frames[j]["poc"] is not None]
        if st == "P":
            # 前向：POC 最接近且小于当前的
            cands = sorted([p for p in prior if p["poc"] < poc],
                           key=lambda p: poc - p["poc"])
            if cands:
                edges.append({"from": fr["index"], "to": cands[0]["index"], "list": "L0"})
        elif st == "B":
            fwd = sorted([p for p in prior if p["poc"] < poc], key=lambda p: poc - p["poc"])
            bwd = sorted([p for p in prior if p["poc"] > poc], key=lambda p: p["poc"] - poc)
            if fwd:
                edges.append({"from": fr["index"], "to": fwd[0]["index"], "list": "L0"})
            if bwd:
                edges.append({"from": fr["index"], "to": bwd[0]["index"], "list": "L1"})

    return {"nodes": nodes, "edges": edges}
