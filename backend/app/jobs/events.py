"""A background job's life as events: started, asked them something, was told something, ended.

The job's own conversation (its model messages) lives in the job table; these are what the
chat agent sees. A question and an ending are routed, so they reach them by text or on a call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.events.payload import Payload, Role, Turn


class JobStarted(Payload):
    kind: Literal["job_started"] = "job_started"
    routes = False  # the agent's own doing

    job: str
    goal: str

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, f"you started background task {self.job}: {self.goal}")


class JobAsked(Payload):
    """The job needs something from them. It waits until it's told or cancelled."""

    kind: Literal["job_asked"] = "job_asked"

    job: str
    question: str

    def describe(self) -> str:
        return f"a background task needs the user to answer: {self.question}"

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, f"background task {self.job} asks them: {self.question}")


class JobTold(Payload):
    """Their answer, or anything else they said the job should know, passed on to it."""

    kind: Literal["job_told"] = "job_told"
    routes = False

    job: str
    text: str

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, f"you told background task {self.job}: {self.text}")


class JobEnded(Payload):
    kind: Literal["job_ended"] = "job_ended"

    job: str
    outcome: Literal["done", "failed", "cancelled"]
    text: str = ""  # what it found or did; why it failed

    def should_route(self) -> bool:
        return self.outcome != "cancelled"  # a cancel is the agent's own doing

    def describe(self) -> str:
        return (
            "a background task finished" if self.outcome == "done" else "a background task failed"
        )

    def turn(self, at: datetime) -> Turn | None:
        match self.outcome:
            case "done":
                return Turn(Role.NOTE, f"background task {self.job} finished: {self.text}")
            case "failed":
                return Turn(Role.NOTE, f"background task {self.job} failed: {self.text}")
            case "cancelled":
                return Turn(Role.NOTE, f"background task {self.job} was cancelled")
