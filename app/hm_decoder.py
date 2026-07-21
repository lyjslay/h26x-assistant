"""HEVC 解码驱动：调用重编译的 HM 分析器(带 ENC_DEC_TRACE + HM_CU_DUMP)。

一次运行产出三份数据(均落在工程目录 cwd)：
- TraceDec.txt   : VPS/SPS/PPS/Slice 头逐字段(bit位置+名+值) —— 供语法页
- cu_dump.csv    : 逐 CU 几何/深度/QP/预测/MV/ref —— 供预览叠加
- stdout(捕获)   : 每 POC 的 slice 类型/QP/参考列表[L0][L1] —— 供帧信息+参考图

要求 HM 分析器为本项目重编译版(makefile 加 -DENC_DEC_TRACE=1 -DHM_CU_DUMP=1)。
若为 stock 分析器(无 trace/dump)，给出明确中文提示。
"""
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import config, project

TRACE_NAME = "TraceDec.txt"
CU_DUMP_NAME = "cu_dump.csv"
TU_DUMP_NAME = "tu_dump.csv"
SAO_DUMP_NAME = "sao_dump.csv"
HM_STDOUT_NAME = "hm_stdout.txt"
DEC_YUV_NAME = "hm_decoded.yuv"

# HM 每帧摘要行:  POC 3 TId: 0 ( P-SLICE, QP 30 ) [DT ...] [L0 0 ] [L1 3 ] ...
_RE_POC = re.compile(
    r"POC\s+(?P<poc>-?\d+)\s+TId:\s*\d+\s*\(\s*(?P<stype>[IPB])-SLICE,\s*QP\s*(?P<qp>-?\d+)\s*\)"
    r".*?\[L0(?P<l0>[^\]]*)\]\s*\[L1(?P<l1>[^\]]*)\]"
)


class HMDecodeError(RuntimeError):
    pass


def _stamp(es_path: Path) -> str:
    st = es_path.stat()
    return "%d-%d" % (int(st.st_mtime), st.st_size)


def ensure_hevc_decoded(project_id: str, cfg: Optional[Dict[str, str]] = None,
                        force: bool = False) -> Dict[str, Path]:
    """确保 HEVC 已解码，返回 {trace, cu_dump, stdout} 路径。"""
    if cfg is None:
        cfg = config.load_config()
    meta = project.get_project(project_id)
    if meta.get("codec") != "hevc":
        raise HMDecodeError("HEVC 解码器仅用于 hevc 流，此流为 %s" % meta.get("codec"))

    hm = cfg.get("hm_analyser", "")
    if not hm or not os.path.exists(hm):
        raise HMDecodeError("未配置 HM 分析器(hm_analyser)，请在设置页填写 TAppDecoderAnalyser 路径")

    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]
    trace_path = proj.dir / TRACE_NAME
    cu_path = proj.dir / CU_DUMP_NAME
    out_path = proj.dir / HM_STDOUT_NAME
    stamp_path = proj.dir / ".hm_stamp"
    cur = _stamp(es_path)

    if (not force and trace_path.exists() and cu_path.exists() and out_path.exists()
            and stamp_path.exists()
            and stamp_path.read_text(encoding="utf-8").strip() == cur):
        return {"trace": trace_path, "cu_dump": cu_path, "stdout": out_path}

    _run_hm(hm, proj.dir, es_path.name, out_path)

    if not cu_path.exists() or cu_path.stat().st_size == 0:
        raise HMDecodeError(
            "HM 解码完成但未生成 cu_dump.csv。请确认使用的是**本项目重编译版**"
            " HM 分析器(编译时加 -DENC_DEC_TRACE=1 -DHM_CU_DUMP=1)，而非官方 stock 版。")
    stamp_path.write_text(cur, encoding="utf-8")
    return {"trace": trace_path, "cu_dump": cu_path, "stdout": out_path}


def _run_hm(hm: str, workdir: Path, es_name: str, out_path: Path) -> None:
    cmd = [os.path.abspath(hm), "-b", es_name, "-o", DEC_YUV_NAME]
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        raise HMDecodeError("HM 解码超时(>30min)")
    except OSError as e:
        raise HMDecodeError("无法运行 HM 分析器: %s" % e)
    out = proc.stdout.decode("utf-8", errors="replace")
    out_path.write_text(out, encoding="utf-8")
    if "POC" not in out:
        raise HMDecodeError("HM 未输出解码信息(exit=%d)：\n%s" % (proc.returncode, out[-600:]))


def parse_hm_frames(stdout_path: Path) -> List[Dict]:
    """解析 HM stdout 每 POC 摘要 → 帧列表(解码序)含参考列表。"""
    frames: List[Dict] = []
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    for m in _RE_POC.finditer(text):
        l0 = [int(x) for x in m.group("l0").split()]
        l1 = [int(x) for x in m.group("l1").split()]
        frames.append({
            "index": len(frames),
            "poc": int(m.group("poc")),
            "slice_type": m.group("stype"),
            "qp": int(m.group("qp")),
            "ref_l0": l0, "ref_l1": l1,
        })
    return frames
