from services.google_calendar.services import GoogleCalendarService


def main():
    service = GoogleCalendarService()

    print("Testing Google Calendar...")

    calendars = service.list_calendars()

    print(calendars)


if __name__ == "__main__":
    main()