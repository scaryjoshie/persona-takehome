# Storage

SQLite via SQLModel and SQLAlchemy. In-memory per-user cache as the working copy, loaded on first contact, written through on every change. SQLite writes take about a millisecond, so plain synchronous SQLModel sessions inside the async handlers are fine at this scale. Postgres is overkill: the store is an append-only log plus a slots object per user, and no other process reads it.

Users are identified by a phone number they type at the start (normalized). Multiple browser tabs can attach to the same user.

## Tables

```python
class User(SQLModel, table=True):
    phone: str = Field(primary_key=True)
    created_at: datetime
    # slots as plain columns, written only by tools
    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail_status: str = "not_asked"       # not_asked | link_sent | connected | skipped
    graduated: bool = False
    call_status: str = "none"             # none | ringing | connected
    floor: str = "text"

class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    ts: datetime
    origin: str
    channel: str
    kind: str
    payload: dict = Field(sa_column=Column(JSON))   # validated into the Pydantic union on read

class Integration(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_phone: str = Field(foreign_key="user.phone", index=True)
    provider: str                      # "google" today, "canvas" later
    account_id: str                    # email for Google
    status: str                        # connected | revoked | failed
    credentials: bytes                 # encrypted blob, provider-specific shape
    scopes: str
    meta: dict = Field(sa_column=Column(JSON))
    connected_at: datetime
    revoked_at: datetime | None = None

class Call(SQLModel, table=True):
    id: str = Field(primary_key=True)     # OpenAI call_id
    user_phone: str = Field(foreign_key="user.phone", index=True)
    reason: str | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
```

The event log records *that* something happened (Gmail connected, call ended); the Integration and Call tables hold the records. The OAuth callback writes both: one Integration row and one event so the conversation knows.

One generic Integration table rather than a Google-specific one, because providers are definitions in code (auth flow, scopes, capabilities) and per-user connections are rows. See [10-gmail-and-integrations.md](10-gmail-and-integrations.md).

## Volume

A five-minute call produces on the order of a hundred utterance rows; a text session maybe fifty. Nothing needs batching.

## Ordering

One consumer task per user drains one queue. The store assigns the sequence inside that consumer. Each socket delivers its own messages in order, so the only cross-source ordering is arrival at the queue, which is the only meaningful order. See [04-events-and-types.md](04-events-and-types.md) for the transcription out-of-order fix.

## Credentials encryption

- Fernet from the `cryptography` library, key from an environment variable, encrypting the credentials blob before it hits SQLite. In production the key would come from a KMS.
- Never logged, never sent to the browser, deleted on revoke. Refresh token requested only with minimal scopes.
- OAuth basics: state parameter bound to the user, PKCE.
- Honest framing for the write-up: encryption at rest with the key on the same box protects against the database file leaking, not against the box being compromised. That is still the right layer for a take-home.

## Persistence across restarts

On Fly.io the SQLite file lives on a small persistent volume. On startup, rebuild the per-user cache lazily from the tables. Nothing in flight (open calls, debounce timers) survives a restart; a call in progress at restart becomes a `call ended: server restart` event on first contact.
