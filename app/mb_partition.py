"""宏块分割几何还原：由 trace 的 mb_type / sub_mb_type / transform_size_8x8_flag /
intra 模式，经 H.264 标准表(Table 7-11~7-18) 还原每个(子)块的几何与类别。

输出用于 P3 的"各类细分子块分类配色"叠加：每个块含
  { x, y, w, h, kind, pred, ref_list, intra_mode }
坐标单位为像素，相对宏块左上角(0..16)。

kind 取值(决定配色)：
  intra_4x4 / intra_8x8 / intra_16x16 / intra_pcm
  inter_16x16 / inter_16x8 / inter_8x16 / inter_8x8 /
  inter_8x4 / inter_4x8 / inter_4x4
  skip / direct / unknown
pred 取值(帧间预测方向，用于 MV 上色)：L0 / L1 / Bi / Direct / Intra / None
"""
from typing import Dict, List, Optional

# ---- 4x4 亮度块扫描序 → (x,y) 以 4 像素为单位 (H.264 Fig 6-10) ----
LUMA4x4_SCAN = [
    (0, 0), (1, 0), (0, 1), (1, 1),
    (2, 0), (3, 0), (2, 1), (3, 1),
    (0, 2), (1, 2), (0, 3), (1, 3),
    (2, 2), (3, 2), (2, 3), (3, 3),
]
# 8x8 块(用于 I_8x8)光栅序 → (x,y) 以 8 像素为单位
BLK8x8_POS = [(0, 0), (8, 0), (0, 8), (8, 8)]

# ---- P 宏块类型(Table 7-13) → 分割几何 + 预测 ----
# 每项: (kind, [(x,y,w,h,pred)...])  pred 相对分割
P_MB = {
    0: ("inter_16x16", [(0, 0, 16, 16, "L0")]),
    1: ("inter_16x8", [(0, 0, 16, 8, "L0"), (0, 8, 16, 8, "L0")]),
    2: ("inter_8x16", [(0, 0, 8, 16, "L0"), (8, 0, 8, 16, "L0")]),
    3: ("inter_8x8", None),   # 需 sub_mb_type
    4: ("inter_8x8", None),   # P_8x8ref0
}

# ---- B 宏块类型(Table 7-14) → (kind, 几何, 各分割 pred) ----
# 用几何形状 + 每分割方向表达
def _bpart(shape, preds):
    if shape == "16x16":
        return [(0, 0, 16, 16, preds[0])]
    if shape == "16x8":
        return [(0, 0, 16, 8, preds[0]), (0, 8, 16, 8, preds[1])]
    if shape == "8x16":
        return [(0, 0, 8, 16, preds[0]), (8, 0, 8, 16, preds[1])]
    return []

B_MB = {
    0: ("direct", "16x16", ["Direct"]),
    1: ("inter_16x16", "16x16", ["L0"]),
    2: ("inter_16x16", "16x16", ["L1"]),
    3: ("inter_16x16", "16x16", ["Bi"]),
    4: ("inter_16x8", "16x8", ["L0", "L0"]),
    5: ("inter_8x16", "8x16", ["L0", "L0"]),
    6: ("inter_16x8", "16x8", ["L1", "L1"]),
    7: ("inter_8x16", "8x16", ["L1", "L1"]),
    8: ("inter_16x8", "16x8", ["L0", "L1"]),
    9: ("inter_8x16", "8x16", ["L0", "L1"]),
    10: ("inter_16x8", "16x8", ["L1", "L0"]),
    11: ("inter_8x16", "8x16", ["L1", "L0"]),
    12: ("inter_16x8", "16x8", ["L0", "Bi"]),
    13: ("inter_8x16", "8x16", ["L0", "Bi"]),
    14: ("inter_16x8", "16x8", ["L1", "Bi"]),
    15: ("inter_8x16", "8x16", ["L1", "Bi"]),
    16: ("inter_16x8", "16x8", ["Bi", "L0"]),
    17: ("inter_8x16", "8x16", ["Bi", "L0"]),
    18: ("inter_16x8", "16x8", ["Bi", "L1"]),
    19: ("inter_8x16", "8x16", ["Bi", "L1"]),
    20: ("inter_16x8", "16x8", ["Bi", "Bi"]),
    21: ("inter_8x16", "8x16", ["Bi", "Bi"]),
    22: ("inter_8x8", None, None),   # B_8x8, 需 sub_mb_type
}

