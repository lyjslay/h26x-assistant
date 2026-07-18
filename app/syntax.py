"""语法解析业务层：驱动 JM 解码 → 解析 trace → 附中英文字典 → 缓存。

对外提供：
- syntax_overview(project_id): 帧列表(轻量，供左侧导航)
- frame_syntax(project_id, index): 单帧完整语法树(NAL→字段 / 宏块→字段)
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import decoder, jm_trace_parser, project

_DICT_PATH = Path(__file__).resolve().parent / "data" / "syntax_dict_h264.json"
_DICT: Optional[Dict[str, Dict]] = None

# MB type_code(片类型值) → 中文帧类型已在 parser 处理；此处给宏块 type 名
MB_SLICE_LABEL = {2: "I", 7: "I", 0: "P", 5: "P", 1: "B", 6: "B"}


def _load_dict() -> Dict[str, Dict]:
    global _DICT
    if _DICT is None:
        _DICT = json.loads(_DICT_PATH.read_text(encoding="utf-8"))
    return _DICT


# JM 有时输出不带列表后缀的通用名(mvd_l / ref_idx_l)，或带 0/1 分量后缀，
# 归一到字典键。
def _normalize_field_name(name: str) -> str:
    d = _load_dict()
    if name in d:
        return name
    # JM 通用名/分量后缀归一：*_l -> *_l0；mvd0_l/mvd1_l -> mvd0_l0/mvd1_l0
    alias = {
        "mvd_l": "mvd_l0", "ref_idx_l": "ref_idx_l0",
        "mvd0_l": "mvd0_l0", "mvd1_l": "mvd1_l0",
    }
    return alias.get(name, name)


def _annotate(name: str) -> Dict[str, str]:
    key = _normalize_field_name(name)
    d = _load_dict().get(key)
    if d:
        return {"name_zh": d.get("zh", ""), "desc": d.get("desc", ""),
                "clause": d.get("clause", "")}
    return {"name_zh": "", "desc": "", "clause": ""}


def _parsed_cache_path(proj: "project.Project") -> Path:
    return proj.dir / "syntax_parsed.json"


def _ensure_parsed(project_id: str, cfg=None) -> Dict[str, object]:
    """确保 trace 已生成并解析，带磁盘缓存。"""
    proj = project.Project(project_id)
    trace_path = decoder.ensure_h264_trace(project_id, cfg=cfg)
    cache = _parsed_cache_path(proj)
    stamp = proj.dir / ".trace_stamp"
    parsed_stamp = proj.dir / ".syntax_stamp"
    cur = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""

    if cache.exists() and parsed_stamp.exists() and \
            parsed_stamp.read_text(encoding="utf-8").strip() == cur:
        return json.loads(cache.read_text(encoding="utf-8"))

    parsed = jm_trace_parser.parse_trace(trace_path)
    cache.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    parsed_stamp.write_text(cur, encoding="utf-8")
    return parsed


def syntax_overview(project_id: str, cfg=None) -> Dict[str, object]:
    """帧列表总览(轻量)。"""
    meta = project.get_project(project_id)
    parsed = _ensure_parsed(project_id, cfg=cfg)
    frames = parsed["frames"]
    out_frames: List[Dict] = []
    for fr in frames:
        out_frames.append({
            "index": fr["index"],
            "poc": fr["poc"],
            "slice_type": fr["slice_type"],
            "frame_num": fr["frame_num"],
            "num_mbs": len(fr["macroblocks"]),
            "num_nals": len(fr["nals"]),
            "nal_types": [n["name_en"] for n in fr["nals"]],
        })
    return {
        "project_id": project_id,
        "codec": meta.get("codec"),
        "width": meta.get("width"), "height": meta.get("height"),
        "num_frames": len(frames),
        "frames": out_frames,
    }


def _decorate_nal(nal: Dict) -> Dict:
    fields = []
    for f in nal.get("fields", []):
        ann = _annotate(f["name"])
        fields.append({
            "bit": f["bit"],
            "category": f.get("category", ""),
            "name_en": f["name"],
            "name_zh": ann["name_zh"],
            "binary": f.get("binary", ""),
            "value": f.get("value"),
            "desc": ann["desc"],
            "clause": ann["clause"],
        })
    return {
        "nal_index": nal["nal_index"],
        "nal_unit_type": nal["nal_unit_type"],
        "name_en": nal["name_en"], "name_zh": nal["name_zh"],
        "startcode": nal.get("startcode", ""),
        "length": nal.get("length"),
        "forbidden_bit": nal.get("forbidden_bit"),
        "nal_ref_idc": nal.get("nal_ref_idc"),
        "is_slice": nal.get("is_slice", False),
        "fields": fields,
    }


def _decorate_mb(mb: Dict) -> Dict:
    fields = []
    for f in mb.get("fields", []):
        ann = _annotate(f["name"])
        fields.append({
            "bit": f["bit"],
            "name_en": f["name"], "name_zh": ann["name_zh"],
            "binary": f.get("binary", ""), "value": f.get("value"),
            "desc": ann["desc"], "clause": ann["clause"],
        })
    return {
        "mb_index": mb["mb_index"],
        "slice": mb.get("slice", 0),
        "type_code": mb.get("type_code"),
        "slice_kind": MB_SLICE_LABEL.get(mb.get("type_code"), "?"),
        "num_residual_coeffs": len(mb.get("residuals", [])),
        "fields": fields,
    }


def frame_syntax(project_id: str, index: int, cfg=None,
                 include_mbs: bool = True) -> Dict[str, object]:
    """单帧完整语法树。"""
    parsed = _ensure_parsed(project_id, cfg=cfg)
    frames = parsed["frames"]
    if index < 0 or index >= len(frames):
        raise IndexError("帧索引越界: %d (共 %d 帧)" % (index, len(frames)))
    fr = frames[index]
    out = {
        "index": fr["index"], "poc": fr["poc"],
        "slice_type": fr["slice_type"], "frame_num": fr["frame_num"],
        "nals": [_decorate_nal(n) for n in fr["nals"]],
        "num_mbs": len(fr["macroblocks"]),
    }
    if include_mbs:
        out["macroblocks"] = [_decorate_mb(m) for m in fr["macroblocks"]]
    return out
