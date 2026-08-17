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

    def close(self) -> None:

        self.client.close()