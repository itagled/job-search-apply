"""ORM models for all project tables, separated by topic.

Migrations (Alembic, in ``migrations/``) are generated from these models: they are the only
definition of each table.
"""

from jobradar.db.models.base import Base, JSONType, PKType
from jobradar.db.models.companies import (
    COMPANY_STAGES,
    REVIEW_DECISIONS,
    CompanyContact,
    PersonPost,
    TargetCompany,
)
from jobradar.db.models.jobs import JOB_STATUSES, JobPost
from jobradar.db.models.outreach import ApplicationContact, ApplicationEvent

__all__ = [
    "Base",
    "JSONType",
    "PKType",
    "JOB_STATUSES",
    "JobPost",
    "COMPANY_STAGES",
    "REVIEW_DECISIONS",
    "TargetCompany",
    "CompanyContact",
    "PersonPost",
    "ApplicationContact",
    "ApplicationEvent",
]
