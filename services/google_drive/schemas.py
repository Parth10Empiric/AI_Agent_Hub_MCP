from pydantic import BaseModel, Field


class GoogleDriveFile(BaseModel):
    """Represents a Google Drive file."""

    id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    web_view_link: str | None = None


class GoogleDriveSearchResult(BaseModel):
    """Result returned from a Google Drive file search."""

    files: list[GoogleDriveFile] = Field(default_factory=list)
    total: int = 0


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