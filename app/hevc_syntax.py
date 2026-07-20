"""HEVC 语法业务层：驱动 HM 解码 → 解析 trace/CU dump → 附中英文字典 → 缓存。

与 H.264 的 syntax.py 平行；对外 API 形状一致，便于前端统一。
- syntax_overview(project_id): 帧列表(解码序，来自 HM stdout)
- frame_syntax(project_id, index): 单帧语法树(NAL 头字段 + CU 列表)
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import hm_cu, hm_decoder, hm_trace_parser, project

_DICT_PATH = Path(__file__).resolve().parent / "data" / "syntax_dict_h265.json"
_DICT: Optional[Dict[str, Dict]] = None

# HEVC PartSize 枚举 → 名称
PART_SIZE = {0: "2Nx2N", 1: "2NxN", 2: "Nx2N", 3: "NxN",
             4: "2NxnU", 5: "2NxnD", 6: "nLx2N", 7: "nRx2N"}
PRED_MODE = {0: "inter", 1: "intra", 2: "none"}


def _load_dict() -> Dict[str, Dict]:
    global _DICT
    if _DICT is None:
        _DICT = json.loads(_DICT_PATH.read_text(encoding="utf-8"))
    return _DICT


def _normalize(name: str) -> str:
    """去掉数组下标后缀 [i]/[][j] 以匹配字典键。"""
    d = _load_dict()
    if name in d:
        return name
    base = name.split("[")[0]
    return base


def _annotate(name: str) -> Dict[str, str]:
    key = _normalize(name)
    d = _load_dict().get(key)
    if d:
        return {"name_zh": d.get("zh", ""), "desc": d.get("desc", ""),
                "clause": d.get("clause", "")}
    return {"name_zh": "", "desc": "", "clause": ""}


def _ensure(project_id: str, cfg=None) -> Dict[str, object]:
    """解码 + 解析 trace/CU/stdout，带磁盘缓存。返回结构化中间数据。"""
    proj = project.Project(project_id)
    paths = hm_decoder.ensure_hevc_decoded(project_id, cfg=cfg)
    cache = proj.dir / "hevc_parsed.json"
    stamp = proj.dir / ".hm_stamp"
    pstamp = proj.dir / ".hevc_syntax_stamp"
    cur = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    if cache.exists() and pstamp.exists() and \
            pstamp.read_text(encoding="utf-8").strip() == cur:
        return json.loads(cache.read_text(encoding="utf-8"))

    frames = hm_decoder.parse_hm_frames(paths["stdout"])       # 解码序 + 参考列表
    parsed_trace = hm_trace_parser.parse_trace(paths["trace"])  # 头部段落
    slices = hm_trace_parser.slice_sections(parsed_trace)
    param_sections = [s for s in parsed_trace["sections"] if s["kind"] != "slice"]

    # 每帧关联一个 slice 段落(按 first_slice 出现顺序)。HM trace 的 slice 段
    # 顺序即解码顺序；取每帧对应第 index 个 slice。
    result = {
        "frames": frames,
        "param_sections": param_sections,
        "slice_sections": slices,
    }
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    pstamp.write_text(cur, encoding="utf-8")
    return result


def syntax_overview(project_id: str, cfg=None) -> Dict[str, object]:
    meta = project.get_project(project_id)
    data = _ensure(project_id, cfg=cfg)
    frames = data["frames"]
    segs = hm_cu.parse_cu_dump_segments(
        project.Project(project_id).dir / hm_decoder.CU_DUMP_NAME)
    out_frames = []
    for fr in frames:
        ncu = len(segs[fr["index"]]["cus"]) if fr["index"] < len(segs) else 0
        out_frames.append({
            "index": fr["index"], "poc": fr["poc"],
            "slice_type": fr["slice_type"], "frame_num": None,
            "num_mbs": ncu,  # 复用字段名(前端统一)：HEVC 下为 CU 数
            "num_nals": 1,
            "nal_types": ["Slice (%s)" % fr["slice_type"]],
            "qp": fr["qp"],
            "ref_l0": fr["ref_l0"], "ref_l1": fr["ref_l1"],
        })
    return {
        "project_id": project_id, "codec": meta.get("codec"),
        "width": meta.get("width"), "height": meta.get("height"),
        "num_frames": len(out_frames), "frames": out_frames,
        "unit": "CU",
    }


def _decorate_section(sec: Dict) -> Dict:
    fields = []
    for f in sec.get("fields", []):
        ann = _annotate(f["name"])
        fields.append({
            "bit": f["bit"], "name_en": f["name"], "name_zh": ann["name_zh"],
            "descriptor": f.get("descriptor", ""),
            "binary": "", "value": f.get("value"),
            "desc": ann["desc"], "clause": ann["clause"],
        })
    return {
        "name_en": sec["name_en"], "name_zh": sec["name_zh"],
        "kind": sec["kind"], "fields": fields,
    }


def frame_syntax(project_id: str, index: int, cfg=None,
                 include_mbs: bool = True) -> Dict[str, object]:
    data = _ensure(project_id, cfg=cfg)
    frames = data["frames"]
    if index < 0 or index >= len(frames):
        raise IndexError("帧索引越界: %d (共 %d 帧)" % (index, len(frames)))
    fr = frames[index]

    # NAL 段落：首帧含 VPS/SPS/PPS/SEI，之后帧只挂自己的 slice
    nals: List[Dict] = []
    if index == 0:
        for s in data["param_sections"]:
            nals.append(_decorate_section(s))
    slices = data["slice_sections"]
    if index < len(slices):
        nals.append(_decorate_section(slices[index]))

    out = {
        "index": fr["index"], "poc": fr["poc"],
        "slice_type": fr["slice_type"], "frame_num": None,
        "qp": fr["qp"], "ref_l0": fr["ref_l0"], "ref_l1": fr["ref_l1"],
        "nals": nals,
    }
    if include_mbs:
        cus = hm_cu.cus_for_decode_index(
            project.Project(project_id).dir / hm_decoder.CU_DUMP_NAME, index)
        out["num_mbs"] = len(cus)
        out["macroblocks"] = [_cu_as_mb(c) for c in cus]
    else:
        out["num_mbs"] = 0
    return out


def _cu_as_mb(cu: Dict) -> Dict:
    """把一个 CU 表达成"宏块"结构(前端统一)。字段用 CU 语义。"""
    pm = PRED_MODE.get(cu["predMode"], "?")
    fields = [
        {"bit": None, "name_en": "cu_pos", "name_zh": "CU位置",
         "binary": "", "value": "(%d,%d)" % (cu["x"], cu["y"]),
         "desc": "CU 左上角像素坐标", "clause": ""},
        {"bit": None, "name_en": "cu_size", "name_zh": "CU尺寸",
         "binary": "", "value": cu["size"], "desc": "CU 边长(像素)", "clause": ""},
        {"bit": None, "name_en": "depth", "name_zh": "四叉树深度",
         "binary": "", "value": cu["depth"], "desc": "CTU 四叉树划分深度", "clause": ""},
        {"bit": None, "name_en": "pred_mode", "name_zh": "预测模式",
         "binary": "", "value": pm, "desc": "intra/inter", "clause": ""},
        {"bit": None, "name_en": "part_size", "name_zh": "PU分割",
         "binary": "", "value": PART_SIZE.get(cu["partSize"], "?"),
         "desc": "预测单元分割方式", "clause": ""},
        {"bit": None, "name_en": "qp", "name_zh": "量化参数QP",
         "binary": "", "value": cu["qp"], "desc": "该 CU 的 QP", "clause": ""},
    ]
    if cu["predMode"] == 1:
        fields.append({"bit": None, "name_en": "intra_dir_luma", "name_zh": "帧内亮度方向",
                       "binary": "", "value": cu["intraDirY"],
                       "desc": "亮度帧内预测方向(0=Planar,1=DC,2-34角度)", "clause": ""})
    elif cu["predMode"] == 0:
        fields.append({"bit": None, "name_en": "inter_dir", "name_zh": "帧间预测方向",
                       "binary": "", "value": cu["interDir"],
                       "desc": "1=L0,2=L1,3=双向", "clause": ""})
        if cu["interDir"] & 1:
            fields.append({"bit": None, "name_en": "mv_l0", "name_zh": "L0运动矢量",
                           "binary": "", "value": "(%d,%d) ref%d" % (cu["mvL0x"], cu["mvL0y"], cu["refL0"]),
                           "desc": "列表0 MV(1/4像素)与参考索引", "clause": ""})
        if cu["interDir"] & 2:
            fields.append({"bit": None, "name_en": "mv_l1", "name_zh": "L1运动矢量",
                           "binary": "", "value": "(%d,%d) ref%d" % (cu["mvL1x"], cu["mvL1y"], cu["refL1"]),
                           "desc": "列表1 MV(1/4像素)与参考索引", "clause": ""})
    return {
        "mb_index": None,  # HEVC 用坐标标识，非线性 index
        "cu_x": cu["x"], "cu_y": cu["y"], "cu_size": cu["size"],
        "slice_kind": PRED_MODE.get(cu["predMode"], "?"),
        "type_code": cu["predMode"],
        "num_residual_coeffs": 0,
        "fields": fields,
    }
