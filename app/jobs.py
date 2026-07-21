"""后台解码任务 + 进度反馈。

解码(JM/HM)是最耗时的步骤(1440p 可达~100s)。本模块把解码放到后台线程，
边解码边解析解码器 stdout 的逐帧输出，实时更新进度，供前端轮询。

- start_decode(project_id): 若未解码则启动后台线程；已在跑或已完成则直接返回状态。
- get_status(project_id): 返回 {state, done, total, message, error}。

state ∈ pending / running / done / error。
每工程仅一个解码任务(用 project.get_decode_lock 串行化)。
"""
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

from . import config, decoder, hm_decoder, project

# 进度状态表(内存)：project_id -> dict
_STATUS: Dict[str, Dict] = {}
_STATUS_GUARD = threading.Lock()

# JM ldecod 每解码一帧输出一行，形如 "00003( P )   6   1  9 ..."
_JM_FRAME = re.compile(r"^\s*\d+\(\s*[IPBb]+\s*\)")
# HM 每帧一行 "POC    3 TId: 0 ( P-SLICE ...)"
_HM_FRAME = re.compile(r"^POC\s+-?\d+\s+TId")


def _set(project_id: str, **kw):
    with _STATUS_GUARD:
        st = _STATUS.setdefault(project_id, {})
        st.update(kw)


def get_status(project_id: str) -> Dict:
    with _STATUS_GUARD:
        st = dict(_STATUS.get(project_id, {}))
    if not st:
        # 未启动：若已有缓存解码结果，视为 done
        if _already_decoded(project_id):
            return {"state": "done", "done": 0, "total": 0,
                    "message": "已解码(缓存)", "error": ""}
        return {"state": "idle", "done": 0, "total": 0, "message": "", "error": ""}
    return st


def _already_decoded(project_id: str) -> bool:
    try:
        meta = project.get_project(project_id)
    except FileNotFoundError:
        return False
    proj = project.Project(project_id)
    if meta.get("codec") == "h264":
        return (proj.dir / decoder.TRACE_NAME).exists() and (proj.dir / ".trace_stamp").exists()
    if meta.get("codec") == "hevc":
        return (proj.dir / hm_decoder.CU_DUMP_NAME).exists() and (proj.dir / ".hm_stamp").exists()
    return False


def _expected_frames(project_id: str, cfg) -> int:
    """用 ffprobe 估计总帧数(供进度分母)。失败返回 0(进度按不定态显示)。"""
    try:
        meta = project.get_project(project_id)
        ffprobe = cfg.get("ffprobe", "")
        proj = project.Project(project_id)
        es = str(proj.dir / meta["elementary_stream"])
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-count_packets", "-show_entries", "stream=nb_read_packets",
             "-of", "csv=p=0", es],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return int((out.stdout.decode("utf-8", "replace").strip() or "0"))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError):
        return 0


def start_decode(project_id: str, cfg: Optional[Dict[str, str]] = None) -> Dict:
    """启动(或复用)后台解码，立即返回当前状态。"""
    if cfg is None:
        cfg = config.load_config()
    st = get_status(project_id)
    if st["state"] in ("running", "pending"):
        return st
    if st["state"] == "done":
        return st

    _set(project_id, state="pending", done=0, total=0, message="准备解码…", error="")
    t = threading.Thread(target=_run, args=(project_id, cfg), daemon=True)
    t.start()
    return get_status(project_id)


def _run(project_id: str, cfg: Dict):
    lock = project.get_decode_lock(project_id)
    with lock:
        # 二次检查：可能在等锁期间别的线程已完成
        if _already_decoded(project_id):
            _set(project_id, state="done", message="已解码(缓存)")
            return
        try:
            meta = project.get_project(project_id)
        except FileNotFoundError as e:
            _set(project_id, state="error", error=str(e))
            return

        codec = meta.get("codec")
        total = _expected_frames(project_id, cfg)
        _set(project_id, state="running", done=0, total=total,
             message="解码中…", error="")
        try:
            if codec == "h264":
                _decode_h264(project_id, cfg)
            elif codec == "hevc":
                _decode_hevc(project_id, cfg)
            else:
                raise RuntimeError("不支持的编码: %s" % codec)
            _set(project_id, state="done", message="解码完成")
        except Exception as e:  # noqa: BLE001 —— 后台线程需兜底一切异常到状态
            _set(project_id, state="error", error=str(e), message="解码失败")


