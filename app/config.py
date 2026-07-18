"""外部工具路径的探测、校验与持久化。

管理 4 个外部二进制的路径：ffmpeg / ffprobe / JM 解码器(ldecod) / HM 分析器。
- 首次启动自动探测(PATH + 仓库内置默认位置)。
- 持久化到 ~/.streamtool/config.json，可被前端覆盖。
- verify() 逐个运行 `-version` 类命令，返回可用性与版本，供前端亮红/绿灯。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

CONFIG_DIR = Path(os.path.expanduser("~")) / ".streamtool"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _candidate_roots():
    """搜索 JM/HM 的候选根目录，兼容"内嵌仓库"与"独立部署"两种布局。

    顺序：
    1. 环境变量 JMHM_ROOT（显式指定，最高优先级）。
    2. 从 streamtool/ 向上逐级(最多 4 层)，兼容与 JM/HM 同级或作为子目录。
    3. 当前工作目录。
    """
    roots = []
    env_root = os.environ.get("JMHM_ROOT")
    if env_root:
        roots.append(Path(env_root))
    here = Path(__file__).resolve()
    for up in range(1, 5):
        try:
            roots.append(here.parents[up])
        except IndexError:
            break
    roots.append(Path.cwd())
    # 去重保序
    seen, uniq = set(), []
    for r in roots:
        rp = str(r)
        if rp not in seen:
            seen.add(rp)
            uniq.append(r)
    return uniq

# 工具键 -> (PATH 中可能的命令名, 仓库内置默认相对路径列表)
TOOL_DEFAULTS = {
    "ffmpeg": (["ffmpeg"], []),
    "ffprobe": (["ffprobe"], []),
    "jm_ldecod": (
        ["ldecod", "ldecod.exe"],
        ["JM/bin/ldecod.exe"],
    ),
    "hm_analyser": (
        ["TAppDecoderAnalyserStatic", "TAppDecoderStatic"],
        [
            "HM/bin/TAppDecoderAnalyserStatic",
            "HM/bin/TAppDecoderStatic",
            "HM/bin/TAppDecoderAnalyserStaticd",
        ],
    ),
}

# 每个工具用于自检的命令行参数 (只为拿版本/退出码，不做实际解码)
_VERIFY_ARGS = {
    "ffmpeg": ["-version"],
    "ffprobe": ["-version"],
    # JM/HM 无标准 --version：给个无效输入让它打印 banner 后退出，取 banner 当版本
    "jm_ldecod": ["-v"],
    "hm_analyser": [],
}


def _which_in_repo(rel_paths) -> Optional[str]:
    for root in _candidate_roots():
        for rel in rel_paths:
            p = root / rel
            if p.exists() and os.access(p, os.X_OK):
                return str(p)
    return None


def detect_tool(key: str) -> Optional[str]:
    """探测单个工具：先查 PATH，再查仓库内置默认位置。"""
    names, repo_defaults = TOOL_DEFAULTS[key]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return _which_in_repo(repo_defaults)


def default_config() -> Dict[str, str]:
    return {key: (detect_tool(key) or "") for key in TOOL_DEFAULTS}


def load_config() -> Dict[str, str]:
    """读取持久化配置；缺失的键用自动探测补齐。"""
    cfg = default_config()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in cfg and v:
                    cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: Dict[str, str]) -> Dict[str, str]:
    """只保留已知键，写入磁盘。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    clean = {k: str(cfg.get(k, "") or "") for k in TOOL_DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    return clean


def _extract_version(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def verify_tool(key: str, path: str) -> Dict[str, object]:
    """运行工具取版本，返回 {ok, version, error}。"""
    result = {"ok": False, "version": "", "error": ""}
    if not path:
        result["error"] = "未配置路径"
        return result
    if not os.path.exists(path):
        result["error"] = "文件不存在: %s" % path
        return result
    if not os.access(path, os.X_OK):
        result["error"] = "文件不可执行: %s" % path
        return result
    args = _VERIFY_ARGS.get(key, [])
    try:
        proc = subprocess.run(
            [path] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        result["version"] = _extract_version(out)
        # ffmpeg/ffprobe 返回 0；JM/HM 打印 banner 后可能非 0，但只要有 banner 就算可用
        if key in ("ffmpeg", "ffprobe"):
            result["ok"] = proc.returncode == 0
        else:
            result["ok"] = bool(result["version"])
        if not result["ok"] and not result["error"]:
            result["error"] = "退出码 %d" % proc.returncode
    except subprocess.TimeoutExpired:
        result["error"] = "执行超时"
    except OSError as e:
        result["error"] = str(e)
    return result


def verify_all(cfg: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, object]]:
    if cfg is None:
        cfg = load_config()
    return {key: verify_tool(key, cfg.get(key, "")) for key in TOOL_DEFAULTS}