# ---- P 子宏块类型(Table 7-17): (kind, 8x8内几何) ----
P_SUB = {
    0: ("inter_8x8", [(0, 0, 8, 8)]),
    1: ("inter_8x4", [(0, 0, 8, 4), (0, 4, 8, 4)]),
    2: ("inter_4x8", [(0, 0, 4, 8), (4, 0, 4, 8)]),
    3: ("inter_4x4", [(0, 0, 4, 4), (4, 0, 4, 4), (0, 4, 4, 4), (4, 4, 4, 4)]),
}

# ---- B 子宏块类型(Table 7-18): (kind, 几何, pred) ----
B_SUB = {
    0: ("direct", [(0, 0, 8, 8)], "Direct"),
    1: ("inter_8x8", [(0, 0, 8, 8)], "L0"),
    2: ("inter_8x8", [(0, 0, 8, 8)], "L1"),
    3: ("inter_8x8", [(0, 0, 8, 8)], "Bi"),
    4: ("inter_8x4", [(0, 0, 8, 4), (0, 4, 8, 4)], "L0"),
    5: ("inter_4x8", [(0, 0, 4, 8), (4, 0, 4, 8)], "L0"),
    6: ("inter_8x4", [(0, 0, 8, 4), (0, 4, 8, 4)], "L1"),
    7: ("inter_4x8", [(0, 0, 4, 8), (4, 0, 4, 8)], "L1"),
    8: ("inter_8x4", [(0, 0, 8, 4), (0, 4, 8, 4)], "Bi"),
    9: ("inter_4x8", [(0, 0, 4, 8), (4, 0, 4, 8)], "Bi"),
    10: ("inter_4x4", [(0, 0, 4, 4), (4, 0, 4, 4), (0, 4, 4, 4), (4, 4, 4, 4)], "L0"),
    11: ("inter_4x4", [(0, 0, 4, 4), (4, 0, 4, 4), (0, 4, 4, 4), (4, 4, 4, 4)], "L1"),
    12: ("inter_4x4", [(0, 0, 4, 4), (4, 0, 4, 4), (0, 4, 4, 4), (4, 4, 4, 4)], "Bi"),
}

# I 宏块 mb_type: 0=I_NxN(4x4或8x8), 1..24=I_16x16, 25=I_PCM
I_PCM_TYPE = 25


def _fields_map(mb: Dict):
    """把 mb['fields'] 收集成 name->[values] 便于按出现顺序取。

    兼容两种字段结构：原始解析(name) 与 装饰后(name_en)。
    """
    out = {}
    for f in mb.get("fields", []):
        name = f.get("name") or f.get("name_en")
        if name is None:
            continue
        out.setdefault(name, []).append(f.get("value"))
    return out


def _intra_modes(fm) -> List[int]:
    return fm.get("intra4x4_pred_mode", []) or fm.get("intra8x8_pred_mode", [])


def _get1(fm, name, default=None):
    v = fm.get(name)
    return v[0] if v else default


def reconstruct(mb: Dict, slice_kind: str) -> Dict[str, object]:
    """还原一个宏块的分割。返回 {mb_kind, pred, partitions:[...]}。

    slice_kind: 'I' / 'P' / 'B'(来自帧片类型，用于解释 mb_type 数值)。
    """
    fm = _fields_map(mb)
    mb_type = _get1(fm, "mb_type")
    t8x8 = _get1(fm, "transform_size_8x8_flag", 0)

    # skip 判定：宏块无 mb_type 字段(仅 mb_skip_flag/end_of_slice_flag)。
    # 注意：JM trace 里 mb_skip_flag 的数值不能直接当作"是否跳过"——
    # 真正跳过的宏块根本不解析 mb_type，故以 mb_type 缺失为准。
    if mb_type is None:
        kind = "skip"
        return {
            "mb_kind": kind, "pred": "Direct" if slice_kind == "B" else "L0",
            "partitions": [{"x": 0, "y": 0, "w": 16, "h": 16,
                            "kind": "skip", "pred": "Skip", "intra_mode": None}],
        }

    if slice_kind == "I":
        return _intra_mb(mb_type, t8x8, fm)
    if slice_kind == "P":
        return _inter_mb_p(mb_type, t8x8, fm)
    if slice_kind == "B":
        return _inter_mb_b(mb_type, t8x8, fm)
    # 未知片类型：按 I 处理
    return _intra_mb(mb_type, t8x8, fm)


