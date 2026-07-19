"""扫描 Annex-B 裸流，定位每个起始码 / NAL 的绝对字节区间。

输出的 NAL 列表按流中顺序，可与 trace 的 NAL 顺序一一对齐(顺序一致)。
每项含：start_code 区间、nal header 字节、payload 字节区间、nal_unit_type。
"""
from pathlib import Path
from typing import Dict, List


def scan_nals(es_path: Path) -> List[Dict]:
    """扫描 Annex-B 流，返回 NAL 字节分布。

    每项: {
      index, sc_start, sc_len(3或4), nal_start(=sc_end),
      header_len(1), payload_start, nal_end(下一起始码前/EOF),
      nal_unit_type, nal_ref_idc, forbidden_bit
    }
    """
    data = es_path.read_bytes()
    n = len(data)
    # 找所有起始码位置(00 00 01 或 00 00 00 01)
    starts = []  # (pos_of_startcode, sc_len)
    i = 0
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                starts.append((i, 3))
                i += 3
                continue
            if i + 4 <= n and data[i + 2] == 0 and data[i + 3] == 1:
                starts.append((i, 4))
                i += 4
                continue
        i += 1

    nals = []
    for k, (sc_pos, sc_len) in enumerate(starts):
        nal_start = sc_pos + sc_len
        nal_end = starts[k + 1][0] if k + 1 < len(starts) else n
        if nal_start >= nal_end:
            continue
        header = data[nal_start]
        forbidden = (header >> 7) & 1
        ref_idc = (header >> 5) & 3
        ntype = header & 0x1F
        nals.append({
            "index": len(nals),
            "sc_start": sc_pos,
            "sc_len": sc_len,
            "nal_start": nal_start,        # NAL 头字节起点
            "header_len": 1,
            "payload_start": nal_start + 1,  # RBSP 载荷起点(EPB 未去除)
            "nal_end": nal_end,            # 下一起始码前(含末尾, 可能有trailing)
            "nal_unit_type": ntype,
            "nal_ref_idc": ref_idc,
            "forbidden_bit": forbidden,
            "total_bytes": nal_end - sc_pos,
        })
    return nals
