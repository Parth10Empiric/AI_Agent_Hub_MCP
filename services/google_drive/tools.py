from typing import Any

from mcp.server import MCPServer

from .errors import GoogleDriveError
from .services import GoogleDriveService
from core.tool_utils import handle_tool_error


def register_google_drive_tools(mcp: MCPServer) -> None:
    """Register all Google Drive tools with the MCP server."""

    drive_service = GoogleDriveService()

    # ============================================================
    # FILE DISCOVERY
    # ============================================================

    @mcp.tool()
    def google_drive_search_files(
        query: str,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        Search Google Drive files by name.

        Args:
            query: Text to search for in file names.
            page_size: Maximum number of files to return.

        Returns:
            Matching Google Drive files.
        """

        try:
            result = drive_service.search_files(
                query=query,
                page_size=page_size,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_get_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Get metadata for a Google Drive file.

        Args:
            file_id: Google Drive file ID.

        Returns:
            Google Drive file metadata.
        """

        try:
            file = drive_service.get_file(
                file_id=file_id,
            )

            return {
                "success": True,
                "file": file.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_list_folder(
        folder_id: str,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """
        List files and folders inside a Google Drive folder.

        Args:
            folder_id: Google Drive folder ID.
            page_size: Maximum number of items to return.

        Returns:
            Files and folders inside the folder.
        """

        try:
            result = drive_service.list_folder(
                folder_id=folder_id,
                page_size=page_size,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # FILE CONTENT
    # ============================================================

    @mcp.tool()
    def google_drive_read_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Read text content from a Google Drive file.

        Supports readable Google Workspace and text-based files.

        Args:
            file_id: Google Drive file ID.

        Returns:
            File metadata and content.
        """

        try:
            result = drive_service.read_file(
                file_id=file_id,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_download_file(
        file_id: str,
        output_path: str,
    ) -> dict[str, Any]:
        """
        Download a Google Drive file to the local filesystem.

        Args:
            file_id: Google Drive file ID.
            output_path: Local path where the file should be saved.

        Returns:
            Download result and local file path.
        """

        try:
            path = drive_service.download_file(
                file_id=file_id,
                output_path=output_path,
            )

            return {
                "success": True,
                "file_id": file_id,
                "output_path": path,
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # FILE MANAGEMENT
    # ============================================================

    @mcp.tool()
    def google_drive_create_file(
        name: str,
        mime_type: str = "text/plain",
        content: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a file in Google Drive.

        Args:
            name: Name of the new file.
            mime_type: MIME type of the file.
            content: Optional text content.
            parent_id: Optional parent folder ID.

        Returns:
            Created file metadata.
        """

        try:
            file = drive_service.create_file(
                name=name,
                mime_type=mime_type,
                content=content,
                parent_id=parent_id,
            )

            return {
                "success": True,
                "file": file.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_upload_file(
        file_path: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Upload a local file to Google Drive.

        Args:
            file_path: Local file path.
            parent_id: Optional Google Drive folder ID.

        Returns:
            Uploaded file metadata.
        """

        try:
            file = drive_service.upload_file(
                file_path=file_path,
                parent_id=parent_id,
            )

            return {
                "success": True,
                "file": file.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_update_file(
        file_id: str,
        name: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """
        Update a Google Drive file.

        Args:
            file_id: Google Drive file ID.
            name: Optional new file name.
            content: Optional new text content.

        Returns:
            Updated file metadata.
        """

        try:
            file = drive_service.update_file(
                file_id=file_id,
                name=name,
                content=content,
            )

            return {
                "success": True,
                "file": file.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_move_file(
        file_id: str,
        new_parent_id: str,
    ) -> dict[str, Any]:
        """
        Move a Google Drive file into another folder.

        Args:
            file_id: Google Drive file ID.
            new_parent_id: Destination folder ID.

        Returns:
            Updated file metadata.
        """

        try:
            file = drive_service.move_file(
                file_id=file_id,
                new_parent_id=new_parent_id,
            )

            return {
                "success": True,
                "file": file.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_delete_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Move a Google Drive file to trash.

        Args:
            file_id: Google Drive file ID.

        Returns:
            Delete operation result.
        """

        try:
            deleted = drive_service.delete_file(
                file_id=file_id,
            )

            return {
                "success": deleted,
                "file_id": file_id,
                "message": "File moved to trash.",
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # FOLDER MANAGEMENT
    # ============================================================

    @mcp.tool()
    def google_drive_create_folder(
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a folder in Google Drive.

        Args:
            name: Name of the new folder.
            parent_id: Optional parent folder ID.

        Returns:
            Created folder metadata.
        """

        try:
            folder = drive_service.create_folder(
                name=name,
                parent_id=parent_id,
            )

            return {
                "success": True,
                "folder": folder.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # SHARING / PERMISSIONS
    # ============================================================

    @mcp.tool()
    def google_drive_list_permissions(
        file_id: str,
    ) -> dict[str, Any]:
        """
        List permissions for a Google Drive file.

        Args:
            file_id: Google Drive file ID.

        Returns:
            File permissions.
        """

        try:
            result = drive_service.list_permissions(
                file_id=file_id,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_create_permission(
        file_id: str,
        email_address: str,
        role: str = "reader",
    ) -> dict[str, Any]:
        """
        Share a Google Drive file with a user.

        Args:
            file_id: Google Drive file ID.
            email_address: Email address of the user.
            role: Permission role: reader, commenter, or writer.

        Returns:
            Created permission.
        """

        try:
            permission = drive_service.create_permission(
                file_id=file_id,
                email_address=email_address,
                role=role,
            )

            return {
                "success": True,
                "permission": permission.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)