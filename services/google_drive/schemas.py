from pydantic import BaseModel, Field


class GoogleDriveFile(BaseModel):
    """Represents a Google Drive file."""

    id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    web_view_link: str | None = None


class GoogleDriveSearchResult(BaseModel):
    """
    One PAGE of Google Drive files.

    A page, not an answer. Drive returns at most `page_size` items per
    call and hands back a token for the rest, and a result that does not
    say so is the most dangerous shape this file could have: it looks
    complete.

    That is not hypothetical. Asked to list folders, the agent read a
    truncated page, could not tell it was truncated, and either
    presented it as the whole Drive or refused to answer at all because
    the docstring gave it no way to continue. Both are the same defect -
    the result did not carry its own limits.
    """

    files: list[GoogleDriveFile] = Field(default_factory=list)

    # HOW MANY ARE IN THIS PAGE. Not how many exist.
    #
    # Named `total` before it could paginate, when the two were the same
    # number. They are not any more, and the old name reads as "the
    # total in your Drive" to anything - model or person - that does not
    # read this comment. `returned` cannot be misread.
    #
    # `total` stays as an alias so stored results and older callers keep
    # working; it is the same value it always was.
    returned: int = 0
    total: int = 0

    # The cursor for the next page, straight from Drive. None means this
    # really was the last page.
    next_page_token: str | None = None

    # The same fact as a plain boolean, because that is the one a model
    # reliably acts on. "next_page_token": "abc" requires knowing what a
    # page token is; "has_more": true does not.
    has_more: bool = False


class GoogleDrivePermission(BaseModel):
    """Represents a Google Drive permission."""

    id: str
    permission_type: str
    role: str
    email_address: str | None = None
    display_name: str | None = None


class GoogleDrivePermissionResult(BaseModel):
    """Result containing Drive permissions."""

    permissions: list[GoogleDrivePermission] = Field(
        default_factory=list
    )
    total: int = 0


class GoogleDriveFileContent(BaseModel):
    """Represents readable file content."""

    file: GoogleDriveFile
    content: str
    encoding: str = "utf-8"