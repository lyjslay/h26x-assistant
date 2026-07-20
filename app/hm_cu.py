"""cu_dump.csv 解析器：逐 CU 数据 → 按 POC 分组。

CSV 列(由 HM TDecCu.cpp 的 HM_CU_DUMP 补丁产出)：
  poc,ctu,x,y,size,depth,predMode,partSize,qp,
  intraDirY,interDir,mvL0x,mvL0y,refL0,mvL1x,mvL1y,refL1

predMode: 0=inter 1=intra 2=none
partSize: HEVC PartSize 枚举 0=2Nx2N 1=2NxN 2=Nx2N 3=NxN 4=2NxnU 5=2NxnD 6=nLx2N 7=nRx2N
interDir: 1=L0 2=L1 3=Bi
"""
import csv
from pathlib import Path
from typing import Dict, List


def parse_cu_dump_segments(cu_path: Path) -> List[Dict]:
    """→ [ {poc, cus:[...]} ]，按**解码顺序**切分(相邻 POC 变化即新帧)。

    关键：多 GOP 时 POC 在每个 IDR 重置为 0，故不能按 POC 值分组(会把多个
    POC=0 的帧合并)。CU 行本身按解码序连续写出，按 POC 变化点切段即得每帧。
    """
    segments: List[Dict] = []
    cur_poc = None
    cur: List[Dict] = None
    with open(str(cu_path), "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                cu = {k: int(v) for k, v in row.items()}
            except (ValueError, TypeError):
                continue
            if cur is None or cu["poc"] != cur_poc:
                cur = []
                cur_poc = cu["poc"]
                segments.append({"poc": cur_poc, "cus": cur})
            cur.append(cu)
    return segments


def cus_for_decode_index(cu_path: Path, decode_index: int) -> List[Dict]:
    """取第 decode_index 个解码帧(按解码序切段)的 CU 列表。"""
    segs = parse_cu_dump_segments(cu_path)
    if 0 <= decode_index < len(segs):
        return segs[decode_index]["cus"]
    return []
