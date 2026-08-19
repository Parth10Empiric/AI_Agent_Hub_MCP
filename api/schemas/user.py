from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    """
    A user, as the API returns it.

    A SEPARATE class from the User ORM model, deliberately. Returning
    the ORM object directly would serialise every column it happens to
    have - including password_hash. That is how password hashes end up
    in API responses, and it happens silently the moment someone adds a
    column.

    This class is an allow-list: a field that is not written here
    cannot be returned. Adding a secret column to the database can
    never leak it through this endpoint.
    """

    # Lets Pydantic read attributes off an ORM object rather than
    # requiring a dict.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    email_verified_at: datetime | None
    created_at: datetime
