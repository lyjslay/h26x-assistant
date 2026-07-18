"""FastAPI 应用入口 —— P1：配置 / 工程 / 码率 三组 REST 接口 + 静态前端。"""
import os
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bitrate, config, project

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
    try:
        meta = project.create_project(body.input_path)
        return {"project": meta}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/api/health")
def api_health():
    return {"status": "ok", "phase": "P1"}


# ---------------- 静态前端 ----------------
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    idx = WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"message": "前端未找到，请检查 web/index.html"}