def _intra_mb(mb_type: int, t8x8: int, fm) -> Dict[str, object]:
    parts: List[Dict] = []
    if mb_type == I_PCM_TYPE:
        return {"mb_kind": "intra_pcm", "pred": "Intra",
                "partitions": [{"x": 0, "y": 0, "w": 16, "h": 16,
                                "kind": "intra_pcm", "pred": "Intra", "intra_mode": None}]}
    if mb_type == 0:
        # I_NxN: 4x4 或 8x8
        modes = _intra_modes(fm)
        if t8x8 == 1:
            for i, (bx, by) in enumerate(BLK8x8_POS):
                parts.append({"x": bx, "y": by, "w": 8, "h": 8, "kind": "intra_8x8",
                              "pred": "Intra",
                              "intra_mode": modes[i] if i < len(modes) else None})
            return {"mb_kind": "intra_8x8", "pred": "Intra", "partitions": parts}
        for i, (cx, cy) in enumerate(LUMA4x4_SCAN):
            parts.append({"x": cx * 4, "y": cy * 4, "w": 4, "h": 4, "kind": "intra_4x4",
                          "pred": "Intra",
                          "intra_mode": modes[i] if i < len(modes) else None})
        return {"mb_kind": "intra_4x4", "pred": "Intra", "partitions": parts}
    # I_16x16 (1..24)：整块，预测方向编码在 mb_type 里(此处仅几何)
    i16_mode = (mb_type - 1) % 4  # 0=V,1=H,2=DC,3=Plane
    return {"mb_kind": "intra_16x16", "pred": "Intra",
            "partitions": [{"x": 0, "y": 0, "w": 16, "h": 16, "kind": "intra_16x16",
                            "pred": "Intra", "intra_mode": i16_mode}]}


def _p_intra_offset(mb_type: int):
    """P 片中 mb_type>=5 为帧内，偏移 5。"""
    return mb_type - 5


def _b_intra_offset(mb_type: int):
    """B 片中 mb_type>=23 为帧内，偏移 23。"""
    return mb_type - 23


def _inter_mb_p(mb_type: int, t8x8: int, fm) -> Dict[str, object]:
    if mb_type >= 5:
        return _intra_mb(_p_intra_offset(mb_type), t8x8, fm)
    kind, geom = P_MB.get(mb_type, ("unknown", [(0, 0, 16, 16, "L0")]))
    if geom is not None:
        parts = [{"x": x, "y": y, "w": w, "h": h, "kind": kind, "pred": pr,
                  "intra_mode": None} for (x, y, w, h, pr) in geom]
        return {"mb_kind": kind, "pred": "L0", "partitions": parts}
    # P_8x8：4 个 8x8，各按 sub_mb_type 细分
    subs = fm.get("sub_mb_type", [])
    parts = []
    for q, (qx, qy) in enumerate(BLK8x8_POS):
        st = subs[q] if q < len(subs) else 0
        skind, sgeom = P_SUB.get(st, ("inter_8x8", [(0, 0, 8, 8)]))
        for (sx, sy, sw, sh) in sgeom:
            parts.append({"x": qx + sx, "y": qy + sy, "w": sw, "h": sh,
                          "kind": skind, "pred": "L0", "intra_mode": None})
    return {"mb_kind": "inter_8x8", "pred": "L0", "partitions": parts}


def _inter_mb_b(mb_type: int, t8x8: int, fm) -> Dict[str, object]:
    if mb_type >= 23:
        return _intra_mb(_b_intra_offset(mb_type), t8x8, fm)
    entry = B_MB.get(mb_type)
    if entry is None:
        return {"mb_kind": "unknown", "pred": "Bi",
                "partitions": [{"x": 0, "y": 0, "w": 16, "h": 16,
                                "kind": "unknown", "pred": "Bi", "intra_mode": None}]}
    kind, shape, preds = entry
    if shape is not None:
        geom = _bpart(shape, preds)
        parts = [{"x": x, "y": y, "w": w, "h": h, "kind": kind, "pred": pr,
                  "intra_mode": None} for (x, y, w, h, pr) in geom]
        return {"mb_kind": kind, "pred": preds[0], "partitions": parts}
    # B_8x8：4 个 8x8，各按 sub_mb_type
    subs = fm.get("sub_mb_type", [])
    parts = []
    for q, (qx, qy) in enumerate(BLK8x8_POS):
        st = subs[q] if q < len(subs) else 0
        skind, sgeom, spred = B_SUB.get(st, ("inter_8x8", [(0, 0, 8, 8)], "Bi"))
        for (sx, sy, sw, sh) in sgeom:
            parts.append({"x": qx + sx, "y": qy + sy, "w": sw, "h": sh,
                          "kind": skind, "pred": spred, "intra_mode": None})
    return {"mb_kind": "inter_8x8", "pred": "Bi", "partitions": parts}
