from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# CHANNELS
# ============================================================

class ListChannelsInput(BaseModel):
    exclude_archived: bool = True
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
    )


class GetChannelInput(BaseModel):
    channel_id: str


class CreateChannelInput(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=80,
    )

    is_private: bool = False


class JoinChannelInput(BaseModel):
    channel_id: str


class LeaveChannelInput(BaseModel):
    channel_id: str


class ArchiveChannelInput(BaseModel):
    channel_id: str


# ============================================================
# MESSAGES
# ============================================================

class SendMessageInput(BaseModel):
    channel_id: str

    text: str = Field(
        min_length=1,
    )


class UpdateMessageInput(BaseModel):
    channel_id: str

    timestamp: str

    text: str = Field(
        min_length=1,
    )


class DeleteMessageInput(BaseModel):
    channel_id: str

    timestamp: str


class ChannelHistoryInput(BaseModel):
    channel_id: str

    limit: int = Field(
        default=50,
        ge=1,
        le=999,
    )

    oldest: str | None = None
    latest: str | None = None


class ThreadRepliesInput(BaseModel):
    channel_id: str

    timestamp: str

    limit: int = Field(
        default=50,
        ge=1,
        le=999,
    )


# ============================================================
# USERS
# ============================================================

class GetUserInput(BaseModel):
    user_id: str


class ListUsersInput(BaseModel):
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
    )


# ============================================================
# FILES
# ============================================================

class ListFilesInput(BaseModel):
    channel_id: str | None = None

    user_id: str | None = None

    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
    )


class GetFileInput(BaseModel):
    file_id: str


class DeleteFileInput(BaseModel):
    file_id: str


class UploadFileInput(BaseModel):
    file_path: str

    channel_id: str | None = None

    title: str | None = None

    initial_comment: str | None = None