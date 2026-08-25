import os
from pathlib import Path
from typing import Any

import httpx

from core.logging import get_logger

from .errors import (
    SlackAPIError,
    SlackAuthenticationError,
    SlackNotFoundError,
    SlackPermissionError,
)


logger = get_logger(__name__)


class SlackService:

    BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        token: str | None = None,
    ):
        self.token = token or os.getenv(
            "SLACK_BOT_TOKEN"
        )

        if not self.token:
            raise SlackAuthenticationError(
                "SLACK_BOT_TOKEN is not configured."
            )

        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=30.0,
        )

        self._verify_auth()

    # ========================================================
    # INTERNAL REQUEST
    # ========================================================

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        try:
            response = self.client.request(
                method,
                endpoint,
                params=params,
                json=json,
            )

            response.raise_for_status()

            data = response.json()

        except httpx.HTTPError as exc:

            logger.exception(
                "Slack HTTP request failed"
            )

            raise SlackAPIError(
                f"Slack HTTP request failed: {exc}"
            ) from exc

        except ValueError as exc:

            logger.exception(
                "Slack returned invalid JSON"
            )

            raise SlackAPIError(
                "Slack returned an invalid response."
            ) from exc

        if not data.get("ok"):

            error_code = data.get(
                "error",
                "unknown_error",
            )

            self._raise_slack_error(
                error_code
            )

        return data

    # ========================================================
    # ERROR MAPPING
    # ========================================================

    def _raise_slack_error(
        self,
        error_code: str,
    ) -> None:

        permission_errors = {
            "missing_scope",
            "not_authed",
            "invalid_auth",
            "token_revoked",
            "account_inactive",
        }

        not_found_errors = {
            "channel_not_found",
            "message_not_found",
            "file_not_found",
            "user_not_found",
        }

        if error_code in permission_errors:

            raise SlackPermissionError(
                f"Slack permission/authentication error: "
                f"{error_code}"
            )

        if error_code in not_found_errors:

            raise SlackNotFoundError(
                f"Slack resource not found: "
                f"{error_code}"
            )

        raise SlackAPIError(
            f"Slack API error: {error_code}",
            error_code=error_code,
        )

    # ========================================================
    # AUTH
    # ========================================================

    def _verify_auth(self) -> dict[str, Any]:

        return self._request(
            "POST",
            "/auth.test",
        )

    def get_auth_info(self) -> dict[str, Any]:

        return self._request(
            "POST",
            "/auth.test",
        )

    # ========================================================
    # CHANNELS
    # ========================================================

    def list_channels(
        self,
        *,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/conversations.list",
            params={
                "exclude_archived": exclude_archived,
                "limit": limit,
                "types": (
                    "public_channel,"
                    "private_channel,"
                    "mpim,"
                    "im"
                ),
            },
        )

    def get_channel(
        self,
        channel_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/conversations.info",
            params={
                "channel": channel_id,
            },
        )

    def create_channel(
        self,
        name: str,
        *,
        is_private: bool = False,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.create",
            json={
                "name": name,
                "is_private": is_private,
            },
        )

    def join_channel(
        self,
        channel_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.join",
            json={
                "channel": channel_id,
            },
        )

    def leave_channel(
        self,
        channel_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.leave",
            json={
                "channel": channel_id,
            },
        )

    def archive_channel(
        self,
        channel_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.archive",
            json={
                "channel": channel_id,
            },
        )

    # ========================================================
    # MESSAGES
    # ========================================================

    def send_message(
        self,
        channel_id: str,
        text: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/chat.postMessage",
            json={
                "channel": channel_id,
                "text": text,
            },
        )

    def update_message(
        self,
        channel_id: str,
        timestamp: str,
        text: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/chat.update",
            json={
                "channel": channel_id,
                "ts": timestamp,
                "text": text,
            },
        )

    def delete_message(
        self,
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/chat.delete",
            json={
                "channel": channel_id,
                "ts": timestamp,
            },
        )

    def get_history(
        self,
        channel_id: str,
        *,
        limit: int = 50,
        oldest: str | None = None,
        latest: str | None = None,
    ) -> dict[str, Any]:

        params: dict[str, Any] = {
            "channel": channel_id,
            "limit": limit,
        }

        if oldest:
            params["oldest"] = oldest

        if latest:
            params["latest"] = latest

        return self._request(
            "GET",
            "/conversations.history",
            params=params,
        )

    def get_thread_replies(
        self,
        channel_id: str,
        timestamp: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/conversations.replies",
            params={
                "channel": channel_id,
                "ts": timestamp,
                "limit": limit,
            },
        )

    # ========================================================
    # USERS
    # ========================================================

    def list_users(
        self,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/users.list",
            params={
                "limit": limit,
            },
        )

    def get_user(
        self,
        user_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/users.info",
            params={
                "user": user_id,
            },
        )

    # ========================================================
    # FILES
    # ========================================================

    def list_files(
        self,
        *,
        channel_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:

        params: dict[str, Any] = {
            "limit": limit,
        }

        if channel_id:
            params["channel"] = channel_id

        if user_id:
            params["user"] = user_id

        return self._request(
            "GET",
            "/files.list",
            params=params,
        )

    def get_file(
        self,
        file_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/files.info",
            params={
                "file": file_id,
            },
        )

    def delete_file(
        self,
        file_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/files.delete",
            json={
                "file": file_id,
            },
        )

    def upload_file(
        self,
        file_path: str,
        *,
        channel_id: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:

        path = Path(file_path)

        if not path.exists():

            raise SlackNotFoundError(
                f"File does not exist: {file_path}"
            )

        if not path.is_file():

            raise SlackAPIError(
                f"Path is not a file: {file_path}"
            )

        data: dict[str, Any] = {}

        if channel_id:
            data["channels"] = channel_id

        if title:
            data["title"] = title

        if initial_comment:
            data["initial_comment"] = initial_comment

        try:

            with path.open(
                "rb"
            ) as file:

                response = self.client.post(
                    "/files.upload",
                    data=data,
                    files={
                        "file": (
                            path.name,
                            file,
                        )
                    },
                )

            response.raise_for_status()

            result = response.json()

        except httpx.HTTPError as exc:

            logger.exception(
                "Slack file upload failed"
            )

            raise SlackAPIError(
                f"Slack file upload failed: {exc}"
            ) from exc

        if not result.get("ok"):

            self._raise_slack_error(
                result.get(
                    "error",
                    "file_upload_failed",
                )
            )

        return result

    # ========================================================
    # CLEANUP
    # ========================================================

    # ========================================================
    # CHANNEL ADMINISTRATION
    # ========================================================

    def invite_to_channel(
        self,
        channel_id: str,
        user_ids: list[str],
    ) -> dict[str, Any]:
        """
        Add people to a channel.

        Slack wants a comma-separated string, not a JSON array, and
        silently does nothing useful if handed the wrong shape. The
        list is the sane interface; the join happens here.
        """

        if not user_ids:
            raise ValueError(
                "At least one user id is required."
            )

        return self._request(
            "POST",
            "/conversations.invite",
            json={
                "channel": channel_id,
                "users": ",".join(user_ids),
            },
        )

    def remove_from_channel(
        self,
        channel_id: str,
        user_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.kick",
            json={
                "channel": channel_id,
                "user": user_id,
            },
        )

    def rename_channel(
        self,
        channel_id: str,
        name: str,
    ) -> dict[str, Any]:

        if not name.strip():
            raise ValueError("Channel name cannot be empty.")

        return self._request(
            "POST",
            "/conversations.rename",
            json={
                "channel": channel_id,
                "name": name.strip(),
            },
        )

    def set_channel_topic(
        self,
        channel_id: str,
        topic: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.setTopic",
            json={
                "channel": channel_id,
                "topic": topic,
            },
        )

    def set_channel_purpose(
        self,
        channel_id: str,
        purpose: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/conversations.setPurpose",
            json={
                "channel": channel_id,
                "purpose": purpose,
            },
        )

    def list_channel_members(
        self,
        channel_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/conversations.members",
            params={
                "channel": channel_id,
                "limit": max(1, min(limit, 1000)),
            },
        )

    def open_conversation(
        self,
        user_ids: list[str],
    ) -> dict[str, Any]:
        """
        Open (or find) a direct message conversation.

        This is the missing half of "send a DM". Slack's chat.postMessage
        needs a CHANNEL id, and a DM's channel does not exist until it
        has been opened - so without this, sending a direct message is
        not possible at all, only replying in one that already exists.
        """

        if not user_ids:
            raise ValueError(
                "At least one user id is required."
            )

        return self._request(
            "POST",
            "/conversations.open",
            json={"users": ",".join(user_ids)},
        )

    # ========================================================
    # MESSAGES - SCHEDULING, REACTIONS, PINS
    # ========================================================

    def send_ephemeral_message(
        self,
        channel_id: str,
        user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """
        A message only ONE person in the channel can see.

        Useful for answering someone without adding noise for everyone
        else, and it cannot be edited or deleted afterwards because it
        was never really posted.
        """

        return self._request(
            "POST",
            "/chat.postEphemeral",
            json={
                "channel": channel_id,
                "user": user_id,
                "text": text,
            },
        )

    def send_thread_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """
        Reply inside a thread.

        `broadcast` also pushes the reply to the channel, which is the
        difference between answering a question and announcing it.
        """

        return self._request(
            "POST",
            "/chat.postMessage",
            json={
                "channel": channel_id,
                "text": text,
                "thread_ts": thread_ts,
                "reply_broadcast": broadcast,
            },
        )

    def schedule_message(
        self,
        channel_id: str,
        text: str,
        post_at: int,
    ) -> dict[str, Any]:
        """
        Post a message at a future time.

        `post_at` is a Unix timestamp in SECONDS. Slack rejects a time
        in the past or more than 120 days out, and the error for the
        first case ("time_in_past") is easy to hit by passing
        milliseconds - so the units are checked here where a clear
        message is possible.
        """

        if post_at > 10_000_000_000:
            raise ValueError(
                "post_at looks like milliseconds. Slack expects "
                "Unix SECONDS."
            )

        return self._request(
            "POST",
            "/chat.scheduleMessage",
            json={
                "channel": channel_id,
                "text": text,
                "post_at": post_at,
            },
        )

    def list_scheduled_messages(
        self,
        channel_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:

        params: dict[str, Any] = {
            "limit": max(1, min(limit, 1000)),
        }

        if channel_id:
            params["channel"] = channel_id

        return self._request(
            "GET",
            "/chat.scheduledMessages.list",
            params=params,
        )

    def delete_scheduled_message(
        self,
        channel_id: str,
        scheduled_message_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/chat.deleteScheduledMessage",
            json={
                "channel": channel_id,
                "scheduled_message_id": scheduled_message_id,
            },
        )

    def get_permalink(
        self,
        channel_id: str,
        message_ts: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/chat.getPermalink",
            params={
                "channel": channel_id,
                "message_ts": message_ts,
            },
        )

    def add_reaction(
        self,
        channel_id: str,
        timestamp: str,
        name: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/reactions.add",
            json={
                "channel": channel_id,
                "timestamp": timestamp,
                # Slack wants the bare name. ":tada:" is a common and
                # confusing 500-adjacent failure ("invalid_name").
                "name": name.strip().strip(":"),
            },
        )

    def remove_reaction(
        self,
        channel_id: str,
        timestamp: str,
        name: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/reactions.remove",
            json={
                "channel": channel_id,
                "timestamp": timestamp,
                "name": name.strip().strip(":"),
            },
        )

    def list_reactions(
        self,
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/reactions.get",
            params={
                "channel": channel_id,
                "timestamp": timestamp,
            },
        )

    def pin_message(
        self,
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/pins.add",
            json={
                "channel": channel_id,
                "timestamp": timestamp,
            },
        )

    def unpin_message(
        self,
        channel_id: str,
        timestamp: str,
    ) -> dict[str, Any]:

        return self._request(
            "POST",
            "/pins.remove",
            json={
                "channel": channel_id,
                "timestamp": timestamp,
            },
        )

    def list_pins(
        self,
        channel_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/pins.list",
            params={"channel": channel_id},
        )

    def search_messages(
        self,
        query: str,
        count: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """
        Full-text search across the workspace.

        Needs a signed-in member's credential rather than an app's -
        Slack does not expose search to apps at all, and the refusal
        it sends back is explained by the tool layer rather than
        passed on raw.

        The word "token" is deliberately absent from the tool-facing
        docstring: the router indexes those docstrings, and an LLM
        prompt saying "use minimum tokens" then matched this tool as
        the most relevant thing on the server.
        """

        if not query.strip():
            raise ValueError("Search query cannot be empty.")

        return self._request(
            "GET",
            "/search.messages",
            params={
                "query": query,
                "count": max(1, min(count, 100)),
                "page": max(1, page),
            },
        )

    # ========================================================
    # USERS
    # ========================================================

    def get_user_presence(
        self,
        user_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/users.getPresence",
            params={"user": user_id},
        )

    def find_user_by_email(
        self,
        email: str,
    ) -> dict[str, Any]:
        """
        Look a member up by email address.

        The practical way to turn "message Sam" into a user id when
        you know their email but not their Slack handle.
        """

        if "@" not in email:
            raise ValueError(
                "A full email address is required."
            )

        return self._request(
            "GET",
            "/users.lookupByEmail",
            params={"email": email.strip()},
        )

    def get_user_profile(
        self,
        user_id: str,
    ) -> dict[str, Any]:

        return self._request(
            "GET",
            "/users.profile.get",
            params={"user": user_id},
        )

    def list_usergroups(self) -> dict[str, Any]:

        return self._request(
            "GET",
            "/usergroups.list",
        )

    def get_team_info(self) -> dict[str, Any]:

        return self._request(
            "GET",
            "/team.info",
        )

    def close(self) -> None:

        self.client.close()