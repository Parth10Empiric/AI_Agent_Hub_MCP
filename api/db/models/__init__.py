"""
Every model, imported.

This file is not tidiness - it is what makes migrations correct.

A model class registers itself on Base.metadata only when its module is
IMPORTED. Alembic autogenerate compares Base.metadata against the live
database. So a model file nobody imports is invisible to Alembic, which
will either never create the table or, worse, generate a migration that
DROPS it because metadata says it should not exist.

Import a new model here the moment you create it.
"""

from api.db.models.agent import Agent, AgentTool
from api.db.models.approval import PendingApproval
from api.db.models.audit import AuditLog
from api.db.models.conversation import Conversation, Message
from api.db.models.execution import ToolExecution
from api.db.models.oauth import OAuthState
from api.db.models.permission import AgentScope
from api.db.models.plugin import PluginConnection
from api.db.models.token import RefreshToken
from api.db.models.user import User

__all__ = [
    "Agent",
    "AgentScope",
    "AgentTool",
    "AuditLog",
    "Conversation",
    "Message",
    "OAuthState",
    "PendingApproval",
    "PluginConnection",
    "RefreshToken",
    "ToolExecution",
    "User",
]
