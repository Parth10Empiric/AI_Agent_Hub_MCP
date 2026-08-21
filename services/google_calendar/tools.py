from mcp.server import MCPServer

from core.tenancy import service_proxy

from .schemas import event_input_schema
from .services import GoogleCalendarService
from .tool_helper import handle_calendar_errors


# Per-request, not per-process. Every tool below is unchanged.
calendar_service = service_proxy(
    "google_calendar",
    lambda token: GoogleCalendarService(access_token=token),
)


def register_google_calendar_tools(
    mcp: MCPServer,
):

    # Calendar list

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_list_calendars(
        max_results: int = 100,
        page_token: str | None = None,
    ):
        """
        List calendars available to the authenticated user.
        """

        return calendar_service.list_calendars(
            max_results=max_results,
            page_token=page_token,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_get_calendar(
        calendar_id: str = "primary",
    ):
        """
        Get information about a calendar.
        """

        return calendar_service.get_calendar(
            calendar_id=calendar_id,
        )

    # =========================================================
    # Events
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_list_events(
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
        query: str | None = None,
        page_token: str | None = None,
    ):
        """
        List events from a Google Calendar.
        """

        return calendar_service.list_events(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
            query=query,
            page_token=page_token,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_get_event(
        event_id: str,
        calendar_id: str = "primary",
    ):
        """
        Get a specific Google Calendar event.
        """

        return calendar_service.get_event(
            event_id=event_id,
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_create_event(
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
        """
        Create a Google Calendar event.

        start/end should use RFC3339 for timed events,
        or YYYY-MM-DD for all-day events.
        """

        return calendar_service.create_event(
            summary=summary,
            start=start,
            end=end,
            calendar_id=calendar_id,
            description=description,
            location=location,
            time_zone=time_zone,
            attendees=attendees,
            recurrence=recurrence,
            reminders=reminders,
            color_id=color_id,
            visibility=visibility,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_update_event(
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
        """
        Update an existing Google Calendar event.
        """

        return calendar_service.update_event(
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            time_zone=time_zone,
            attendees=attendees,
            recurrence=recurrence,
            reminders=reminders,
            color_id=color_id,
            visibility=visibility,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_delete_event(
        event_id: str,
        calendar_id: str = "primary",
    ):
        """
        Delete a Google Calendar event.
        """

        return calendar_service.delete_event(
            event_id=event_id,
            calendar_id=calendar_id,
        )

    # =========================================================
    # FreeBusy
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_freebusy(
        time_min: str,
        time_max: str,
        calendar_ids: list[str],
        time_zone: str | None = None,
    ):
        """
        Check free/busy information for one or more calendars.
        """

        return calendar_service.query_freebusy(
            time_min=time_min,
            time_max=time_max,
            calendar_ids=calendar_ids,
            time_zone=time_zone,
        )

    # =========================================================
    # Colors
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_get_colors():
        """
        Get available Google Calendar and event colors.
        """

        return calendar_service.get_colors()

    # =========================================================
    # Settings
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_list_settings(
        max_results: int = 100,
        page_token: str | None = None,
    ):
        """
        List Calendar settings for the authenticated user.
        """

        return calendar_service.list_settings(
            max_results=max_results,
            page_token=page_token,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_get_setting(
        setting_id: str,
    ):
        """
        Get a specific Calendar setting.
        """

        return calendar_service.get_setting(
            setting_id=setting_id,
        )

    # =========================================================
    # ACL
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_list_acl(
        calendar_id: str = "primary",
        max_results: int = 100,
        page_token: str | None = None,
    ):
        """
        List access-control rules for a calendar.
        """

        return calendar_service.list_acl(
            calendar_id=calendar_id,
            max_results=max_results,
            page_token=page_token,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_get_acl_rule(
        rule_id: str,
        calendar_id: str = "primary",
    ):
        """
        Get a calendar ACL rule.
        """

        return calendar_service.get_acl_rule(
            rule_id=rule_id,
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_create_acl_rule(
        scope_type: str,
        role: str,
        calendar_id: str = "primary",
        scope_value: str | None = None,
    ):
        """
        Create a Calendar ACL rule.

        scope_type:
            default
            user
            group
            domain

        role:
            none
            freeBusyReader
            reader
            writerWithoutPrivateAccess
            writer
            owner
        """

        return calendar_service.create_acl_rule(
            scope_type=scope_type,
            role=role,
            calendar_id=calendar_id,
            scope_value=scope_value,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_update_acl_rule(
        rule_id: str,
        role: str,
        calendar_id: str = "primary",
    ):
        """
        Update a Calendar ACL rule.
        """

        return calendar_service.update_acl_rule(
            rule_id=rule_id,
            role=role,
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_delete_acl_rule(
        rule_id: str,
        calendar_id: str = "primary",
    ):
        """
        Delete a Calendar ACL rule.
        """

        return calendar_service.delete_acl_rule(
            rule_id=rule_id,
            calendar_id=calendar_id,
        )
        