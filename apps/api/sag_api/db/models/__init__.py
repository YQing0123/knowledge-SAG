"""ORM 模型聚合导入 —— 保证 Base.metadata 注册全部表。"""

from sag_api.db.models.agent import Agent, AgentBinding, Message, Thread
from sag_api.db.models.answer_model import AnswerModelProfile
from sag_api.db.models.dsh import DshMessage, DshSession
from sag_api.db.models.document import Document
from sag_api.db.models.folder_sync import FolderSyncConfig, FolderSyncEntry
from sag_api.db.models.job import Job
from sag_api.db.models.mcp_api_key import McpApiKey
from sag_api.db.models.processing_log import ProcessingLog
from sag_api.db.models.octx import (
    OctxAsset,
    OctxDocumentBinding,
    OctxInstallation,
    OctxOperationLease,
    OctxRelease,
    OctxSourceBinding,
    OctxTransfer,
)
from sag_api.db.models.setting import Setting
from sag_api.db.models.source import Source
from sag_api.db.models.universe import (
    ExplorationSession,
    ExplorationStep,
    UniverseDirtySource,
    UniverseOverview,
    UniversePartition,
)
from sag_api.db.models.user import User
from sag_api.project_management.models import Project, ProjectStage, Requirement, Task, TaskDailyNode, WorkReport

__all__ = [
    "Agent",
    "AgentBinding",
    "AnswerModelProfile",
    "Document",
    "DshMessage",
    "DshSession",
    "FolderSyncConfig",
    "FolderSyncEntry",
    "Job",
    "McpApiKey",
    "ProcessingLog",
    "Message",
    "OctxAsset",
    "OctxDocumentBinding",
    "OctxInstallation",
    "OctxOperationLease",
    "OctxRelease",
    "OctxSourceBinding",
    "OctxTransfer",
    "Setting",
    "Source",
    "Thread",
    "User",
    "Project",
    "ProjectStage",
    "Task",
    "TaskDailyNode",
    "Requirement",
    "WorkReport",
    "ExplorationSession",
    "ExplorationStep",
    "UniverseDirtySource",
    "UniverseOverview",
    "UniversePartition",
]
