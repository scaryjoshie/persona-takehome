# Storage

Revised 2026-09-26 after the data-layer review (conventions borrowed from cado backend-2).

## Runtime versus data

**Data lives in SQLite, full stop.** Events and user state are read through service functions when needed and written inside short transactions. There is no in-memory store, no load-on-first-contact, no eviction, no rebuild-after-restart story, because there is nothing to rebuild. A read of a few hundred rows from a local SQLite file is well under a millisecond, so a cache would only be a second copy of the truth.

**Runtime holds only what can live nowhere but in a process**, per user: a lock so events are handled one at a time, the text driver's debounce timer and running task, the live voice session while a call is up, and the sockets to push to. If the process restarts, the live things are gone, and that is correct: the sockets and the call died with it.

## Layout and rules

- `app/database.py`: one async engine (`sqlite+aiosqlite`), one session factory, schema creation. Nothing else opens connections.
- Each section has `models.py` (SQLModel tables, persistence only, plain names, string enums as string columns) and `service.py` (module functions that take a session, touch only their own tables, never commit).
- `app/pipeline.py`: `submit` is the one door for every event. It saves the event in one short transaction, first applying it to the user's state if it is one of the kinds that change state (call events → call state and floor; slot, Gmail, and graduated events → slots), then pushes it to the browser and routes it. An event that would change nothing is dropped.
- `app/runtime.py`: `Runtime` (lock, drivers, voice session, subscribers) and `Runtimes` (registry by phone).
- The user is a frozen value (`app/users/types.py`) built from its row; it is replaced, never mutated.
- Migrations: none. Tables are created at startup. A migration flow for a SQLite demo is ceremony.
- Tests use a temporary SQLite file per test with the session factory injected, the same path production uses.

## Tables

```python
class UserRow(SQLModel, table=True):          # app/users/models.py
    __tablename__ = "user"
    phone: str = Field(primary_key=True)
    created_at: datetime
    agent_name: str | None; user_name: str | None; help_need: str | None
    gmail: str | None                          # GmailPhase value; None = not asked
    gmail_email: str | None; graduated: bool = False
    call_phase: str = "none"; call_reason: str | None; call_id: str | None
    call_initiated_by: str | None; call_started_at: datetime | None; call_ended_at: datetime | None
    floor: str = "text"

class EventRow(SQLModel, table=True):         # app/events/models.py
    __tablename__ = "event"
    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    seq: int = Field(index=True)               # per user, max+1 inside the transaction
    ts: datetime; origin: str; channel: str; kind: str
    payload: dict = Field(sa_column=Column(JSON))

# Integration (app/gmail/models.py, slice 4): user_phone, provider, account_id, status,
#   credentials (Fernet-encrypted bytes), scopes, meta JSON, connected_at, revoked_at.
```

The event log records *that* something happened (Gmail connected, call ended); the user row holds the current state and the Integration table the credentials. The OAuth callback writes both: one Integration row and one event so the conversation knows.

One generic Integration table rather than a Google-specific one, because providers are definitions in code (auth flow, scopes, capabilities) and per-user connections are rows. See [10-gmail-and-integrations.md](10-gmail-and-integrations.md).

## Credentials encryption

- Fernet from the `cryptography` library, key from an environment variable, encrypting the credentials blob before it hits SQLite. In production the key would come from a KMS.
- Never logged, never sent to the browser, deleted on revoke. Refresh token requested only with minimal scopes.
- OAuth basics: state parameter bound to the user, PKCE.
- Honest framing for the write-up: encryption at rest with the key on the same box protects against the database file leaking, not against the box being compromised. That is still the right layer for a take-home.

