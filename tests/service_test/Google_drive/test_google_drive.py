from services.google_drive.services import GoogleDriveService


def main():
    drive = GoogleDriveService()

    result = drive.search_files("PYfiels ajbnasdn")

    print(f"Found {result.total} files")

    for file in result.files:
        print(
            f"- {file.name} "
            f"({file.mime_type}) "
            f"[{file.id}]"
        )

if __name__ == "__main__":
    main()