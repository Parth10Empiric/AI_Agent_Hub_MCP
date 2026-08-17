import io
from pathlib import Path
from typing import Any
import contextlib
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .errors import (
    GoogleDriveAPIError,
    GoogleDriveAuthenticationError,
    GoogleDriveFileNotFoundError,
    GoogleDriveFileOperationError,
    GoogleDriveUnsupportedFileTypeError,
)

from .schemas import (
    GoogleDriveFile,
    GoogleDriveFileContent,
    GoogleDrivePermission,
    GoogleDrivePermissionResult,
    GoogleDriveSearchResult,
)

class GoogleDriveService:
    """Service layer for interacting with Google Drive."""

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        credentials_path: str = "credentials.json",
        token_path: str = "token.json",
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)

        self._credentials: Credentials | None = None
        self._service: Resource | None = None

    def _authenticate(self) -> Credentials:
        """Authenticate the user with Google OAuth."""
        
        try:

            credentials = None

            # Load previously generated token
            if self.token_path.exists():
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_path),
                    self.SCOPES,
                )

            # Refresh or create credentials when necessary
            if not credentials or not credentials.valid:

                if credentials and credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())

                else:
                    if not self.credentials_path.exists():
                        raise GoogleDriveAuthenticationError(
                            f"Google credentials file not found: "
                            f"{self.credentials_path}"
                        )

                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_path),
                        self.SCOPES,
                    )

                    with contextlib.redirect_stdout(sys.stderr):
                        credentials = flow.run_local_server(port=0)

                # Save token for future requests
                self.token_path.write_text(
                    credentials.to_json(),
                    encoding="utf-8",
                )

            return credentials
        
        except GoogleDriveAuthenticationError:
            raise
        
        except Exception as exc:
            raise GoogleDriveAuthenticationError(
                f"Google Drive authentication failed: {exc}"
            ) from exc

    def _get_service(self) -> Resource:
        """Create and return the Google Drive API client."""

        if self._service is None:
            self._credentials = self._authenticate()

            self._service = build(
                "drive",
                "v3",
                credentials=self._credentials,
            )

        return self._service

    def _create_temp_file(self, content: str) -> str:
        """Create a temporary file containing text content."""

        import tempfile

        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            suffix=".txt",
        )

        temp_file.write(content)
        temp_file.close()

        return temp_file.name

    def search_files(
        self,
        query: str,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Search files in Google Drive by name."""

        try: 
            
            service = self._get_service()

            escaped_query = query.replace("'", "\\'")

            drive_query = (
                f"name contains '{escaped_query}' "
                "and trashed = false"
            )

            response = (
                service.files()
                .list(
                    q=drive_query,
                    pageSize=page_size,
                    fields=(
                        "files("
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                        ")"
                    ),
                )
                .execute()
            )

            files = [
                GoogleDriveFile(
                    id=file["id"],
                    name=file["name"],
                    mime_type=file["mimeType"],
                    modified_time=file.get("modifiedTime"),
                    web_view_link=file.get("webViewLink"),
                )
                for file in response.get("files", [])
            ]

            return GoogleDriveSearchResult(
                files=files,
                total=len(files),
            )
            
        except HttpError as exc:
            raise GoogleDriveAPIError(
                f"Google Drive API request failed: {exc}"
            ) from exc
            
            
    def get_file(self, file_id: str) -> GoogleDriveFile:
        """Get metadata for a Google Drive file by ID."""

        try:
            service = self._get_service()

            file = (
                service.files()
                .get(
                    fileId=file_id,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=file["id"],
                name=file["name"],
                mime_type=file["mimeType"],
                modified_time=file.get("modifiedTime"),
                web_view_link=file.get("webViewLink"),
            )

        except HttpError as exc:
            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to get Google Drive file: {exc}"
            ) from exc

    def list_folder(
        self,
        folder_id: str,
        page_size: int = 100,
    ) -> GoogleDriveSearchResult:
        """List files and folders inside a Google Drive folder."""

        try:
            service = self._get_service()

            drive_query = (
                f"'{folder_id}' in parents "
                "and trashed = false"
            )

            response = (
                service.files()
                .list(
                    q=drive_query,
                    pageSize=page_size,
                    orderBy="folder,name",
                    fields=(
                        "files("
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                        ")"
                    ),
                )
                .execute()
            )

            files = [
                GoogleDriveFile(
                    id=file["id"],
                    name=file["name"],
                    mime_type=file["mimeType"],
                    modified_time=file.get("modifiedTime"),
                    web_view_link=file.get("webViewLink"),
                )
                for file in response.get("files", [])
            ]

            return GoogleDriveSearchResult(
                files=files,
                total=len(files),
            )

        except HttpError as exc:
            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive folder not found: {folder_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to list Google Drive folder: {exc}"
            ) from exc
            
    def read_file(self, file_id: str) -> GoogleDriveFileContent:
        """
        Read text content from a Google Drive file.

        Supports:
        - Google Docs
        - Google Sheets
        - Google Slides
        - Plain text files
        - Markdown
        - CSV
        """

        try:
            service = self._get_service()

            file = (
                service.files()
                .get(
                    fileId=file_id,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            mime_type = file["mimeType"]

            drive_file = GoogleDriveFile(
                id=file["id"],
                name=file["name"],
                mime_type=mime_type,
                modified_time=file.get("modifiedTime"),
                web_view_link=file.get("webViewLink"),
            )

            # Google Docs
            if mime_type == "application/vnd.google-apps.document":

                response = service.files().export(
                    fileId=file_id,
                    mimeType="text/plain",
                ).execute()

                content = response.decode("utf-8")

            # Google Sheets
            elif mime_type == "application/vnd.google-apps.spreadsheet":

                response = service.files().export(
                    fileId=file_id,
                    mimeType="text/csv",
                ).execute()

                content = response.decode("utf-8")

            # Google Slides
            elif mime_type == "application/vnd.google-apps.presentation":

                response = service.files().export(
                    fileId=file_id,
                    mimeType="text/plain",
                ).execute()

                content = response.decode("utf-8")

            # Normal text files
            elif mime_type.startswith("text/"):

                response = service.files().get(
                    fileId=file_id,
                    alt="media",
                ).execute()

                if isinstance(response, bytes):
                    content = response.decode(
                        "utf-8",
                        errors="replace",
                    )
                else:
                    content = str(response)

            else:
                raise GoogleDriveUnsupportedFileTypeError(
                    f"Cannot read file type directly: {mime_type}"
                )

            return GoogleDriveFileContent(
                file=drive_file,
                content=content,
            )

        except GoogleDriveUnsupportedFileTypeError:
            raise

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to read Google Drive file: {exc}"
            ) from exc
            
    def download_file(
        self,
        file_id: str,
        output_path: str,
    ) -> str:
        """Download a Google Drive file to the local filesystem."""

        try:
            service = self._get_service()

            file = service.files().get(
                fileId=file_id,
                fields="id,name,mimeType",
            ).execute()

            mime_type = file["mimeType"]

            output = Path(output_path)
            output.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            # Google Workspace files must be exported.
            export_types = {
                "application/vnd.google-apps.document": (
                    "application/pdf"
                ),
                "application/vnd.google-apps.spreadsheet": (
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                "application/vnd.google-apps.presentation": (
                    "application/pdf"
                ),
            }

            if mime_type in export_types:

                response = service.files().export(
                    fileId=file_id,
                    mimeType=export_types[mime_type],
                ).execute()

                output.write_bytes(response)

            else:

                response = service.files().get(
                    fileId=file_id,
                    alt="media",
                ).execute()

                output.write_bytes(response)

            return str(output)

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to download file: {exc}"
            ) from exc

    def create_file(
        self,
        name: str,
        mime_type: str = "text/plain",
        content: str | None = None,
        parent_id: str | None = None,
    ) -> GoogleDriveFile:
        """Create a file in Google Drive."""

        try:
            service = self._get_service()

            metadata: dict[str, Any] = {
                "name": name,
                "mimeType": mime_type,
            }

            if parent_id:
                metadata["parents"] = [parent_id]

            media_body = None

            if content is not None:

                media_body = MediaFileUpload(
                    self._create_temp_file(content),
                    mimetype=mime_type,
                    resumable=False,
                )

            file = (
                service.files()
                .create(
                    body=metadata,
                    media_body=media_body,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=file["id"],
                name=file["name"],
                mime_type=file["mimeType"],
                modified_time=file.get("modifiedTime"),
                web_view_link=file.get("webViewLink"),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to create file: {exc}"
            ) from exc

    def upload_file(
        self,
        file_path: str,
        parent_id: str | None = None,
    ) -> GoogleDriveFile:
        """Upload a local file to Google Drive."""

        try:
            service = self._get_service()

            path = Path(file_path)

            if not path.exists():
                raise FileNotFoundError(
                    f"Local file not found: {file_path}"
                )

            metadata: dict[str, Any] = {
                "name": path.name,
            }

            if parent_id:
                metadata["parents"] = [parent_id]

            media = MediaFileUpload(
                str(path),
                resumable=True,
            )

            file = (
                service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=file["id"],
                name=file["name"],
                mime_type=file["mimeType"],
                modified_time=file.get("modifiedTime"),
                web_view_link=file.get("webViewLink"),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to upload file: {exc}"
            ) from exc

    def update_file(
        self,
        file_id: str,
        name: str | None = None,
        content: str | None = None,
    ) -> GoogleDriveFile:
        """Update a Google Drive file."""

        try:
            service = self._get_service()

            metadata: dict[str, Any] = {}

            if name is not None:
                metadata["name"] = name

            media_body = None

            if content is not None:

                temp_path = self._create_temp_file(content)

                media_body = MediaFileUpload(
                    temp_path,
                    mimetype="text/plain",
                    resumable=False,
                )

            file = (
                service.files()
                .update(
                    fileId=file_id,
                    body=metadata if metadata else None,
                    media_body=media_body,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=file["id"],
                name=file["name"],
                mime_type=file["mimeType"],
                modified_time=file.get("modifiedTime"),
                web_view_link=file.get("webViewLink"),
            )

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveFileOperationError(
                f"Failed to update file: {exc}"
            ) from exc

    def move_file(
        self,
        file_id: str,
        new_parent_id: str,
    ) -> GoogleDriveFile:
        """Move a file into another Google Drive folder."""

        try:
            service = self._get_service()

            file = service.files().get(
                fileId=file_id,
                fields="id,name,mimeType,parents",
            ).execute()

            previous_parents = ",".join(
                file.get("parents", [])
            )

            updated_file = (
                service.files()
                .update(
                    fileId=file_id,
                    addParents=new_parent_id,
                    removeParents=previous_parents,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=updated_file["id"],
                name=updated_file["name"],
                mime_type=updated_file["mimeType"],
                modified_time=updated_file.get(
                    "modifiedTime"
                ),
                web_view_link=updated_file.get(
                    "webViewLink"
                ),
            )

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveFileOperationError(
                f"Failed to move file: {exc}"
            ) from exc

    def delete_file(self, file_id: str) -> bool:
        """Move a Google Drive file to trash."""

        try:
            service = self._get_service()

            service.files().update(
                fileId=file_id,
                body={
                    "trashed": True,
                },
            ).execute()

            return True

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveFileOperationError(
                f"Failed to delete file: {exc}"
            ) from exc

    def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> GoogleDriveFile:
        """Create a folder in Google Drive."""

        try:
            service = self._get_service()

            metadata: dict[str, Any] = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }

            if parent_id:
                metadata["parents"] = [parent_id]

            folder = (
                service.files()
                .create(
                    body=metadata,
                    fields=(
                        "id,"
                        "name,"
                        "mimeType,"
                        "webViewLink,"
                        "modifiedTime"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=folder["id"],
                name=folder["name"],
                mime_type=folder["mimeType"],
                modified_time=folder.get("modifiedTime"),
                web_view_link=folder.get("webViewLink"),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to create folder: {exc}"
            ) from exc

    def list_permissions(
        self,
        file_id: str,
    ) -> GoogleDrivePermissionResult:
        """List permissions for a Google Drive file."""

        try:
            service = self._get_service()

            response = (
                service.permissions()
                .list(
                    fileId=file_id,
                    fields=(
                        "permissions("
                        "id,"
                        "type,"
                        "role,"
                        "emailAddress,"
                        "displayName"
                        ")"
                    ),
                )
                .execute()
            )

            permissions = [
                GoogleDrivePermission(
                    id=permission["id"],
                    permission_type=permission["type"],
                    role=permission["role"],
                    email_address=permission.get(
                        "emailAddress"
                    ),
                    display_name=permission.get(
                        "displayName"
                    ),
                )
                for permission in response.get(
                    "permissions",
                    [],
                )
            ]

            return GoogleDrivePermissionResult(
                permissions=permissions,
                total=len(permissions),
            )

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to list permissions: {exc}"
            ) from exc

    def create_permission(
        self,
        file_id: str,
        email_address: str,
        role: str = "reader",
    ) -> GoogleDrivePermission:
        """Share a Google Drive file with a user."""

        allowed_roles = {
            "reader",
            "commenter",
            "writer",
        }

        if role not in allowed_roles:
            raise ValueError(
                f"Invalid role '{role}'. "
                f"Allowed roles: {allowed_roles}"
            )

        try:
            service = self._get_service()

            permission_body = {
                "type": "user",
                "role": role,
                "emailAddress": email_address,
            }

            permission = (
                service.permissions()
                .create(
                    fileId=file_id,
                    body=permission_body,
                    fields=(
                        "id,"
                        "type,"
                        "role,"
                        "emailAddress,"
                        "displayName"
                    ),
                    sendNotificationEmail=True,
                )
                .execute()
            )

            return GoogleDrivePermission(
                id=permission["id"],
                permission_type=permission["type"],
                role=permission["role"],
                email_address=permission.get(
                    "emailAddress"
                ),
                display_name=permission.get(
                    "displayName"
                ),
            )

        except HttpError as exc:

            if exc.resp.status == 404:
                raise GoogleDriveFileNotFoundError(
                    f"Google Drive file not found: {file_id}"
                ) from exc

            raise GoogleDriveAPIError(
                f"Failed to create permission: {exc}"
            ) from exc
        
