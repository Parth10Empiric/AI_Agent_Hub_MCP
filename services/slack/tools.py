from typing import Any

from core.tenancy import service_proxy

from services.slack.services import SlackService
from services.slack.tool_helpers import slack_tool


def register_slack_tools(mcp) -> None:

    # Per-request, not per-process. SlackService already accepted a
    # token; it simply never received one.
    service = service_proxy("slack", lambda token: SlackService(token=token))

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
        Post a message to a Slack channel or direct message.

        This is the normal way to say something in Slack - a summary,
        an update, a notification, an announcement. Everyone in the
        channel sees it and it cannot be un-sent, only edited or
        deleted afterwards.

        `channel_id` is an id such as C0123ABC, not a "#name". Use
        slack_list_channels to turn a channel name into its id, or
        slack_open_conversation to get the channel id for a direct
        message. To answer inside an existing thread rather than in
        the channel, use slack_send_thread_reply.
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
    # ================================================================
    # CHANNEL ADMINISTRATION
    # ================================================================
    #
    # get_channel, join_channel, leave_channel and archive_channel
    # existed in SlackService from the beginning and were never
    # registered as tools - so the agent could create a channel but
    # not look one up, and could not join the channel it had just
    # made. They are wired up here.

    @mcp.tool()
    @slack_tool
    def slack_get_channel(
        channel_id: str,
    ) -> dict[str, Any]:
        """
        Get information about one Slack channel.

        Returns its name, topic, purpose, member count and whether it
        is private or archived.
        """

        return service.get_channel(channel_id)

    @mcp.tool()
    @slack_tool
    def slack_join_channel(
        channel_id: str,
    ) -> dict[str, Any]:
        """
        Join a public Slack channel.

        A bot usually has to be in a channel before it can post there,
        so this is often the step that makes sending a message work.
        """

        return service.join_channel(channel_id)

    @mcp.tool()
    @slack_tool
    def slack_leave_channel(
        channel_id: str,
    ) -> dict[str, Any]:
        """
        Leave a Slack channel.
        """

        return service.leave_channel(channel_id)

    @mcp.tool()
    @slack_tool
    def slack_archive_channel(
        channel_id: str,
    ) -> dict[str, Any]:
        """
        Archive a Slack channel.

        Archiving hides the channel and stops anyone posting in it.
        The history is kept and a workspace admin can un-archive it,
        so this is reversible - but it is visible to everybody in the
        channel.
        """

        return service.archive_channel(channel_id)

    @mcp.tool()
    @slack_tool
    def slack_rename_channel(
        channel_id: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Rename a Slack channel.

        Everyone in the workspace sees the new name, and links that
        used the old one may break.
        """

        return service.rename_channel(
            channel_id=channel_id,
            name=name,
        )

    @mcp.tool()
    @slack_tool
    def slack_set_channel_topic(
        channel_id: str,
        topic: str,
    ) -> dict[str, Any]:
        """
        Set a Slack channel's topic.

        The topic is shown in the channel header and the change is
        announced in the channel itself.
        """

        return service.set_channel_topic(
            channel_id=channel_id,
            topic=topic,
        )

    @mcp.tool()
    @slack_tool
    def slack_set_channel_purpose(
        channel_id: str,
        purpose: str,
    ) -> dict[str, Any]:
        """
        Set a Slack channel's purpose - its longer description.
        """

        return service.set_channel_purpose(
            channel_id=channel_id,
            purpose=purpose,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_channel_members(
        channel_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        List the user ids of everyone in a Slack channel.
        """

        return service.list_channel_members(
            channel_id=channel_id,
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_add_channel_members(
        channel_id: str,
        user_ids: list[str],
    ) -> dict[str, Any]:
        """
        Add people to a Slack channel.

        This puts other human beings into a conversation and gives
        them everything already said in it, which is why it is treated
        as an access-control change rather than an ordinary message.
        """

        return service.invite_to_channel(
            channel_id=channel_id,
            user_ids=user_ids,
        )

    @mcp.tool()
    @slack_tool
    def slack_remove_channel_member(
        channel_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """
        Remove someone from a Slack channel.

        Visible to the person removed and to everyone else present.
        """

        return service.remove_from_channel(
            channel_id=channel_id,
            user_id=user_id,
        )

    # ================================================================
    # MESSAGES - THREADS, SCHEDULING, REACTIONS AND PINS
    # ================================================================

    @mcp.tool()
    @slack_tool
    def slack_open_conversation(
        user_ids: list[str],
    ) -> dict[str, Any]:
        """
        Open a direct message conversation and return its channel id.

        Use this before sending a DM: slack_send_message needs a
        channel id, and a direct message's channel does not exist
        until it has been opened. Pass one user id for a DM, or
        several for a group DM.
        """

        return service.open_conversation(user_ids)

    @mcp.tool()
    @slack_tool
    def slack_send_thread_reply(
        channel_id: str,
        thread_timestamp: str,
        text: str,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """
        Reply inside an existing Slack thread.

        `thread_timestamp` is the `ts` of the message being replied to
        - the same value slack_channel_history returns. Set
        broadcast=True to also show the reply in the main channel,
        which turns an answer into an announcement.
        """

        return service.send_thread_reply(
            channel_id=channel_id,
            thread_ts=thread_timestamp,
            text=text,
            broadcast=broadcast,
        )

    @mcp.tool()
    @slack_tool
    def slack_send_ephemeral_message(
        channel_id: str,
        user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Send a private note that only one member of the channel can
        see.

        Nobody else sees it, and it cannot be edited or withdrawn
        afterwards. For an ordinary channel post use
        slack_send_message.
        """

        return service.send_ephemeral_message(
            channel_id=channel_id,
            user_id=user_id,
            text=text,
        )

    @mcp.tool()
    @slack_tool
    def slack_schedule_message(
        channel_id: str,
        text: str,
        post_at: int,
    ) -> dict[str, Any]:
        """
        Schedule a Slack message for a future time.

        `post_at` is a Unix timestamp in SECONDS. Slack refuses a time
        in the past or more than 120 days ahead.
        """

        return service.schedule_message(
            channel_id=channel_id,
            text=text,
            post_at=post_at,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_scheduled_messages(
        channel_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        List Slack messages scheduled but not yet sent.
        """

        return service.list_scheduled_messages(
            channel_id=channel_id,
            limit=limit,
        )

    @mcp.tool()
    @slack_tool
    def slack_delete_scheduled_message(
        channel_id: str,
        scheduled_message_id: str,
    ) -> dict[str, Any]:
        """
        Cancel a scheduled Slack message before it is sent.
        """

        return service.delete_scheduled_message(
            channel_id=channel_id,
            scheduled_message_id=scheduled_message_id,
        )

    @mcp.tool()
    @slack_tool
    def slack_get_permalink(
        channel_id: str,
        message_timestamp: str,
    ) -> dict[str, Any]:
        """
        Get the permanent URL of one Slack message.
        """

        return service.get_permalink(
            channel_id=channel_id,
            message_ts=message_timestamp,
        )

    @mcp.tool()
    @slack_tool
    def slack_add_reaction(
        channel_id: str,
        timestamp: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Add an emoji reaction to a Slack message.

        `name` is the bare emoji name without colons, e.g. 'tada'.
        """

        return service.add_reaction(
            channel_id=channel_id,
            timestamp=timestamp,
            name=name,
        )

    @mcp.tool()
    @slack_tool
    def slack_remove_reaction(
        channel_id: str,
        timestamp: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Remove one of your emoji reactions from a Slack message.
        """

        return service.remove_reaction(
            channel_id=channel_id,
            timestamp=timestamp,
            name=name,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_reactions(
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """
        List the emoji reactions on a Slack message, and who left them.
        """

        return service.list_reactions(
            channel_id=channel_id,
            timestamp=timestamp,
        )

    @mcp.tool()
    @slack_tool
    def slack_pin_message(
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """
        Pin a message to a Slack channel.
        """

        return service.pin_message(
            channel_id=channel_id,
            timestamp=timestamp,
        )

    @mcp.tool()
    @slack_tool
    def slack_unpin_message(
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """
        Remove a pinned message from a Slack channel.
        """

        return service.unpin_message(
            channel_id=channel_id,
            timestamp=timestamp,
        )

    @mcp.tool()
    @slack_tool
    def slack_list_pins(
        channel_id: str,
    ) -> dict[str, Any]:
        """
        List the pinned messages in a Slack channel.
        """

        return service.list_pins(channel_id)

    @mcp.tool()
    @slack_tool
    def slack_search_messages(
        query: str,
        count: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """
        Search Slack messages across the whole workspace.

        Supports Slack search syntax, e.g. 'in:#general from:@sam
        deploy'. Slack only permits search for a signed-in member, not
        for an app, so a workspace connected as an app will refuse
        this; slack_channel_history is the per-channel alternative.
        """

        return service.search_messages(
            query=query,
            count=count,
            page=page,
        )

    # ================================================================
    # USERS AND WORKSPACE
    # ================================================================

    @mcp.tool()
    @slack_tool
    def slack_find_user_by_email(
        email: str,
    ) -> dict[str, Any]:
        """
        Find a Slack user by their email address.

        The practical way to turn a person into a user id when you
        know their email but not their Slack handle.
        """

        return service.find_user_by_email(email)

    @mcp.tool()
    @slack_tool
    def slack_get_user_profile(
        user_id: str,
    ) -> dict[str, Any]:
        """
        Get a Slack user's full profile, including their display
        name, role and contact details.
        """

        return service.get_user_profile(user_id)

    @mcp.tool()
    @slack_tool
    def slack_get_user_presence(
        user_id: str,
    ) -> dict[str, Any]:
        """
        Check whether a Slack user is currently active or away.
        """

        return service.get_user_presence(user_id)

    @mcp.tool()
    @slack_tool
    def slack_list_usergroups() -> dict[str, Any]:
        """
        List the user groups defined in the Slack workspace.
        """

        return service.list_usergroups()

    @mcp.tool()
    @slack_tool
    def slack_get_team_info() -> dict[str, Any]:
        """
        Get information about the Slack workspace itself.
        """

        return service.get_team_info()
