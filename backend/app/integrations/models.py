"""The integration table: real columns only for what code filters on; the rest is JSON."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class IntegrationRow(SQLModel, table=True):
    __tablename__ = "integration"  # pyright: ignore[reportAssignmentType]

    id: str = Field(primary_key=True)
    phone: str = Field(index=True)
    app: str
    account: str = ""
    status: str = "setting_up"
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # auth, api, notes
    secrets: str = ""  # Fernet-encrypted JSON {name: value}; never in `data`, events or prompts
    created_at: datetime
    updated_at: datetime
