import datetime
import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .errors import (
    GoogleCalendarAPIError,
    GoogleCalendarAuthError,
    GoogleCalendarNotFoundError,
    GoogleCalendarPermissionError,
    GoogleCalendarValidationError,
)


BASE_DIR = Path(__file__).resolve().parents[2]

CREDENTIALS_FILE = Path(
    os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        BASE_DIR / "credentials.json",
    )
)

TOKEN_FILE = Path(
    os.getenv(
        "GOOGLE_CALENDAR_TOKEN_FILE",
        BASE_DIR / "google_calendar_token.json",
    )
)


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


class GoogleCalendarService:
    """
    Production-oriented Google Calendar API wrapper.

    All Google Calendar API communication lives here.
    MCP tools should not directly call Google APIs.
    """

    def __init__(self):
        self._service = None

    # =========================================================
    # Authentication
    # =========================================================

    def _authenticate(self):
        creds = None

        if TOKEN_FILE.exists():

            try:
                creds = Credentials.from_authorized_user_file(
                    str(TOKEN_FILE),
                    SCOPES,
                )
            except Exception as exc:
                raise GoogleCalendarAuthError(
                    f"Unable to read Calendar token: {exc}"
                ) from exc

        if not creds or not creds.valid:

            if (
                creds
                and creds.expired
                and creds.refresh_token
            ):

                try:
                    creds.refresh(Request())

                except Exception as exc:
                    raise GoogleCalendarAuthError(
                        f"Unable to refresh Calendar token: {exc}"
                    ) from exc

            else:

                if not CREDENTIALS_FILE.exists():
                    raise GoogleCalendarAuthError(
                        "Google credentials.json was not found at "
                        f"{CREDENTIALS_FILE}"
                    )

                try:

                    flow = (
                        InstalledAppFlow
                        .from_client_secrets_file(
                            str(CREDENTIALS_FILE),
                            SCOPES,
                        )
                    )

                    creds = flow.run_local_server(
                        port=0
                    )

                except Exception as exc:

                    raise GoogleCalendarAuthError(
                        f"Google Calendar OAuth failed: {exc}"
                    ) from exc

            try:

                TOKEN_FILE.write_text(
                    creds.to_json(),
                    encoding="utf-8",
                )

            except Exception as exc:

                raise GoogleCalendarAuthError(
                    f"Unable to save Calendar token: {exc}"
                ) from exc

        return creds

    def get_service(self):
        """
        Lazily create the Google Calendar client.
        """

        if self._service is not None:
            return self._service

        try:

            credentials = self._authenticate()

            self._service = build(
                "calendar",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )

            return self._service

        except GoogleCalendarAuthError:
            raise

        except Exception as exc:

            raise GoogleCalendarAPIError(
                f"Unable to initialize Google Calendar: {exc}"
            ) from exc

    # =========================================================
    # Utility
    # =========================================================

    @staticmethod
    def _handle_http_error(error: HttpError):
        status = getattr(
            error.resp,
            "status",
            None,
        )

        if status in (401, 403):

            raise GoogleCalendarPermissionError(
                f"Google Calendar permission error: {error}"
            ) from error

        if status == 404:

            raise GoogleCalendarNotFoundError(
                f"Google Calendar resource not found: {error}"
            ) from error

        if status == 400:

            raise GoogleCalendarValidationError(
                f"Invalid Google Calendar request: {error}"
            ) from error

        raise GoogleCalendarAPIError(
            f"Google Calendar API error: {error}"
        ) from error

    @staticmethod
    def _event_start_end(
        start: str,
        end: str,
        time_zone: str | None = None,
    ):
        """
        Convert user-friendly date strings into
        Google Calendar event start/end objects.
        """

        if not start or not end:
            raise GoogleCalendarValidationError(
                "Both start and end are required."
            )

        # All-day event
        if len(start) == 10 and len(end) == 10:

            return (
                {"date": start},
                {"date": end},
            )

        start_data = {
            "dateTime": start,
        }

        end_data = {
            "dateTime": end,
        }

        if time_zone:

            start_data["timeZone"] = time_zone
            end_data["timeZone"] = time_zone

        return start_data, end_data

    @staticmethod
    def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:

        return {
            "id": event.get("id"),
            "status": event.get("status"),
            "summary": event.get("summary"),
            "description": event.get("description"),
            "location": event.get("location"),
            "html_link": event.get("htmlLink"),
            "start": event.get("start"),
            "end": event.get("end"),
            "created": event.get("created"),
            "updated": event.get("updated"),
            "creator": event.get("creator"),
            "organizer": event.get("organizer"),
            "attendees": event.get("attendees", []),
            "recurrence": event.get("recurrence", []),
            "visibility": event.get("visibility"),
            "color_id": event.get("colorId"),
        }

    # =========================================================
    # CalendarList
    # =========================================================

    def list_calendars(
        self,
        max_results: int = 100,
        page_token: str | None = None,
    ):

        service = self.get_service()

        try:

            request = service.calendarList().list(
                maxResults=max_results,
            )

            if page_token:
                request = request.page_token(
                    page_token
                )

            response = request.execute()

            return {
                "success": True,
                "items": response.get(
                    "items",
                    [],
                ),
                "next_page_token": response.get(
                    "nextPageToken"
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def get_calendar(
        self,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        try:

            result = (
                service.calendars()
                .get(
                    calendarId=calendar_id,
                )
                .execute()
            )

            return {
                "success": True,
                "calendar": result,
            }

        except HttpError as error:
            self._handle_http_error(error)

    # =========================================================
    # Events
    # =========================================================

    def list_events(
        self,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
        query: str | None = None,
        single_events: bool = True,
        order_by: str = "startTime",
        page_token: str | None = None,
    ):

        service = self.get_service()

        try:

            kwargs = {
                "calendarId": calendar_id,
                "maxResults": max_results,
                "singleEvents": single_events,
                "orderBy": order_by,
            }

            if time_min:
                kwargs["timeMin"] = time_min

            if time_max:
                kwargs["timeMax"] = time_max

            if query:
                kwargs["q"] = query

            if page_token:
                kwargs["pageToken"] = page_token

            response = (
                service.events()
                .list(**kwargs)
                .execute()
            )

            return {
                "success": True,
                "items": [
                    self._normalize_event(event)
                    for event in response.get(
                        "items",
                        [],
                    )
                ],
                "next_page_token": response.get(
                    "nextPageToken"
                ),
                "next_sync_token": response.get(
                    "nextSyncToken"
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def get_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        try:

            event = (
                service.events()
                .get(
                    calendarId=calendar_id,
                    eventId=event_id,
                )
                .execute()
            )

            return {
                "success": True,
                "event": self._normalize_event(
                    event
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        description: str | None = None,
        location: str | None = None,
        time_zone: str | None = None,
        attendees: list[str] | None = None,
        recurrence: list[str] | None = None,
        reminders: list[dict] | None = None,
        color_id: str | None = None,
        visibility: str | None = None,
    ):

        service = self.get_service()

        start_data, end_data = (
            self._event_start_end(
                start,
                end,
                time_zone,
            )
        )

        body = {
            "summary": summary,
            "start": start_data,
            "end": end_data,
        }

        if description:
            body["description"] = description

        if location:
            body["location"] = location

        if attendees:
            body["attendees"] = [
                {"email": email}
                for email in attendees
            ]

        if recurrence:
            body["recurrence"] = recurrence

        if reminders:

            body["reminders"] = {
                "useDefault": False,
                "overrides": reminders,
            }

        if color_id:
            body["colorId"] = color_id

        if visibility:
            body["visibility"] = visibility

        try:

            event = (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body=body,
                    sendUpdates="all"
                    if attendees
                    else "none",
                )
                .execute()
            )

            return {
                "success": True,
                "event": self._normalize_event(
                    event
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def update_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        time_zone: str | None = None,
        attendees: list[str] | None = None,
        recurrence: list[str] | None = None,
        reminders: list[dict] | None = None,
        color_id: str | None = None,
        visibility: str | None = None,
    ):

        service = self.get_service()

        try:

            event = (
                service.events()
                .get(
                    calendarId=calendar_id,
                    eventId=event_id,
                )
                .execute()
            )

            if summary is not None:
                event["summary"] = summary

            if description is not None:
                event["description"] = description

            if location is not None:
                event["location"] = location

            if start is not None or end is not None:

                current_start = (
                    start
                    or event["start"].get(
                        "dateTime",
                        event["start"].get(
                            "date"
                        ),
                    )
                )

                current_end = (
                    end
                    or event["end"].get(
                        "dateTime",
                        event["end"].get(
                            "date"
                        ),
                    )
                )

                (
                    event["start"],
                    event["end"],
                ) = self._event_start_end(
                    current_start,
                    current_end,
                    time_zone,
                )

            if attendees is not None:

                event["attendees"] = [
                    {"email": email}
                    for email in attendees
                ]

            if recurrence is not None:
                event["recurrence"] = recurrence

            if reminders is not None:

                event["reminders"] = {
                    "useDefault": False,
                    "overrides": reminders,
                }

            if color_id is not None:
                event["colorId"] = color_id

            if visibility is not None:
                event["visibility"] = visibility

            updated = (
                service.events()
                .update(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body=event,
                    sendUpdates="all"
                    if attendees is not None
                    else "none",
                )
                .execute()
            )

            return {
                "success": True,
                "event": self._normalize_event(
                    updated
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def delete_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        try:

            (
                service.events()
                .delete(
                    calendarId=calendar_id,
                    eventId=event_id,
                )
                .execute()
            )

            return {
                "success": True,
                "deleted_event_id": event_id,
            }

        except HttpError as error:
            self._handle_http_error(error)

    # =========================================================
    # FreeBusy
    # =========================================================

    def query_freebusy(
        self,
        time_min: str,
        time_max: str,
        calendar_ids: list[str],
        time_zone: str | None = None,
    ):

        service = self.get_service()

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [
                {"id": calendar_id}
                for calendar_id in calendar_ids
            ],
        }

        if time_zone:
            body["timeZone"] = time_zone

        try:

            response = (
                service.freebusy()
                .query(
                    body=body
                )
                .execute()
            )

            return {
                "success": True,
                "calendars": response.get(
                    "calendars",
                    {},
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    # =========================================================
    # Colors
    # =========================================================

    def get_colors(self):

        service = self.get_service()

        try:

            return {
                "success": True,
                "colors": (
                    service.colors()
                    .get()
                    .execute()
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    # =========================================================
    # Settings
    # =========================================================

    def list_settings(
        self,
        max_results: int = 100,
        page_token: str | None = None,
    ):

        service = self.get_service()

        try:

            kwargs = {
                "maxResults": max_results,
            }

            if page_token:
                kwargs["pageToken"] = page_token

            response = (
                service.settings()
                .list(**kwargs)
                .execute()
            )

            return {
                "success": True,
                "items": response.get(
                    "items",
                    [],
                ),
                "next_page_token": response.get(
                    "nextPageToken"
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def get_setting(
        self,
        setting_id: str,
    ):

        service = self.get_service()

        try:

            setting = (
                service.settings()
                .get(
                    setting=setting_id,
                )
                .execute()
            )

            return {
                "success": True,
                "setting": setting,
            }

        except HttpError as error:
            self._handle_http_error(error)

    # =========================================================
    # ACL
    # =========================================================

    def list_acl(
        self,
        calendar_id: str = "primary",
        max_results: int = 100,
        page_token: str | None = None,
    ):

        service = self.get_service()

        try:

            kwargs = {
                "calendarId": calendar_id,
                "maxResults": max_results,
            }

            if page_token:
                kwargs["pageToken"] = page_token

            response = (
                service.acl()
                .list(**kwargs)
                .execute()
            )

            return {
                "success": True,
                "items": response.get(
                    "items",
                    [],
                ),
                "next_page_token": response.get(
                    "nextPageToken"
                ),
            }

        except HttpError as error:
            self._handle_http_error(error)

    def get_acl_rule(
        self,
        rule_id: str,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        try:

            rule = (
                service.acl()
                .get(
                    calendarId=calendar_id,
                    ruleId=rule_id,
                )
                .execute()
            )

            return {
                "success": True,
                "rule": rule,
            }

        except HttpError as error:
            self._handle_http_error(error)

    def create_acl_rule(
        self,
        scope_type: str,
        role: str,
        calendar_id: str = "primary",
        scope_value: str | None = None,
    ):

        service = self.get_service()

        valid_scope_types = {
            "default",
            "user",
            "group",
            "domain",
        }

        valid_roles = {
            "none",
            "freeBusyReader",
            "reader",
            "writerWithoutPrivateAccess",
            "writer",
            "owner",
        }

        if scope_type not in valid_scope_types:
            raise GoogleCalendarValidationError(
                f"Invalid scope type: {scope_type}"
            )

        if role not in valid_roles:
            raise GoogleCalendarValidationError(
                f"Invalid ACL role: {role}"
            )

        scope = {
            "type": scope_type,
        }

        if scope_type != "default":

            if not scope_value:
                raise GoogleCalendarValidationError(
                    "scope_value is required for "
                    f"{scope_type} ACL rules."
                )

            scope["value"] = scope_value

        body = {
            "scope": scope,
            "role": role,
        }

        try:

            rule = (
                service.acl()
                .insert(
                    calendarId=calendar_id,
                    body=body,
                )
                .execute()
            )

            return {
                "success": True,
                "rule": rule,
            }

        except HttpError as error:
            self._handle_http_error(error)

    def update_acl_rule(
        self,
        rule_id: str,
        role: str,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        valid_roles = {
            "none",
            "freeBusyReader",
            "reader",
            "writerWithoutPrivateAccess",
            "writer",
            "owner",
        }

        if role not in valid_roles:
            raise GoogleCalendarValidationError(
                f"Invalid ACL role: {role}"
            )

        try:

            rule = (
                service.acl()
                .patch(
                    calendarId=calendar_id,
                    ruleId=rule_id,
                    body={
                        "role": role,
                    },
                )
                .execute()
            )

            return {
                "success": True,
                "rule": rule,
            }

        except HttpError as error:
            self._handle_http_error(error)

    def delete_acl_rule(
        self,
        rule_id: str,
        calendar_id: str = "primary",
    ):

        service = self.get_service()

        try:

            (
                service.acl()
                .delete(
                    calendarId=calendar_id,
                    ruleId=rule_id,
                )
                .execute()
            )

            return {
                "success": True,
                "deleted_rule_id": rule_id,
            }

        except HttpError as error:
            self._handle_http_error(error)