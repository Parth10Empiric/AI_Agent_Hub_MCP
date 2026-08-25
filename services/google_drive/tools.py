from typing import Any

from mcp.server import MCPServer

from .errors import GoogleDriveError
from .services import GoogleDriveService
from core.tenancy import service_proxy
from core.tool_utils import handle_tool_error


def register_google_drive_tools(mcp: MCPServer) -> None:
    """Register all Google Drive tools with the MCP server."""

    # Per-request, resolved from the MCP request metadata. Every tool
    # below still calls `drive_service.something(...)` unchanged.
    drive_service = service_proxy(
        "google_drive",
        lambda token: GoogleDriveService(access_token=token),
    )

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
    # ============================================================
    # TRASH - THE REVERSIBLE HALF OF DELETING
    # ============================================================

    @mcp.tool()
    def google_drive_trash_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Move a Google Drive file to the bin.

        PREFER THIS over google_drive_delete_file. The file leaves the
        user's Drive either way, but from the bin it can be restored
        for 30 days, while delete_file erases it with nothing to
        recover. Use delete_file only when the user has explicitly
        asked for permanent removal.

        Args:
            file_id: The file to move to the bin.

        Returns:
            The file's id, name and trashed state.
        """

        try:
            return {
                "success": True,
                **drive_service.trash_file(file_id=file_id),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_restore_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Restore a Google Drive file from the bin.

        Args:
            file_id: The file to restore.

        Returns:
            The file's id, name and trashed state.
        """

        try:
            return {
                "success": True,
                **drive_service.restore_file(file_id=file_id),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_list_trash(
        page_size: int = 50,
    ) -> dict[str, Any]:
        """
        List the files currently in the Google Drive bin.

        Args:
            page_size: Maximum number of files to return.

        Returns:
            The files waiting in the bin.
        """

        try:
            result = drive_service.list_trash(page_size=page_size)

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_empty_trash() -> dict[str, Any]:
        """
        Permanently erase EVERY file in the Google Drive bin.

        THIS CANNOT BE UNDONE, and it destroys files the user may
        never have intended to lose - the bin is where things sit
        precisely because nobody was sure. Only use this when the user
        has explicitly asked to empty the bin.

        Returns:
            Confirmation that the bin was emptied.
        """

        try:
            return {
                "success": True,
                **drive_service.empty_trash(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # COPY, EXPORT AND SHORTCUTS
    # ============================================================

    @mcp.tool()
    def google_drive_copy_file(
        file_id: str,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Duplicate a Google Drive file.

        Args:
            file_id: The file to copy.
            name: Name for the copy. Defaults to "Copy of ...".
            parent_id: Folder to put the copy in.

        Returns:
            The newly created copy.
        """

        try:
            result = drive_service.copy_file(
                file_id=file_id,
                name=name,
                parent_id=parent_id,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_export_file(
        file_id: str,
        export_format: str,
        output_path: str,
    ) -> dict[str, Any]:
        """
        Export a Google Doc, Sheet or Slide to a real file.

        Google's own formats hold no downloadable bytes, so
        google_drive_download_file cannot fetch them - they must be
        converted first, which is what this does. For ordinary
        uploaded files (PDFs, images, .docx) use download_file
        instead.

        Args:
            file_id: The Google-format document to export.
            export_format: pdf, txt, html, rtf, csv, docx, xlsx
                or pptx.
            output_path: Where to write the exported file.

        Returns:
            The path the exported file was written to.
        """

        try:
            return {
                "success": True,
                **drive_service.export_file(
                    file_id=file_id,
                    export_format=export_format,
                    output_path=output_path,
                ),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_create_shortcut(
        target_id: str,
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a shortcut to another Google Drive item.

        A shortcut lets one file appear in several folders without
        being copied, so edits are shared rather than diverging.

        Args:
            target_id: The file or folder to point at.
            name: Name for the shortcut.
            parent_id: Folder to create the shortcut in.

        Returns:
            The newly created shortcut.
        """

        try:
            result = drive_service.create_shortcut(
                target_id=target_id,
                name=name,
                parent_id=parent_id,
            )

            return {
                "success": True,
                **result.model_dump(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    # ============================================================
    # ACCOUNT, VERSIONS AND SHARING
    # ============================================================

    @mcp.tool()
    def google_drive_get_storage_quota() -> dict[str, Any]:
        """
        Show how much Google Drive storage is used and how much is
        left.

        Returns:
            Bytes used and available, and the percentage used.
        """

        try:
            return {
                "success": True,
                **drive_service.get_storage_quota(),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_list_revisions(
        file_id: str,
    ) -> dict[str, Any]:
        """
        List the saved versions of a Google Drive file.

        Shows who changed the file and when, which is how to find out
        what a document looked like before an edit.

        Args:
            file_id: The file whose history to list.

        Returns:
            The file's revisions, newest last.
        """

        try:
            return {
                "success": True,
                **drive_service.list_revisions(file_id=file_id),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_list_shared_drives(
        page_size: int = 50,
    ) -> dict[str, Any]:
        """
        List the shared drives this account can reach.

        Args:
            page_size: Maximum number of drives to return.

        Returns:
            The available shared drives.
        """

        try:
            return {
                "success": True,
                **drive_service.list_shared_drives(
                    page_size=page_size,
                ),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_update_permission(
        file_id: str,
        permission_id: str,
        role: str,
    ) -> dict[str, Any]:
        """
        Change what somebody may do with a Google Drive file.

        Roles, from most to least powerful: owner, organizer,
        fileOrganizer, writer, commenter, reader. Raising somebody to
        writer lets them change or delete the contents.

        Args:
            file_id: The file whose sharing is changing.
            permission_id: The permission to change - from
                google_drive_list_permissions.
            role: The new role.

        Returns:
            The updated permission.
        """

        try:
            return {
                "success": True,
                **drive_service.update_permission(
                    file_id=file_id,
                    permission_id=permission_id,
                    role=role,
                ),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)

    @mcp.tool()
    def google_drive_delete_permission(
        file_id: str,
        permission_id: str,
    ) -> dict[str, Any]:
        """
        Revoke somebody's access to a Google Drive file.

        The person loses access immediately and is not told why. If
        they are the file's owner this will fail - ownership has to be
        transferred instead.

        Args:
            file_id: The file to revoke access to.
            permission_id: The permission to remove - from
                google_drive_list_permissions.

        Returns:
            Confirmation of what was revoked.
        """

        try:
            return {
                "success": True,
                **drive_service.delete_permission(
                    file_id=file_id,
                    permission_id=permission_id,
                ),
            }

        except GoogleDriveError as exc:
            return handle_tool_error(exc)
