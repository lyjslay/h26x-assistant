"""HM TraceDec.txt 解析器 → 头部语法结构(VPS/SPS/PPS/Slice)。

HM trace 格式：
  段落标题:  =========== Sequence Parameter Set  ===========
  字段行:    "   132  sps_strong_intra_smoothing_enable_flag   u(1)  : 1"
             = <bit位置> <字段名> <描述子(u/ue/se(v)等)> : <值>
  CU 结构语法不在 trace 中(HEVC 用 CABAC bin)，改由 cu_dump.csv 提供(见 hm_cu.py)。

按段落分组，每段对应一个 NAL 单元(VPS/SPS/PPS/SEI/Slice)。
Slice 段落有多个(每帧一个)，按出现顺序与解码帧对应。
"""
import re
from pathlib import Path
from typing import Dict, List

_RE_HEADER = re.compile(r"=+\s*(?P<name>[A-Za-z0-9 ]+?)\s*=+\s*$")
_RE_FIELD = re.compile(
    r"^\s*(?P<bit>\d+)\s+(?P<name>[A-Za-z0-9_\[\]]+)\s+"
    r"(?P<desc>[a-z]+\([^)]*\)|[a-z]+\(\d+\))\s*:\s*(?P<val>-?\d+)\s*$"
)

# 段落名 → (英文, 中文, 类别键)
SECTION_MAP = {
    "Video Parameter Set": ("VPS", "视频参数集", "vps"),
    "Sequence Parameter Set": ("SPS", "序列参数集", "sps"),
    "Picture Parameter Set": ("PPS", "图像参数集", "pps"),
    "Slice": ("Slice Header", "片头", "slice"),
    "SEI message": ("SEI", "补充增强信息", "sei"),
    "User data unregistered SEI message": ("SEI(user)", "用户自定义SEI", "sei"),
    "Active parameter sets SEI message": ("SEI(aps)", "激活参数集SEI", "sei"),
}

# slice_type: HEVC 0=B 1=P 2=I
HEVC_SLICE_TYPE = {0: "B", 1: "P", 2: "I"}


def _section_info(name: str):
    for key, v in SECTION_MAP.items():
        if name.startswith(key):
            return v
    return (name, name, "other")


def parse_trace(trace_path: Path) -> Dict[str, object]:
    """解析 → { sections: [ {name_en, name_zh, kind, fields:[...]} ] }。

    Slice 段落单独标 slice_seq(第几个 slice，从0)，供与解码帧对齐。
    """
    sections: List[Dict] = []
    cur: Dict = None
    slice_seq = 0

    with open(str(trace_path), "r", encoding="latin-1", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            mh = _RE_HEADER.match(line)
            if mh:
                en, zh, kind = _section_info(mh.group("name").strip())
                cur = {"name_en": en, "name_zh": zh, "kind": kind, "fields": []}
                if kind == "slice":
                    cur["slice_seq"] = slice_seq
                    slice_seq += 1
                sections.append(cur)
                continue
            mf = _RE_FIELD.match(line)
            if mf and cur is not None:
                cur["fields"].append({
                    "bit": int(mf.group("bit")),
                    "name": mf.group("name"),
                    "descriptor": mf.group("desc"),
                    "value": int(mf.group("val")),
                })
    return {"sections": sections}


def slice_sections(parsed: Dict) -> List[Dict]:
    return [s for s in parsed["sections"] if s["kind"] == "slice"]


def field_value(section: Dict, name: str):
    for f in section.get("fields", []):
        if f["name"] == name:
            return f["value"]
    return None
