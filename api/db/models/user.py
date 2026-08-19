from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base, TimestampMixin, UUIDMixin

# Imported for type checking only.
#
# A real import here would be circular: user imports agent, agent
# imports user. SQLAlchemy does not need the class object - it resolves
# the string "Agent" through its own registry of mapped classes, which
# is populated when the module is imported by api/db/models/__init__.py.
if TYPE_CHECKING:
    from api.db.models.agent import Agent
    from api.db.models.plugin import PluginConnection


class User(UUIDMixin, TimestampMixin, Base):
    """
    An Agent Hub account.
    """

    __tablename__ = "users"

    # 320 is the maximum length of an email address per RFC 5321:
    # 64 for the local part, 1 for the @, 255 for the domain.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)

    # The HASH, never the password. argon2id produces roughly 100
    # characters; 255 leaves room to change algorithm without a
    # migration.
    password_hash: Mapped[str] = mapped_column(String(255))

    # Mapped[str | None] is how nullability is expressed in SQLAlchemy
    # 2.0. There is no nullable=False argument any more - the type
    # annotation IS the constraint.
    full_name: Mapped[str | None] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
    )

    # NULL means "not verified yet". A separate is_verified boolean
    # would answer whether, but never when - and the when is what you
    # need when investigating an account.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # cascade="all, delete-orphan" is an ORM rule: deleting a User
    # through the session deletes its agents too.
    #
    # ondelete="CASCADE" on the foreign key (see agent.py) is a
    # DATABASE rule that applies even to a raw SQL DELETE.
    #
    # They are different mechanisms. Setting both means the behaviour
    # is the same whether the delete comes through the ORM or not.
    agents: Mapped[list["Agent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )

    plugin_connections: Mapped[list["PluginConnection"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )
