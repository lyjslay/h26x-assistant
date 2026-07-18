"""JM trace_dec.txt 解析器 → 统一 JSON 语法结构。

JM 源码是 Latin-1，trace 亦然，必须以 latin-1 读取。

trace 语法(实测确认)：
  NAL 边界:
    "Annex B NALU w/ long startcode, len 25, forbidden_bit 0, nal_reference_idc 3, nal_unit_type 7"
  参数集/片头字段行(带分类前缀):
    "@0     SPS: profile_idc                          01100100 (100) "
    分类 ∈ {SPS, PPS, SH, SEI, ...}
  宏块起始分隔符:
    "*********** POC: 0 (I/P) MB: 3 Slice: 0 Type 2 **********"
  宏块语法字段(无分类前缀，值在括号):
    "@0      mb_type                                     (  3)"
  残差/系数行(两个尾随数字，无括号)：
    "@27     Luma sng                                    20    0"

统一 schema：见 parse_trace() 返回。
"""
import re
from pathlib import Path
from typing import Dict, List, Optional

_RE_NALU = re.compile(
    r"Annex B NALU w/ (?P<sc>long|short) startcode,\s*len (?P<len>\d+),"
    r"\s*forbidden_bit (?P<fb>\d+),\s*nal_reference_idc (?P<ridc>\d+),"
    r"\s*nal_unit_type (?P<ntype>\d+)"
)
_RE_FIELD_CAT = re.compile(
    r"^@(?P<bit>\d+)\s+(?P<cat>[A-Za-z0-9_]+):\s+(?P<name>[A-Za-z0-9_]+)\s+"
    r"(?P<rest>.*)$"
)
_RE_MB = re.compile(
    r"\*+\s*POC:\s*(?P<poc>-?\d+)\s*\((?P<pt>[^)]*)\)\s*MB:\s*(?P<mb>\d+)"
    r"\s*Slice:\s*(?P<slice>\d+)\s*Type\s*(?P<type>-?\d+)"
)
_RE_MB_LINE = re.compile(r"^@(?P<bit>\d+)\s+(?P<body>.+?)\s*$")
_RE_PAREN = re.compile(r"\((?P<val>[ \t]*-?\d+)\)\s*$")
_RE_BIN = re.compile(r"(?P<bin>[01]{1,64})\s+\((?P<val>[ \t]*-?\d+)\)\s*$")

NAL_TYPES = {
    0: ("Unspecified", "未指定"),
    1: ("Coded slice (non-IDR)", "非IDR片"),
    2: ("Coded slice data partition A", "片数据分区A"),
    3: ("Coded slice data partition B", "片数据分区B"),
    4: ("Coded slice data partition C", "片数据分区C"),
    5: ("Coded slice (IDR)", "IDR片(关键帧)"),
    6: ("SEI", "补充增强信息"),
    7: ("SPS", "序列参数集"),
    8: ("PPS", "图像参数集"),
    9: ("Access unit delimiter", "访问单元分隔符"),
    10: ("End of sequence", "序列结束"),
    11: ("End of stream", "流结束"),
    12: ("Filler data", "填充数据"),
    13: ("SPS extension", "SPS扩展"),
    14: ("Prefix NAL unit", "前缀NAL"),
    15: ("Subset SPS", "子集SPS"),
    19: ("Coded slice aux", "辅助编码片"),
    20: ("Coded slice extension", "编码片扩展"),
}

SLICE_TYPE = {
    0: "P", 1: "B", 2: "I", 3: "SP", 4: "SI",
    5: "P", 6: "B", 7: "I", 8: "SP", 9: "SI",
}

_SLICE_NAL = (1, 2, 5, 19, 20)


def _to_int(s: str) -> Optional[int]:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _parse_rest(rest: str):
    m = _RE_BIN.search(rest)
    if m:
        return m.group("bin"), _to_int(m.group("val"))
    m = _RE_PAREN.search(rest)
    if m:
        return "", _to_int(m.group("val"))
    return "", None


# CAVLC 系数编码相关的中间量标记(coeff_token/level/run 等)，归入残差桶不污染语法字段
_COEFF_MARKERS = ("#c=", "#t1=", "vlc=", "tr.1", "trailing", "total_zeros",
                  "totalcoeff", "run_before", "# c &", "level", "coeff_token")


def _is_coeff_coding(name: str) -> bool:
    low = name.lower()
    return any(mk.lower() in low for mk in _COEFF_MARKERS)


def _classify_mb_line(body: str):
    """宏块内一行 → (kind, name, binary, value, coeff, run)。kind∈{'syntax','residual'}。

    残差桶包含：变换系数(尾随两整数)与 CAVLC 系数编码中间量(coeff_token/level 等)。
    语法桶只保留干净的预测/模式类语法元素(mb_type/mvd/ref_idx/intra 模式/cbp 等)。
    """
    m = _RE_BIN.search(body)
    if m:
        name = body[:m.start()].strip()
        if _is_coeff_coding(name):
            return ("residual", name, "", None, _to_int(m.group("val")), None)
        return ("syntax", name, m.group("bin"), _to_int(m.group("val")), None, None)
    m = _RE_PAREN.search(body)
    if m:
        name = body[:m.start()].strip()
        if _is_coeff_coding(name):
            return ("residual", name, "", None, _to_int(m.group("val")), None)
        return ("syntax", name, "", _to_int(m.group("val")), None, None)
    rm = re.search(r"(-?\d+)\s+(-?\d+)\s*$", body)
    if rm:
        return ("residual", body[:rm.start()].strip(), "", None,
                _to_int(rm.group(1)), _to_int(rm.group(2)))
    return ("syntax", body.strip(), "", None, None, None)


