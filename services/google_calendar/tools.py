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
        # The docstring below is INDEXED BY THE ROUTER, so the words
        # in it are routing vocabulary whether or not they were meant
        # to be. It used to open "Check free/busy information...", and
        # "check" appeared in no other docstring on the server - so
        # IDF treated an ordinary command verb as decisive and this
        # three-argument tool became the top match for "check my
        # calendar connection". Note what is absent below, and keep it
        # absent: "check", "connection", "test".
        """
        Show when one or more calendars are free or busy.

        Needs an explicit time range (time_min, time_max) and the
        calendars to look at, so it answers "is this slot free?".
        To simply list the available calendars, use
        google_calendar_list_calendars, which needs no arguments.
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
        
    # =========================================================
    # Calendar management
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_create_calendar(
        summary: str,
        description: str | None = None,
        time_zone: str | None = None,
    ):
        """
        Create a new calendar owned by the authenticated user.

        `summary` is the calendar's name. `time_zone` takes an IANA
        name such as 'Europe/London'; omitted, the account default is
        used.
        """

        return calendar_service.create_calendar(
            summary=summary,
            description=description,
            time_zone=time_zone,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_update_calendar(
        calendar_id: str,
        summary: str | None = None,
        description: str | None = None,
        location: str | None = None,
        time_zone: str | None = None,
    ):
        """
        Update a calendar's own settings.

        Only the fields you supply are changed. This edits the
        calendar itself, which everyone it is shared with sees - not
        your personal view of it. For that, use
        google_calendar_update_subscription.
        """

        return calendar_service.update_calendar(
            calendar_id=calendar_id,
            summary=summary,
            description=description,
            location=location,
            time_zone=time_zone,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_delete_calendar(
        calendar_id: str,
    ):
        """
        Permanently delete a calendar and every event on it.

        THIS CANNOT BE UNDONE, and it affects everyone the calendar is
        shared with, not only you. If you only want it out of your own
        sidebar, use google_calendar_remove_subscription instead.

        The primary calendar cannot be deleted.
        """

        return calendar_service.delete_calendar(
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_clear_calendar(
        calendar_id: str = "primary",
    ):
        """
        Erase every event on the primary calendar, without removing
        the calendar itself.

        THIS CANNOT BE UNDONE. Google only permits it on the primary
        calendar.
        """

        return calendar_service.clear_calendar(
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_add_subscription(
        calendar_id: str,
        color_id: str | None = None,
        hidden: bool = False,
        selected: bool = True,
    ):
        """
        Subscribe to an existing calendar so it appears in your list.

        Use this for a calendar somebody shared with you. It does not
        create anything - google_calendar_create_calendar does that.
        """

        return calendar_service.add_calendar_to_list(
            calendar_id=calendar_id,
            color_id=color_id,
            hidden=hidden,
            selected=selected,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_remove_subscription(
        calendar_id: str,
    ):
        """
        Un-subscribe from a calendar, removing it from your list.

        The calendar and all its events continue to exist and other
        people are unaffected. This is the safe counterpart to
        google_calendar_delete_calendar.
        """

        return calendar_service.remove_calendar_from_list(
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_update_subscription(
        calendar_id: str,
        color_id: str | None = None,
        hidden: bool | None = None,
        selected: bool | None = None,
        summary_override: str | None = None,
    ):
        """
        Change how a calendar appears in YOUR list.

        Colour, visibility, and the name you personally see. Nobody
        else is affected by any of it.
        """

        return calendar_service.update_calendar_list_entry(
            calendar_id=calendar_id,
            color_id=color_id,
            hidden=hidden,
            selected=selected,
            summary_override=summary_override,
        )

    # =========================================================
    # Events - search, quick add, move, recurrence, RSVP
    # =========================================================

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_search_events(
        query: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
    ):
        """
        Search a calendar's events by free text.

        Matches against titles, descriptions, locations and attendees.
        Narrow it with time_min / time_max, both RFC3339 timestamps.
        """

        return calendar_service.search_events(
            query=query,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_add_event(
        text: str,
        calendar_id: str = "primary",
    ):
        """
        Create an event from a plain sentence.

        Pass something like "Lunch with Sam tomorrow at 1pm" and
        Google works out the date, time and title. Prefer this over
        google_calendar_create_event when the user gave a natural
        phrase rather than exact times - it avoids having to compute a
        timestamp, which is where the wrong year or timezone creeps
        in.
        """

        return calendar_service.quick_add_event(
            text=text,
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_move_event(
        event_id: str,
        destination_calendar_id: str,
        calendar_id: str = "primary",
    ):
        """
        Move an event from one calendar to another.

        `calendar_id` is where the event is now; the event keeps its
        id and its attendees.
        """

        return calendar_service.move_event(
            event_id=event_id,
            destination_calendar_id=destination_calendar_id,
            calendar_id=calendar_id,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_list_event_instances(
        event_id: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
    ):
        """
        List the individual occurrences of a repeating event.

        A repeating event has one id, but each occurrence has its own.
        To change or cancel a SINGLE occurrence, list them here and
        use that occurrence's id - passing the parent id would change
        the whole series.
        """

        return calendar_service.list_event_instances(
            event_id=event_id,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )

    @mcp.tool()
    @handle_calendar_errors
    async def google_calendar_set_invitation_response(
        event_id: str,
        response: str,
        calendar_id: str = "primary",
        comment: str | None = None,
    ):
        """
        Reply to an invitation: 'accepted', 'declined' or 'tentative'.

        The organiser and every other attendee is notified of the
        answer, so this is visible to other people.
        """

        return calendar_service.respond_to_event(
            event_id=event_id,
            response=response,
            calendar_id=calendar_id,
            comment=comment,
        )
