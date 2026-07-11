"""定时同步调度器状态接口。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.sync.scheduler import get_scheduler_state

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status")
async def scheduler_status():
    """获取定时同步调度器状态：各任务今日完成/重试/上次同步时间，及配置钟点。"""
    return JSONResponse(content=get_scheduler_state())
