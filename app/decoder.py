"""解码驱动：调用 JM ldecod 生成逐宏块语法 trace(H.264)。

JM 解码器的 TRACE 在 stock 二进制中已默认开启，运行时会在其工作目录写出
trace_dec.txt(含逐宏块语法)。本模块负责：在工程目录内运行 ldecod、
落地 trace 与 YUV、做缓存与超时控制。

HEVC(HM) 的等价驱动留待 P5。
"""
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

from . import config, project

TRACE_NAME = "trace_dec.txt"
DEC_YUV_NAME = "decoded.yuv"


class DecodeError(RuntimeError):
    pass


def _stamp(es_path: Path) -> str:
    st = es_path.stat()
    return "%d-%d" % (int(st.st_mtime), st.st_size)


def ensure_h264_trace(project_id: str, cfg: Optional[Dict[str, str]] = None,
                      force: bool = False) -> Path:
    """确保工程的 H.264 trace 已生成，返回 trace 文件路径。

    幂等 + 缓存：若 trace 已存在且源流未变，直接复用。
    """
    if cfg is None:
        cfg = config.load_config()
    meta = project.get_project(project_id)
    if meta.get("codec") != "h264":
        raise DecodeError("语法解析(JM)当前仅支持 H.264，此流为 %s" % meta.get("codec"))

    ldecod = cfg.get("jm_ldecod", "")
    if not ldecod or not os.path.exists(ldecod):
        raise DecodeError("未配置 JM 解码器(jm_ldecod)，请在设置页填写 ldecod.exe 路径")

    proj = project.Project(project_id)
    es_path = proj.dir / meta["elementary_stream"]
    trace_path = proj.dir / TRACE_NAME
    stamp_path = proj.dir / ".trace_stamp"
    cur_stamp = _stamp(es_path)

    if not force and trace_path.exists() and stamp_path.exists():
        if stamp_path.read_text(encoding="utf-8").strip() == cur_stamp:
            return trace_path

    _run_ldecod(ldecod, proj.dir, es_path.name)

    if not trace_path.exists() or trace_path.stat().st_size == 0:
        raise DecodeError("JM 解码完成但未生成 trace_dec.txt")
    stamp_path.write_text(cur_stamp, encoding="utf-8")
    return trace_path


def _run_ldecod(ldecod: str, workdir: Path, es_name: str) -> None:
    """在 workdir 内运行 ldecod；trace_dec.txt 会写到 cwd(=workdir)。

    JM 用 -p Param=Value 覆盖配置，-p InputFile 指定输入。
    以 workdir 为 cwd，使 trace_dec.txt / 输出 YUV 落在工程目录。
    """
    cmd = [
        os.path.abspath(ldecod),
        "-p", "InputFile=%s" % es_name,
        "-p", "OutputFile=%s" % DEC_YUV_NAME,
        "-p", "RefFile=",          # 不做 SNR 比对
        "-p", "Silent=0",          # 需要 trace，不静默
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        raise DecodeError("JM 解码超时(>30min)，码流可能过大")
    except OSError as e:
        raise DecodeError("无法运行 JM 解码器: %s" % e)

    out = proc.stdout.decode("utf-8", errors="replace")
    # ldecod 正常结束通常返回 0；即便非 0，只要生成了 trace 也算可用，交给上层判断
    if proc.returncode != 0 and "frames are decoded" not in out:
        tail = out[-800:]
        raise DecodeError("JM 解码失败(exit=%d):\n%s" % (proc.returncode, tail))
