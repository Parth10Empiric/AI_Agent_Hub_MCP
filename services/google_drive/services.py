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

from config.settings import resolve_path, settings

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

def _interactive_auth_allowed() -> bool:
    """
    May this process open a browser to authenticate?

    Only outside production, and never when the caller supplied a
    token. run_local_server() opens a consent window ON THE MACHINE
    RUNNING THE CODE and blocks until somebody clicks it - which on a
    server means a shared subprocess hangs, holding the MCP lock, until
    it times out.

    That is not hypothetical here: the comment in __init__ records a
    21.9-second Drive call caused by exactly this path being reached
    when a token file could not be found.
    """

    import os

    environment = os.getenv("MCP_ENVIRONMENT", "development")

    return environment.strip().lower() != "production"


class GoogleDriveService:
    """Service layer for interacting with Google Drive."""

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """
        PHASE 5.5: `access_token` is the calling user's own token.

        Given one, this service never touches credentials.json,
        token.json, or a browser - it builds Credentials directly and
        talks to Drive as that user. Omitted, it falls back to the
        file-based flow, which is what the CLI and local development
        still use.
        """
        # Absolute paths from config, not bare relative strings.
        #
        # These used to default to "credentials.json" and "token.json",
        # which Path() resolves against the CURRENT WORKING DIRECTORY -
        # and this service runs inside the MCP server subprocess, whose
        # working directory is whatever the client happened to be
        # started from.
        #
        # The result: the saved token was never found again, so
        # _authenticate() fell through to run_local_server() and opened
        # a browser consent window on EVERY Drive call. That is what
        # made a single search_files take 21.9 seconds and then fail.
        #
        # google_calendar/services.py already did this correctly with
        # BASE_DIR - Drive was the odd one out.
        self.credentials_path = resolve_path(
            credentials_path or settings.google_credentials_path
        )
        self.token_path = resolve_path(
            token_path or settings.google_token_path
        )

        self._access_token = access_token

        self._credentials: Credentials | None = None
        self._service: Resource | None = None

    def _authenticate(self) -> Credentials:
        """Authenticate the user with Google OAuth."""

        # PHASE 5.5: a token supplied by the backend wins, and short
        # circuits everything below.
        #
        # No file is read, no file is written and no browser is opened.
        # The token was already refreshed by the backend before this
        # call (api/services/oauth_service.access_token), so there is
        # nothing to refresh here either - which is deliberate: two
        # processes refreshing the same Google credential would race,
        # and Google invalidates the old refresh token when it issues a
        # new one.
        if self._access_token:
            return Credentials(token=self._access_token, scopes=self.SCOPES)

        if not _interactive_auth_allowed():
            raise GoogleDriveAuthenticationError(
                "No Google credentials for this request. Connect Google "
                "Drive in Agent Hub and try again."
            )

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
        

    # =================================================================
    # TRASH - THE REVERSIBLE HALF OF DELETING
    # =================================================================
    #
    # Drive has two different deletes and only one of them is
    # survivable:
    #
    #   trash_file   moves it to the bin. Recoverable for 30 days.
    #   delete_file  erases it immediately. Nothing to recover.
    #
    # Only the second existed here, so "delete that file" had exactly
    # one meaning and it was the unrecoverable one. Adding the
    # reversible option is what lets an agent do what was asked
    # without doing something that cannot be taken back.

    def trash_file(self, file_id: str) -> dict[str, Any]:
        """Move a file to the bin, where it can be restored."""

        try:
            service = self._get_service()

            result = (
                service.files()
                .update(
                    fileId=file_id,
                    body={"trashed": True},
                    fields="id,name,trashed",
                )
                .execute()
            )

            return {
                "id": result["id"],
                "name": result.get("name"),
                "trashed": result.get("trashed", True),
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to trash file: {exc}"
            ) from exc

    def restore_file(self, file_id: str) -> dict[str, Any]:
        """Restore a file from the bin."""

        try:
            service = self._get_service()

            result = (
                service.files()
                .update(
                    fileId=file_id,
                    body={"trashed": False},
                    fields="id,name,trashed",
                )
                .execute()
            )

            return {
                "id": result["id"],
                "name": result.get("name"),
                "trashed": result.get("trashed", False),
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to restore file: {exc}"
            ) from exc

    def list_trash(
        self,
        page_size: int = 50,
    ) -> GoogleDriveSearchResult:
        """List the files currently in the bin."""

        try:
            service = self._get_service()

            response = (
                service.files()
                .list(
                    q="trashed = true",
                    pageSize=max(1, min(page_size, 1000)),
                    fields=(
                        "files(id,name,mimeType,"
                        "modifiedTime,webViewLink)"
                    ),
                )
                .execute()
            )

            files = [
                GoogleDriveFile(
                    id=item["id"],
                    name=item["name"],
                    mime_type=item["mimeType"],
                    modified_time=item.get("modifiedTime"),
                    web_view_link=item.get("webViewLink"),
                )
                for item in response.get("files", [])
            ]

            return GoogleDriveSearchResult(
                files=files,
                total=len(files),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to list trash: {exc}"
            ) from exc

    def empty_trash(self) -> dict[str, Any]:
        """Permanently erase everything in the bin."""

        try:
            service = self._get_service()

            service.files().emptyTrash().execute()

            return {"emptied": True}

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to empty trash: {exc}"
            ) from exc

    # =================================================================
    # COPY, EXPORT, SHORTCUTS
    # =================================================================

    def copy_file(
        self,
        file_id: str,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> GoogleDriveFile:
        """Duplicate a file."""

        try:
            service = self._get_service()

            metadata: dict[str, Any] = {}

            if name:
                metadata["name"] = name

            if parent_id:
                metadata["parents"] = [parent_id]

            result = (
                service.files()
                .copy(
                    fileId=file_id,
                    body=metadata,
                    fields=(
                        "id,name,mimeType,"
                        "modifiedTime,webViewLink"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=result["id"],
                name=result["name"],
                mime_type=result["mimeType"],
                modified_time=result.get("modifiedTime"),
                web_view_link=result.get("webViewLink"),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to copy file: {exc}"
            ) from exc

    # Google's own formats hold no bytes to download - a Doc is not a
    # file, it is a database row. Asking for its content the ordinary
    # way fails; it has to be EXPORTED into a real format first, and
    # this is the map from the shorthand a caller will type to the
    # MIME type the API demands.
    EXPORT_FORMATS: dict[str, str] = {
        "pdf": "application/pdf",
        "txt": "text/plain",
        "html": "text/html",
        "rtf": "application/rtf",
        "csv": "text/csv",
        "docx": (
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        "xlsx": (
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        "pptx": (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        ),
    }

    def export_file(
        self,
        file_id: str,
        export_format: str,
        output_path: str,
    ) -> dict[str, Any]:
        """
        Export a Google Doc, Sheet or Slide to a real file.

        This is the only way to get the contents of a Google-native
        document out of Drive.
        """

        import io

        from googleapiclient.http import MediaIoBaseDownload

        wanted = export_format.strip().lower().lstrip(".")

        mime_type = self.EXPORT_FORMATS.get(wanted)

        if mime_type is None:
            raise GoogleDriveFileOperationError(
                f"Unsupported export format {export_format!r}. "
                f"Supported: {', '.join(sorted(self.EXPORT_FORMATS))}."
            )

        try:
            service = self._get_service()

            request = service.files().export_media(
                fileId=file_id,
                mimeType=mime_type,
            )

            with io.FileIO(output_path, "wb") as handle:

                downloader = MediaIoBaseDownload(handle, request)

                done = False

                while not done:
                    _, done = downloader.next_chunk()

            return {
                "file_id": file_id,
                "format": wanted,
                "output_path": output_path,
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to export file: {exc}"
            ) from exc

    def create_shortcut(
        self,
        target_id: str,
        name: str,
        parent_id: str | None = None,
    ) -> GoogleDriveFile:
        """Create a shortcut pointing at another Drive item."""

        try:
            service = self._get_service()

            metadata: dict[str, Any] = {
                "name": name,
                "mimeType": "application/vnd.google-apps.shortcut",
                "shortcutDetails": {"targetId": target_id},
            }

            if parent_id:
                metadata["parents"] = [parent_id]

            result = (
                service.files()
                .create(
                    body=metadata,
                    fields=(
                        "id,name,mimeType,"
                        "modifiedTime,webViewLink"
                    ),
                )
                .execute()
            )

            return GoogleDriveFile(
                id=result["id"],
                name=result["name"],
                mime_type=result["mimeType"],
                modified_time=result.get("modifiedTime"),
                web_view_link=result.get("webViewLink"),
            )

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to create shortcut: {exc}"
            ) from exc

    # =================================================================
    # ACCOUNT, REVISIONS AND SHARING
    # =================================================================

    def get_storage_quota(self) -> dict[str, Any]:
        """How much Drive space is used, and by what."""

        try:
            service = self._get_service()

            about = (
                service.about()
                .get(fields="storageQuota,user")
                .execute()
            )

            quota = about.get("storageQuota", {})

            def as_int(value: Any) -> int | None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            limit = as_int(quota.get("limit"))
            usage = as_int(quota.get("usage"))

            return {
                "user": about.get("user", {}).get("emailAddress"),
                "limit_bytes": limit,
                "usage_bytes": usage,
                "usage_in_drive_bytes": as_int(
                    quota.get("usageInDrive")
                ),
                "usage_in_trash_bytes": as_int(
                    quota.get("usageInDriveTrash")
                ),
                # Percentages are what a person actually asked for, and
                # an unlimited account has no limit at all - so this is
                # None rather than a division by zero.
                "percent_used": (
                    round(100 * usage / limit, 1)
                    if limit and usage is not None and limit > 0
                    else None
                ),
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to read storage quota: {exc}"
            ) from exc

    def list_revisions(
        self,
        file_id: str,
    ) -> dict[str, Any]:
        """List the saved versions of a file."""

        try:
            service = self._get_service()

            response = (
                service.revisions()
                .list(
                    fileId=file_id,
                    fields=(
                        "revisions(id,modifiedTime,size,"
                        "lastModifyingUser)"
                    ),
                )
                .execute()
            )

            return {
                "file_id": file_id,
                "revisions": [
                    {
                        "id": revision.get("id"),
                        "modified_time": revision.get("modifiedTime"),
                        "size": revision.get("size"),
                        "modified_by": revision.get(
                            "lastModifyingUser", {}
                        ).get("displayName"),
                    }
                    for revision in response.get("revisions", [])
                ],
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to list revisions: {exc}"
            ) from exc

    def update_permission(
        self,
        file_id: str,
        permission_id: str,
        role: str,
    ) -> dict[str, Any]:
        """Change what somebody may do with a file."""

        allowed = {
            "owner",
            "organizer",
            "fileOrganizer",
            "writer",
            "commenter",
            "reader",
        }

        if role not in allowed:
            raise GoogleDriveFileOperationError(
                f"role must be one of: {', '.join(sorted(allowed))}."
            )

        try:
            service = self._get_service()

            result = (
                service.permissions()
                .update(
                    fileId=file_id,
                    permissionId=permission_id,
                    body={"role": role},
                    fields="id,type,role,emailAddress",
                )
                .execute()
            )

            return {
                "id": result.get("id"),
                "permission_type": result.get("type"),
                "role": result.get("role"),
                "email_address": result.get("emailAddress"),
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to update permission: {exc}"
            ) from exc

    def delete_permission(
        self,
        file_id: str,
        permission_id: str,
    ) -> dict[str, Any]:
        """Revoke somebody's access to a file."""

        try:
            service = self._get_service()

            service.permissions().delete(
                fileId=file_id,
                permissionId=permission_id,
            ).execute()

            return {
                "file_id": file_id,
                "revoked_permission_id": permission_id,
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to revoke permission: {exc}"
            ) from exc

    def list_shared_drives(
        self,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List the shared drives available to this account."""

        try:
            service = self._get_service()

            response = (
                service.drives()
                .list(pageSize=max(1, min(page_size, 100)))
                .execute()
            )

            return {
                "drives": [
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "created_time": item.get("createdTime"),
                    }
                    for item in response.get("drives", [])
                ],
            }

        except HttpError as exc:
            raise GoogleDriveFileOperationError(
                f"Failed to list shared drives: {exc}"
            ) from exc