def _decode_h264(project_id: str, cfg: Dict):
    """运行 JM ldecod，流式解析进度。写 trace 后设 stamp(复用 decoder 的缓存约定)。"""
    ldecod = cfg.get("jm_ldecod", "")
    if not ldecod or not Path(ldecod).exists():
        raise RuntimeError("未配置 JM 解码器(jm_ldecod)")
    proj = project.Project(project_id)
    meta = project.get_project(project_id)
    es = proj.dir / meta["elementary_stream"]
    cmd = [str(Path(ldecod).resolve()),
           "-p", "InputFile=%s" % es.name,
           "-p", "OutputFile=%s" % decoder.DEC_YUV_NAME,
           "-p", "RefFile=", "-p", "Silent=0"]
    _stream_run(project_id, cmd, str(proj.dir), _JM_FRAME)
    trace = proj.dir / decoder.TRACE_NAME
    if not trace.exists() or trace.stat().st_size == 0:
        raise RuntimeError(
            "JM 解码完成但未生成 trace_dec.txt(请确认 ldecod 为 TRACE=1 编译版)")
    st = es.stat()
    (proj.dir / ".trace_stamp").write_text("%d-%d" % (int(st.st_mtime), st.st_size),
                                           encoding="utf-8")


def _decode_hevc(project_id: str, cfg: Dict):
    """运行 HM 分析器，流式解析进度。"""
    hm = cfg.get("hm_analyser", "")
    if not hm or not Path(hm).exists():
        raise RuntimeError("未配置 HM 分析器(hm_analyser)")
    proj = project.Project(project_id)
    meta = project.get_project(project_id)
    es = proj.dir / meta["elementary_stream"]
    out_path = proj.dir / hm_decoder.HM_STDOUT_NAME
    cmd = [str(Path(hm).resolve()), "-b", es.name, "-o", hm_decoder.DEC_YUV_NAME]
    captured = _stream_run(project_id, cmd, str(proj.dir), _HM_FRAME)
    out_path.write_text(captured, encoding="utf-8")
    if not (proj.dir / hm_decoder.CU_DUMP_NAME).exists():
        raise RuntimeError(
            "HM 解码完成但未生成 cu_dump.csv(请使用重编译版 HM 分析器，见 hm_patch/)")
    st = es.stat()
    (proj.dir / ".hm_stamp").write_text("%d-%d" % (int(st.st_mtime), st.st_size),
                                        encoding="utf-8")


def _stream_run(project_id: str, cmd, cwd: str, frame_re) -> str:
    """运行子进程，逐行读 stdout，命中帧模式则 +1 进度。返回完整 stdout。"""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=1,
                            universal_newlines=True)
    lines = []
    done = 0
    try:
        for line in proc.stdout:
            lines.append(line)
            if frame_re.match(line):
                done += 1
                with _STATUS_GUARD:
                    st = _STATUS.setdefault(project_id, {})
                    st["done"] = done
                    if st.get("total"):
                        st["message"] = "解码中… %d/%d 帧" % (done, st["total"])
                    else:
                        st["message"] = "解码中… %d 帧" % done
    finally:
        proc.wait(timeout=60)
    if proc.returncode not in (0, None):
        tail = "".join(lines[-15:])
        # JM 有时非 0 但已完成；交由上层按产物判定，这里仅在无产物时才算错
        if "frames are decoded" not in "".join(lines) and "POC" not in "".join(lines):
            raise RuntimeError("解码器退出码 %d:\n%s" % (proc.returncode, tail))
    return "".join(lines)
