"""工程 / 输入管理：探测 codec、解封装为 Annex-B、维护 workdir。

一个"工程"对应一个输入文件的一次分析会话，数据全部落在
workdir/<project_id>/ 下，可整目录拷贝迁移。
"""
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from . import config

WORKDIR = Path(__file__).resolve().parents[1] / "workdir"

# 每工程一把解码锁：保证同一工程的解码不并发(避免重复解码/竞争 trace 文件)。
_DECODE_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def get_decode_lock(project_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _DECODE_LOCKS.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _DECODE_LOCKS[project_id] = lk
        return lk

# 裸码流扩展名 -> codec
RAW_EXT = {
    ".264": "h264", ".h264": "h264", ".avc": "h264", ".jsv": "h264",
    ".265": "hevc", ".h265": "hevc", ".hevc": "hevc", ".bin": None,
}

# codec -> ffmpeg annexb 比特流过滤器
ANNEXB_BSF = {"h264": "h264_mp4toannexb", "hevc": "hevc_mp4toannexb"}


def _run(cmd: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )


def ffprobe_stream_info(ffprobe: str, path: str) -> Dict[str, object]:
    """取视频流的 codec/宽高/帧率/时长等。"""
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,"
        "duration,nb_frames,bit_rate,pix_fmt,profile,level",
        "-show_entries", "format=duration,format_name,bit_rate",
        "-of", "json", path,
    ]
    proc = _run(cmd, timeout=60)
    data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    if not streams:
        raise ValueError("ffprobe 未找到视频流: %s" % path)
    s = streams[0]

    def _fps(val: str) -> float:
        try:
            num, den = val.split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        except (ValueError, ZeroDivisionError, AttributeError):
            return 0.0

    fps = _fps(s.get("avg_frame_rate", "")) or _fps(s.get("r_frame_rate", ""))
    return {
        "codec": s.get("codec_name", ""),
        "width": int(s.get("width", 0) or 0),
        "height": int(s.get("height", 0) or 0),
        "fps": round(fps, 4),
        "duration": float(s.get("duration") or fmt.get("duration") or 0.0),
        "nb_frames": int(s.get("nb_frames") or 0),
        "pix_fmt": s.get("pix_fmt", ""),
        "profile": str(s.get("profile", "")),
        "level": str(s.get("level", "")),
        "format_name": fmt.get("format_name", ""),
        "container_bitrate": int(fmt.get("bit_rate") or s.get("bit_rate") or 0),
    }


def _is_annexb_raw(path: str, codec: str) -> bool:
    """裸 Annex-B 流(可直接送 JM/HM)还是封装格式(需解封装)。

    判据：扩展名在 RAW_EXT 且 format_name 属于裸流(h264/hevc/rawvideo)。
    """
    ext = Path(path).suffix.lower()
    return ext in RAW_EXT


def demux_to_annexb(ffmpeg: str, src: str, codec: str, dst: str) -> None:
    """把封装文件里的视频流 copy 成 Annex-B 裸流。"""
    bsf = ANNEXB_BSF.get(codec)
    cmd = [ffmpeg, "-y", "-i", src, "-map", "0:v:0", "-c:v", "copy"]
    if bsf:
        cmd += ["-bsf:v", bsf]
    cmd += ["-f", codec, dst]
    proc = _run(cmd, timeout=600)
    if proc.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        err = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError("解封装失败:\n%s" % err)


class Project:
    def __init__(self, project_id: str):
        self.id = project_id
        self.dir = WORKDIR / project_id
        self.meta_path = self.dir / "project.json"

    @property
    def elementary_path(self) -> Path:
        return self.dir / ("stream.%s" %
                           ("264" if self.load_meta().get("codec") == "h264" else "265"))

    def load_meta(self) -> Dict[str, object]:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, meta: Dict[str, object]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def create_project(input_path: str, cfg: Optional[Dict[str, str]] = None,
                   original_name: Optional[str] = None) -> Dict[str, object]:
    """建立工程：探测 codec，必要时解封装为 Annex-B，写 project.json。

    original_name: 上传场景下的原始文件名(input_path 可能是临时文件)，
                   用于展示，避免暴露临时路径。
    """
    if cfg is None:
        cfg = config.load_config()
    ffprobe = cfg.get("ffprobe", "")
    ffmpeg = cfg.get("ffmpeg", "")
    if not ffprobe or not os.path.exists(ffprobe):
        raise RuntimeError("ffprobe 未配置或不存在，请先在设置中配置")
    if not os.path.exists(input_path):
        raise FileNotFoundError("输入文件不存在: %s" % input_path)

    info = ffprobe_stream_info(ffprobe, input_path)
    codec = info["codec"]
    if codec not in ("h264", "hevc"):
        raise ValueError("暂不支持的编码: %s (仅支持 h264 / hevc)" % codec)

    pid = uuid.uuid4().hex[:12]
    proj = Project(pid)
    proj.dir.mkdir(parents=True, exist_ok=True)

    ext = "264" if codec == "h264" else "265"
    es_path = proj.dir / ("stream.%s" % ext)

    # 判定裸流/封装：上传场景临时文件无正确后缀，优先用原始文件名的扩展名
    name_for_ext = original_name or input_path
    is_raw = _is_annexb_raw(name_for_ext, codec)
    if is_raw:
        # 裸流：直接拷贝进工程目录(保持可迁移，不依赖原始路径)
        shutil.copy2(input_path, es_path)
        source_kind = "raw"
    else:
        demux_to_annexb(ffmpeg, input_path, codec, str(es_path))
        source_kind = "demuxed"

    meta = {
        "id": pid,
        "input_path": os.path.abspath(input_path),
        "input_name": original_name or os.path.basename(input_path),
        "source_kind": source_kind,
        "elementary_stream": es_path.name,
        "elementary_bytes": os.path.getsize(es_path),
        **info,
    }
    proj.save_meta(meta)
    return meta


def get_project(project_id: str) -> Dict[str, object]:
    proj = Project(project_id)
    meta = proj.load_meta()
    if not meta:
        raise FileNotFoundError("工程不存在: %s" % project_id)
    return meta


def list_projects() -> List[Dict[str, object]]:
    out = []
    if WORKDIR.exists():
        for d in sorted(WORKDIR.iterdir()):
            mp = d / "project.json"
            if mp.exists():
                try:
                    out.append(json.loads(mp.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    pass
    return out
