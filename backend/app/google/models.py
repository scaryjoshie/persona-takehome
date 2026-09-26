"""The google_account table: a connected Google account's refresh token, encrypted."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class GoogleAccountRow(SQLModel, table=True):
    __tablename__ = "google_account"  # pyright: ignore[reportAssignmentType]

    phone: str = Field(primary_key=True)
    email: str = ""
    refresh_token: str = ""  # Fernet-encrypted with CREDENTIALS_KEY
    connected_at: datetime | None = None
