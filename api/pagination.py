from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class InvalidCursor(Exception):
    """
    The cursor was not produced by this API.

    Corrupt, truncated, or hand-written. Always a 422 - the client sent
    something malformed, which is different from asking for a page that
    does not exist.
    """


@dataclass(frozen=True, slots=True)
class Cursor:
    """
    A position in a result set: the (created_at, id) of the last row
    the client saw.

    WHY A PAIR AND NOT JUST created_at

    Timestamps tie. Two messages written in the same transaction can
    share a created_at to the microsecond, and a `<` on a non-unique
    column either skips rows or repeats them. The id breaks the tie,
    and because it is unique the pair is a total order.

    WHY NOT AN OFFSET

    OFFSET counts from the start of the result set, so every INSERT
    shifts the window:

        t=0  ?limit=3           -> [m5, m4, m3]
        t=1  m6 is inserted
        t=2  ?offset=3&limit=3  -> [m3, m2, m1]
                                     ^^ m3 again

    Chat inserts constantly, so that is the normal case, not an edge
    case. A cursor names a boundary instead of counting, and new rows
    do not move it.
    """

    created_at: datetime
    row_id: uuid.UUID

    def encode(self) -> str:
        """
        Serialise to an opaque string.

        Opaque ON PURPOSE. A readable `?after_id=5` invites clients to
        construct their own, and then the sort order can never change
        without breaking them. An unreadable token is a contract that
        says: give this back to me, do not interpret it.

        urlsafe_b64 because ordinary base64 contains "+" and "/", which
        a query string would need escaped.
        """

        payload = json.dumps(
            {"t": self.created_at.isoformat(), "i": str(self.row_id)},
            separators=(",", ":"),
        )

        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, raw: str) -> Cursor:
        try:
            # Padding is stripped when encoding to keep the token tidy;
            # b64decode needs it back. Adding three "=" is always
            # enough and never too many - the decoder ignores extras.
            padded = raw + "=" * (-len(raw) % 4)

            data = json.loads(base64.urlsafe_b64decode(padded))

            return cls(
                created_at=datetime.fromisoformat(data["t"]),
                row_id=uuid.UUID(data["i"]),
            )

        except Exception as exc:
            raise InvalidCursor(str(exc)) from exc


class Page(BaseModel, Generic[T]):
    """
    One page of results.

    has_more is computed by fetching limit+1 rows and discarding the
    extra. The alternative - a COUNT(*) on every page - is an extra
    full scan to answer a question the client only needs a boolean for.
    """

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


def build_page(
    rows: list,
    limit: int,
    key: callable,
) -> tuple[list, str | None, bool]:
    """
    Turn limit+1 rows into (page, next_cursor, has_more).

    `key` extracts (created_at, id) from a row, so this works for
    messages, conversations, or anything else ordered the same way.
    """

    has_more = len(rows) > limit

    page = rows[:limit]

    next_cursor = None

    if has_more and page:
        created_at, row_id = key(page[-1])
        next_cursor = Cursor(created_at, row_id).encode()

    return page, next_cursor, has_more
