"""FastAPI 应用入口 —— P1：配置 / 工程 / 码率 三组 REST 接口 + 静态前端。"""
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bitrate, config, overlay, preview, project, syntax
from .decoder import DecodeError

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="码流分析工具", version="0.1.0-P1")


# ---------------- 配置 ----------------
class ConfigBody(BaseModel):
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None
    jm_ldecod: Optional[str] = None
    hm_analyser: Optional[str] = None


@app.get("/api/config")
def api_get_config():
    cfg = config.load_config()
    return {"config": cfg, "verify": config.verify_all(cfg)}


@app.post("/api/config")
def api_set_config(body: ConfigBody):
    cfg = config.load_config()
    for k, v in body.dict().items():
        if v is not None:
            cfg[k] = v
    saved = config.save_config(cfg)
    return {"config": saved, "verify": config.verify_all(saved)}


@app.post("/api/config/verify")
def api_verify_config():
    cfg = config.load_config()
    return {"verify": config.verify_all(cfg)}


# ---------------- 工程 ----------------
class ProjectBody(BaseModel):
    input_path: str


@app.post("/api/project")
def api_create_project(body: ProjectBody):
    """按服务器本机绝对路径建立工程(高级/大文件免拷贝场景)。"""
    try:
        meta = project.create_project(body.input_path)
        return {"project": meta}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/upload")
async def api_upload_project(file: UploadFile = File(...)):
    """浏览器上传本机文件建立工程(默认方式，流式落盘避免占内存)。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")
    tmp_dir = project.WORKDIR / ".uploads" / uuid.uuid4().hex[:8]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(file.filename)
    tmp_path = tmp_dir / safe_name
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        meta = project.create_project(str(tmp_path), original_name=safe_name)
        return {"project": meta}
    except (ValueError,) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/projects")
def api_list_projects():
    return {"projects": project.list_projects()}


@app.get("/api/project/{project_id}")
def api_get_project(project_id: str):
    try:
        return {"project": project.get_project(project_id)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------- 码率 ----------------
@app.get("/api/project/{project_id}/bitrate")
def api_bitrate(project_id: str):
    try:
        return bitrate.analyze_bitrate(project_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------- 语法解析 (P2) ----------------
@app.get("/api/project/{project_id}/syntax")
def api_syntax_overview(project_id: str):
    """帧列表总览(触发解码+解析，惰性缓存)。"""
    try:
        return syntax.syntax_overview(project_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{project_id}/frame/{index}/syntax")
def api_frame_syntax(project_id: str, index: int, mbs: bool = True):
    """单帧完整语法树(NAL 字段 + 宏块字段)。mbs=false 可省略宏块。"""
    try:
        return syntax.frame_syntax(project_id, index, include_mbs=mbs)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------- 帧预览 + 叠加 (P3) ----------------
@app.get("/api/project/{project_id}/framemap")
def api_framemap(project_id: str):
    """显示序 ↔ 解码序 ↔ POC 映射。"""
    try:
        return preview.frame_map_for(project_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{project_id}/frame/{display_index}/image")
def api_frame_image(project_id: str, display_index: int):
    """按显示序取某帧 PNG。"""
    try:
        png = preview.ensure_frame_png(project_id, display_index)
        return FileResponse(str(png), media_type="image/png")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{project_id}/frame/{decode_index}/overlay")
def api_frame_overlay(project_id: str, decode_index: int):
    """按解码序取某帧叠加数据(分割/QP/MV/子块配色)。"""
    try:
        return overlay.build_frame_overlay(project_id, decode_index)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{project_id}/refgraph")
def api_refgraph(project_id: str):
    """帧间参考关系图。"""
    try:
        return overlay.build_reference_graph(project_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def api_health():
    return {"status": "ok", "phase": "P3"}


# ---------------- 静态前端 ----------------
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    idx = WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"message": "前端未找到，请检查 web/index.html"}
