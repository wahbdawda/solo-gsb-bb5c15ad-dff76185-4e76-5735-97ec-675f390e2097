"""FastAPI 应用:试算、保存配方版本、查询、JSON 导出。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlite3 import Connection

from .database import get_conn, get_version, init_db, list_recipes, save_version
from .models import SolveRequest, SolveResult
from .solver import solve


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时初始化数据库
    yield


app = FastAPI(
    title="水培营养液配比求解 API",
    description=(
        "提交若干浓缩液的元素浓度、可用体积与目标配方,线性规划最小化各元素"
        "相对目标的最大偏差;支持总量、单液上限与稀释水约束。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/solve", response_model=SolveResult, summary="试算(不落库)")
def api_solve(req: SolveRequest):
    try:
        result = solve(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@app.post("/api/recipes/save", summary="求解并保存为新版本")
def api_save(
    req: SolveRequest,
    note: Optional[str] = Query(None, description="版本备注"),
    conn: Connection = Depends(get_conn),
):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="保存配方必须提供 name")
    try:
        result = solve(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    saved = save_version(conn, name, req.model_dump(), result, note=note)
    return {**saved, "result": result}


@app.get("/api/recipes", summary="配方列表")
def api_list(conn: Connection = Depends(get_conn)):
    return list_recipes(conn)


@app.get("/api/recipes/{name}", summary="查询某配方(默认最新版本)")
def api_get(
    name: str,
    version: Optional[int] = Query(None, ge=1),
    conn: Connection = Depends(get_conn),
):
    data = get_version(conn, name, version)
    if data is None:
        raise HTTPException(status_code=404, detail="配方或版本不存在")
    return data


@app.get("/api/recipes/{name}/export", summary="导出某版本为 JSON 文件")
def api_export(
    name: str,
    version: Optional[int] = Query(None, ge=1),
    conn: Connection = Depends(get_conn),
):
    data = get_version(conn, name, version)
    if data is None:
        raise HTTPException(status_code=404, detail="配方或版本不存在")
    v = data["version"]
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "recipe"
    return JSONResponse(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}-v{v}.json\"; "
                f"filename*=UTF-8''{quote(f'{name}-v{v}.json')}"
            )
        },
    )
