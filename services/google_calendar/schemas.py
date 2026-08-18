from typing import Any


def event_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "calendar_id": {
                "type": "string",
                "description": (
                    "Calendar ID. Use 'primary' "
                    "for the user's primary calendar."
                ),
            },
            "summary": {
                "type": "string",
                "description": "Event title.",
            },
            "description": {
                "type": "string",
                "description": "Optional event description.",
            },
            "location": {
                "type": "string",
                "description": "Optional event location.",
            },
            "start": {
                "type": "string",
                "description": (
                    "RFC3339 date-time for timed events, "
                    "for example 2026-08-20T15:00:00+05:30. "
                    "For all-day events use YYYY-MM-DD."
                ),
            },
            "end": {
                "type": "string",
                "description": (
                    "RFC3339 date-time for timed events, "
                    "or YYYY-MM-DD for all-day events."
                ),
            },
            "time_zone": {
                "type": "string",
                "description": (
                    "IANA timezone, for example "
                    "Asia/Kolkata."
                ),
            },
            "attendees": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": (
                    "Optional attendee email addresses."
                ),
            },
            "recurrence": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": (
                    "Optional RFC5545 recurrence rules."
                ),
            },
            "reminders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": [
                                "email",
                                "popup",
                            ],
                        },
                        "minutes": {
                            "type": "integer",
                        },
                    },
                    "required": [
                        "method",
                        "minutes",
                    ],
                },
            },
            "color_id": {
                "type": "string",
                "description": "Optional Google Calendar color ID.",
            },
            "visibility": {
                "type": "string",
                "enum": [
                    "default",
                    "public",
                    "private",
                ],
            },
        },
        "required": [
            "summary",
            "start",
            "end",
        ],
    }