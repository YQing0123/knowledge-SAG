from fastapi import APIRouter

from sag_api.api.v1 import (
    activity,
    agents,
    attachments,
    auth,
    dify,
    documents,
    dsh,
    folder_sync,
    insights,
    jobs,
    knowledge,
    octx,
    openai,
    processing_logs,
    search,
    sources,
    storage_bootstrap,  # [storage-bootstrap]
    system,
    universe,
)
from sag_api.project_management.router import router as project_management_router

api_router = APIRouter(prefix="/api/v1")
for _module in (
    auth,
    dify,
    sources,
    documents,
    dsh,
    folder_sync,
    insights,
    knowledge,
    jobs,
    search,
    agents,
    openai,
    processing_logs,
    activity,
    attachments,
    storage_bootstrap,  # [storage-bootstrap]
    system,
    universe,
    octx,
):
    api_router.include_router(_module.router)
api_router.include_router(project_management_router)
api_router.include_router(search.global_router)

__all__ = ["api_router"]
