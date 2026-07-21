"""HEVC 每帧叠加数据：CU 四叉树 / PU 分割 / QP / MV / 参考关系。

数据来自 cu_dump.csv(逐 CU 精确几何+QP+预测+MV) 与 HM stdout(参考列表)。
输出 schema 与 H.264 overlay 对齐(blocks[].partitions[].kind/pred/mvd)，前端统一渲染。

CU 块类型(kind)配色，与 H.264 命名风格一致：
  intra_cu / inter_cu / skip_cu ；PU 分割用 partition 表达。
更细的按 CU 尺寸区分：hevc_cu_<size>；预测方向 pred: L0/L1/Bi/Intra。
"""
from pathlib import Path
from typing import Dict, List

from . import hm_cu, hm_decoder, hevc_syntax, project

PART_SIZE = hevc_syntax.PART_SIZE


def _pu_partitions(cu: Dict) -> List[Dict]:
    """按 partSize 把 CU 拆成 PU 矩形(相对 CU 左上角)。"""
    s = cu["size"]
    ps = cu["partSize"]
    h = s
    w = s
    if ps == 0:      # 2Nx2N
        rects = [(0, 0, w, h)]
    elif ps == 1:    # 2NxN
        rects = [(0, 0, w, h // 2), (0, h // 2, w, h // 2)]
    elif ps == 2:    # Nx2N
        rects = [(0, 0, w // 2, h), (w // 2, 0, w // 2, h)]
    elif ps == 3:    # NxN
        rects = [(0, 0, w // 2, h // 2), (w // 2, 0, w // 2, h // 2),
                 (0, h // 2, w // 2, h // 2), (w // 2, h // 2, w // 2, h // 2)]
    elif ps == 4:    # 2NxnU
        rects = [(0, 0, w, h // 4), (0, h // 4, w, h * 3 // 4)]
    elif ps == 5:    # 2NxnD
        rects = [(0, 0, w, h * 3 // 4), (0, h * 3 // 4, w, h // 4)]
    elif ps == 6:    # nLx2N
        rects = [(0, 0, w // 4, h), (w // 4, 0, w * 3 // 4, h)]
    elif ps == 7:    # nRx2N
        rects = [(0, 0, w * 3 // 4, h), (w * 3 // 4, 0, w // 4, h)]
    else:
        rects = [(0, 0, w, h)]
    return rects


def _pred_label(cu: Dict) -> str:
    if cu["predMode"] == 1:
        return "Intra"
    d = cu["interDir"]
    return {1: "L0", 2: "L1", 3: "Bi"}.get(d, "L0")


def _cu_kind(cu: Dict) -> str:
    if cu["predMode"] == 1:
        return "intra_cu"
    if cu["predMode"] == 0:
        return "inter_cu"
    return "skip_cu"


def build_frame_overlay(project_id: str, decode_index: int, cfg=None) -> Dict[str, object]:
    """构造某解码序 HEVC 帧的叠加数据。"""
    meta = project.get_project(project_id)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)

    data = hevc_syntax._ensure(project_id, cfg=cfg)  # noqa: SLF001
    frames = data["frames"]
    if decode_index < 0 or decode_index >= len(frames):
        raise IndexError("帧索引越界: %d" % decode_index)
    fr = frames[decode_index]
    poc = fr["poc"]

    cus = hm_cu.cus_for_decode_index(
        project.Project(project_id).dir / hm_decoder.CU_DUMP_NAME, decode_index)

    blocks: List[Dict] = []
    qp_min, qp_max = 52, 0
    ctu_size = max((c["size"] << c["depth"]) for c in cus) if cus else 64

    for cu in cus:
        qp = cu["qp"]
        qp_min = min(qp_min, qp)
        qp_max = max(qp_max, qp)
        kind = _cu_kind(cu)
        pred = _pred_label(cu)
        parts = []
        for (px, py, pw, ph) in _pu_partitions(cu):
            part = {
                "x": cu["x"] + px, "y": cu["y"] + py, "w": pw, "h": ph,
                "kind": kind, "pred": pred,
                "intra_mode": cu["intraDirY"] if cu["predMode"] == 1 else None,
            }
            if cu["predMode"] == 0:
                if cu["interDir"] & 1:
                    part["mvd"] = [cu["mvL0x"], cu["mvL0y"]]
                    part["ref_idx"] = cu["refL0"]
                elif cu["interDir"] & 2:
                    part["mvd"] = [cu["mvL1x"], cu["mvL1y"]]
                    part["ref_idx"] = cu["refL1"]
            parts.append(part)
        blocks.append({
            "mb_index": None,
            "cu_x": cu["x"], "cu_y": cu["y"],
            "x": cu["x"], "y": cu["y"], "w": cu["size"], "h": cu["size"],
            "mb_kind": kind, "qp": qp,
            "depth": cu["depth"], "part_size": PART_SIZE.get(cu["partSize"], "?"),
            "partitions": parts,
        })

    # TU/RQT 网格 + SAO 分类(额外叠加层)
    proj_dir = project.Project(project_id).dir
    tus = hm_cu.rows_for_decode_index(proj_dir / hm_decoder.TU_DUMP_NAME, decode_index)
    tu_rects = [{"x": t["x"], "y": t["y"], "w": t["size"], "h": t["size"]} for t in tus]
    sao_rows = hm_cu.rows_for_decode_index(proj_dir / hm_decoder.SAO_DUMP_NAME, decode_index)
    sao_ctus = _build_sao(sao_rows)

    return {
        "decode_index": decode_index, "poc": poc,
        "slice_type": fr["slice_type"],
        "width": width, "height": height,
        "mb_width": (width + ctu_size - 1) // ctu_size,
        "mb_height": (height + ctu_size - 1) // ctu_size,
        "ctu_size": ctu_size,
        "unit": "CU",
        "slice_qp": fr["qp"],
        "qp_range": [qp_min if blocks else fr["qp"], qp_max if blocks else fr["qp"]],
        "num_blocks": len(blocks),
        "blocks": blocks,
        "tu": tu_rects,
        "sao": sao_ctus,
    }


# SAO 类型: modeIdc 0=off 1=new 2=merge; new 的 typeIdc: 0/1/2/3=EO(0/90/135/45°), 4=BO
SAO_TYPE_LABEL = {
    "off": "关闭", "eo0": "边缘 0°", "eo90": "边缘 90°",
    "eo135": "边缘 135°", "eo45": "边缘 45°", "bo": "带偏移 BO", "merge": "合并",
}


def _sao_kind(mode: int, type_idc: int) -> str:
    if mode == 0:
        return "off"
    if mode == 2:
        return "merge"
    # mode==1 (new)
    return {0: "eo0", 1: "eo90", 2: "eo135", 3: "eo45", 4: "bo"}.get(type_idc, "off")


def _build_sao(rows: List[Dict]) -> List[Dict]:
    """把逐分量 SAO 行按 CTU 聚合，取亮度(comp=0)作为主类型。"""
    by_ctu: Dict[int, Dict] = {}
    for r in rows:
        ctu = r["ctu"]
        d = by_ctu.setdefault(ctu, {
            "ctu": ctu, "x": r["x"], "y": r["y"], "size": r["size"],
            "luma": None, "cb": None, "cr": None,
        })
        comp = {0: "luma", 1: "cb", 2: "cr"}.get(r["comp"])
        if comp:
            d[comp] = {"mode": r["mode"], "typeIdc": r["typeIdc"],
                       "kind": _sao_kind(r["mode"], r["typeIdc"])}
    out = []
    for ctu in sorted(by_ctu):
        d = by_ctu[ctu]
        d["kind"] = d["luma"]["kind"] if d["luma"] else "off"
        out.append(d)
    return out


def build_reference_graph(project_id: str, cfg=None) -> Dict[str, object]:
    """HEVC 帧间参考关系图：直接用 HM 给的精确 L0/L1 参考列表(POC)。"""
    data = hevc_syntax._ensure(project_id, cfg=cfg)  # noqa: SLF001
    frames = data["frames"]
    poc_to_idx = {f["poc"]: f["index"] for f in frames}
    nodes, edges = [], []
    for fr in frames:
        nodes.append({
            "decode_index": fr["index"], "poc": fr["poc"],
            "slice_type": fr["slice_type"], "frame_num": None,
            "is_idr": fr["slice_type"] == "I" and not fr["ref_l0"] and not fr["ref_l1"],
        })
        for rp in fr["ref_l0"]:
            if rp in poc_to_idx:
                edges.append({"from": fr["index"], "to": poc_to_idx[rp], "list": "L0"})
        for rp in fr["ref_l1"]:
            if rp in poc_to_idx:
                edges.append({"from": fr["index"], "to": poc_to_idx[rp], "list": "L1"})
    return {"nodes": nodes, "edges": edges}
