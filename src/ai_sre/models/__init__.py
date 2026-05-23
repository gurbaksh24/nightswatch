"""SQLAlchemy ORM models. One model per file. See docs/04-data-model.md.

Note: the queue's tables are owned by Procrastinate, not by us. They live in
the same Postgres but are not in this package and are not touched by our
Alembic migrations. See `queue/` for the abstraction layer.
"""

from ai_sre.db import Base  # re-export so Alembic env discovers it
from ai_sre.models.alert import Alert
from ai_sre.models.api_key import ApiKey
from ai_sre.models.feedback import Feedback
from ai_sre.models.integration import Integration
from ai_sre.models.investigation import Investigation, InvestigationStage, ToolCall
from ai_sre.models.knowledge import KnowledgeChunk, KnowledgeDoc
from ai_sre.models.report import Report
from ai_sre.models.service import MetricCatalogEntry, Service, ServiceDependency
from ai_sre.models.tenant import Tenant

__all__ = [
    "Alert",
    "ApiKey",
    "Base",
    "Feedback",
    "Integration",
    "Investigation",
    "InvestigationStage",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "MetricCatalogEntry",
    "Report",
    "Service",
    "ServiceDependency",
    "Tenant",
    "ToolCall",
]
