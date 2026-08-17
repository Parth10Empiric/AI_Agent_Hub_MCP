from typing import Any

from services.slack.services import SlackService
from services.slack.tool_helpers import slack_tool


def register_slack_tools(mcp) -> None:

    service = SlackService()

    @mcp.tool()
    @slack_tool
    def slack_auth_info() -> dict[str, Any]:
        """
        Test Slack authentication and return workspace information.
        """

        return service.get_auth_info()

    @mcp.tool()
    @slack_tool
    def slack_list_channels(
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        List Slack channels.
        """

        return service.list_channels(
            exclude_archived=exclude_archived,
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_create_channel(
        name: str,
        is_private: bool = False,
    ) -> dict[str, Any]:
        """
        Create a Slack channel.
        """

        return service.create_channel(
            name=name,
            is_private=is_private,
        )

    @mcp.tool()
    @slack_tool
    def slack_send_message(
        channel_id: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Send a message to a Slack channel.
        """

        return service.send_message(
            channel_id=channel_id,
            text=text,
        )

    @mcp.tool()
    @slack_tool
    def slack_update_message(
        channel_id: str,
        timestamp: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Update a Slack message.
        """

        return service.update_message(
            channel_id=channel_id,
            timestamp=timestamp,
            text=text,
        )

    @mcp.tool()
    @slack_tool
    def slack_delete_message(
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """
        Delete a Slack message.
        """

        return service.delete_message(
            channel_id=channel_id,
            timestamp=timestamp,
        )

    @mcp.tool()
    @slack_tool
    def slack_channel_history(
        channel_id: str,
        limit: int = 50,
        oldest: str | None = None,
        latest: str | None = None,
    ) -> dict[str, Any]:
        """
        Get channel message history.
        """

        return service.get_history(
            channel_id=channel_id,
            limit=limit,
            oldest=oldest,
            latest=latest,
        )

    @mcp.tool()
    @slack_tool
    def slack_thread_replies(
        channel_id: str,
        timestamp: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        Get replies to a Slack thread.
        """

        return service.get_thread_replies(
            channel_id=channel_id,
            timestamp=timestamp,
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_users(
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        List Slack workspace users.
        """

        return service.list_users(
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_get_user(
        user_id: str,
    ) -> dict[str, Any]:
        """
        Get Slack user information.
        """

        return service.get_user(
            user_id,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_files(
        channel_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        List Slack files.
        """

        return service.list_files(
            channel_id=channel_id,
            user_id=user_id,
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_get_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Get Slack file information.
        """

        return service.get_file(
            file_id,
        )

    @mcp.tool()
    @slack_tool
    def slack_delete_file(
        file_id: str,
    ) -> dict[str, Any]:
        """
        Delete a Slack file.
        """

        return service.delete_file(
            file_id,
        )

    @mcp.tool()
    @slack_tool
    def slack_upload_file(
        file_path: str,
        channel_id: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:
        """
        Upload a file to Slack.
        """

        return service.upload_file(
            file_path=file_path,
            channel_id=channel_id,
            title=title,
            initial_comment=initial_comment,
        )