def parse_trace(trace_path: Path) -> Dict[str, object]:
    """解析整个 trace_dec.txt → { frames:[...], nal_index:[...] }。

    帧组织(处理 JM 的"读前一个 slice header"预读)：
    - JM 解码器会在输出当前帧的宏块之前，先解析下一个 slice 的 header，
      因此 slice NAL 比其宏块"提前一拍"。
    - 用 slice_fifo(先进先出的 slice NAL 队列)解决：每个 slice NAL 到达时，
      连同它前面暂存的非 slice NAL(SPS/PPS/SEI) 打包成 bundle 入队；
      当宏块分隔符标志新帧/新片开始时，从队首取一个 bundle 归入该帧。
    """
    frames: List[Dict] = []
    nal_index: List[Dict] = []
    pending_nonslice: List[Dict] = []
    slice_fifo: List[List[Dict]] = []   # 每项是一个 bundle(NAL 列表)

    cur_frame: Optional[Dict] = None
    cur_nal: Optional[Dict] = None
    cur_mb: Optional[Dict] = None
    cur_slice_no: Optional[int] = None
    nal_counter = 0

    def start_frame(poc, stype):
        fr = {
            "index": len(frames), "poc": poc,
            "slice_type": SLICE_TYPE.get(stype, str(stype)),
            "slice_type_val": stype, "frame_num": None,
            "nals": [], "macroblocks": [],
        }
        frames.append(fr)
        return fr

    def attach_next_bundle(fr):
        if slice_fifo:
            fr["nals"].extend(slice_fifo.pop(0))

    with open(str(trace_path), "r", encoding="latin-1", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            mn = _RE_NALU.search(line)
            if mn:
                ntype = int(mn.group("ntype"))
                en, zh = NAL_TYPES.get(ntype, ("type %d" % ntype, "类型%d" % ntype))
                cur_nal = {
                    "nal_index": nal_counter,
                    "startcode": mn.group("sc"),
                    "length": int(mn.group("len")),
                    "forbidden_bit": int(mn.group("fb")),
                    "nal_ref_idc": int(mn.group("ridc")),
                    "nal_unit_type": ntype,
                    "name_en": en, "name_zh": zh,
                    "is_slice": ntype in _SLICE_NAL,
                    "fields": [],
                }
                nal_counter += 1
                nal_index.append({
                    "nal_index": cur_nal["nal_index"],
                    "nal_unit_type": ntype, "name_en": en, "name_zh": zh,
                    "length": cur_nal["length"], "startcode": cur_nal["startcode"],
                })
                if cur_nal["is_slice"]:
                    bundle = pending_nonslice + [cur_nal]
                    pending_nonslice = []
                    slice_fifo.append(bundle)
                else:
                    pending_nonslice.append(cur_nal)
                cur_mb = None
                continue

            mm = _RE_MB.search(line)
            if mm:
                poc = int(mm.group("poc"))
                stype = int(mm.group("type"))
                slice_no = int(mm.group("slice"))
                new_frame = cur_frame is None or cur_frame["poc"] != poc
                if new_frame:
                    cur_frame = start_frame(poc, stype)
                    cur_slice_no = slice_no
                    attach_next_bundle(cur_frame)
                elif slice_no != cur_slice_no:
                    # 同帧多 slice：再取一个 bundle
                    cur_slice_no = slice_no
                    attach_next_bundle(cur_frame)
                cur_mb = {
                    "mb_index": int(mm.group("mb")),
                    "slice": slice_no,
                    "type_code": stype,
                    "fields": [], "residuals": [],
                }
                cur_frame["macroblocks"].append(cur_mb)
                continue

            if line.startswith("@"):
                if cur_mb is not None:
                    ml = _RE_MB_LINE.match(line)
                    if ml:
                        bit = int(ml.group("bit"))
                        kind, name, binv, val, coeff, run = _classify_mb_line(ml.group("body"))
                        if kind == "residual":
                            cur_mb["residuals"].append(
                                {"bit": bit, "name": name, "coeff": coeff, "run": run})
                        else:
                            cur_mb["fields"].append(
                                {"bit": bit, "name": name, "binary": binv, "value": val})
                    continue
                mc = _RE_FIELD_CAT.match(line)
                if mc and cur_nal is not None:
                    binv, val = _parse_rest(mc.group("rest"))
                    cur_nal["fields"].append({
                        "bit": int(mc.group("bit")),
                        "category": mc.group("cat"),
                        "name": mc.group("name"),
                        "binary": binv, "value": val,
                    })
                continue

    # 收尾：未被任何帧领取的 bundle / 非 slice NAL → 参数占位帧
    leftover: List[Dict] = []
    for b in slice_fifo:
        leftover.extend(b)
    leftover.extend(pending_nonslice)
    if leftover:
        frames.append({
            "index": len(frames), "poc": None, "slice_type": "params",
            "slice_type_val": None, "frame_num": None,
            "nals": leftover, "macroblocks": [],
        })

    # 后处理：从 slice header 抽取 frame_num
    for fr in frames:
        for nal in fr["nals"]:
            if nal.get("is_slice"):
                for f in nal["fields"]:
                    if f["name"] == "frame_num":
                        fr["frame_num"] = f["value"]
                        break

    return {"frames": frames, "nal_index": nal_index}
