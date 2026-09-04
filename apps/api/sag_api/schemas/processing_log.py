from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProcessingLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    document_id: str | None
    job_id: str | None
    filename: str | None
    stage: str
    level: str
    message: str
    created_at: datetime
