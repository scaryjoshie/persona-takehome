"""Integrations storage: rows per user, secrets encrypted in their own column, the secure
links a job sends when it needs a secret, and the sign-in links for apps that use Composio.

A secret's value only ever travels from their browser (the secure form) to the vault and from
the vault into a request; the agents never see it.
"""

from __future__ import annotations

import json
import secrets as token_source
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from cryptography.fernet import Fernet
from sqlmodel import col, select

from app.database import SessionFactory
from app.integrations import templates
from app.integrations.composio import Composio, ComposioError
from app.integrations.integration import HttpApi, Integration
from app.integrations.models import IntegrationRow
from app.timers import Clock

# (phone, text): text them from the service (a secure link), recorded like any bubble.
Texter = Callable[[str, str], Awaitable[None]]
# (job, question id, result): a secret they saved resumes the job that asked for it.
Resolver = Callable[[str, str, str], Awaitable[bool]]
LINK_LIFE = timedelta(minutes=9)  # a Composio sign-in link works for about 10


@dataclass(frozen=True)
class SecretRequest:
    phone: str
    integration: str
    name: str
    about: str
    app: str
    job: str
    question: str  # the job's pending tool call


@dataclass(frozen=True)
class SignIn:
    """A sign-in link a job texted; Composio sends them back to our callback when they're done."""

    phone: str
    integration: str
    app: str
    job: str
    question: str
    at: datetime | None = None  # when the link was made


class Integrations:
    def __init__(
        self,
        db: SessionFactory,
        *,
        clock: Clock,
        key: str | None,
        base_url: str,
        composio: Composio | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._fernet = Fernet(key) if key else None
        self._base_url = base_url
        self.text: Texter | None = None  # set at assembly
        self.resolve: Resolver | None = None  # set at assembly: the jobs runner
        # Open secure links, by token. In memory: a restart voids them, and the job asks again.
        self._requests: dict[str, SecretRequest] = {}
        self.composio = composio  # sign-ins to apps; None without COMPOSIO_API_KEY
        self._sign_ins: dict[str, SignIn] = {}  # in memory too

    @property
    def configured(self) -> bool:
        """Secrets need CREDENTIALS_KEY."""
        return self._fernet is not None

    # ---- rows ----------------------------------------------------------------------------

    async def all(self, phone: str) -> list[Integration]:
        async with self._db() as s:
            query = select(IntegrationRow).where(IntegrationRow.phone == phone)
            rows = (await s.exec(query.order_by(col(IntegrationRow.created_at)))).all()
        return [_load(row) for row in rows]

    async def get(self, phone: str, integration: str) -> Integration | None:
        async with self._db() as s:
            row = await s.get(IntegrationRow, integration)
        return _load(row) if row is not None and row.phone == phone else None

    async def save(self, phone: str, integration: Integration) -> Integration:
        """Create, or replace the one with the same id (or app and account). Once it's
        ready, its allowed hosts can shrink but never grow: notes written by an agent that
        read web pages can't widen where it may send their secrets."""
        if integration.template:  # its api and auth are the template's, whatever was sent
            if integration.template not in templates.TEMPLATES:
                raise ValueError(f"there's no template {integration.template}")
            shipped = templates.build(integration.template, host=integration.host)
            integration = integration.model_copy(update={"api": shipped.api, "auth": shipped.auth})
        existing = await self._match(phone, integration)
        if (
            existing is not None
            and existing.status == "ready"
            and isinstance(integration.api, HttpApi)
        ):
            before = set(existing.api.allowed_hosts if isinstance(existing.api, HttpApi) else [])
            if not set(integration.api.allowed_hosts) <= before:
                raise ValueError("a connected integration's allowed hosts can't grow")
        saved = integration.model_copy(
            update={"id": existing.id if existing else uuid.uuid4().hex[:8]}
        )
        now = self._clock()
        async with self._db() as s, s.begin():
            row = await s.get(IntegrationRow, saved.id)
            if row is None:
                row = IntegrationRow(
                    id=saved.id, phone=phone, app=saved.app, created_at=now, updated_at=now
                )
            row.app, row.account, row.status = saved.app, saved.account, saved.status
            row.data = saved.model_dump(mode="json", exclude={"id", "app", "account", "status"})
            row.updated_at = now
            s.add(row)
        return saved

    async def remove(self, phone: str, integration: str) -> bool:
        async with self._db() as s, s.begin():
            row = await s.get(IntegrationRow, integration)
            if row is None or row.phone != phone:
                return False
            await s.delete(row)
        return True

    async def _match(self, phone: str, integration: Integration) -> Integration | None:
        if integration.id:
            return await self.get(phone, integration.id)
        for other in await self.all(phone):
            if other.app == integration.app and other.account == integration.account:
                return other
        return None

    # ---- secrets ---------------------------------------------------------------------------

    async def secrets(self, phone: str, integration: str) -> dict[str, str]:
        async with self._db() as s:
            row = await s.get(IntegrationRow, integration)
        if row is None or row.phone != phone or not row.secrets or self._fernet is None:
            return {}
        return json.loads(self._fernet.decrypt(row.secrets.encode()))

    async def set_secret(self, phone: str, integration: str, name: str, value: str) -> None:
        assert self._fernet is not None
        stored = await self.secrets(phone, integration)
        stored[name] = value
        async with self._db() as s, s.begin():
            row = await s.get(IntegrationRow, integration)
            if row is None or row.phone != phone:
                raise LookupError(f"no integration {integration}")
            row.secrets = self._fernet.encrypt(json.dumps(stored).encode()).decode()
            s.add(row)

    async def missing(self, phone: str, integration: Integration) -> list[str]:
        have = await self.secrets(phone, integration.id)
        return [name for name in integration.secret_names if not have.get(name)]

    # ---- secure links ------------------------------------------------------------------------

    def link_for(self, request: SecretRequest) -> str:
        token = token_source.token_urlsafe(24)
        self._requests[token] = request
        return f"{self._base_url}/api/secret/{token}"

    def request(self, token: str) -> SecretRequest | None:
        return self._requests.get(token)

    async def fulfil(self, token: str, value: str) -> SecretRequest | None:
        """They submitted the form: store the value, then let the job carry on."""
        request = self._requests.pop(token, None)
        if request is None:
            return None
        await self.set_secret(request.phone, request.integration, request.name, value)
        if self.resolve is not None:
            await self.resolve(request.job, request.question, f"they saved {request.name}")
        return request

    # ---- sign-ins (Composio) ------------------------------------------------------------------

    async def composio_user(self, phone: str) -> str:
        """Who they are at Composio: made up once, kept on their first sign-in's row."""
        for integration in await self.all(phone):
            if integration.composio is not None:
                return integration.composio.user_id
        return f"u_{token_source.token_hex(12)}"

    def callback_for(self, sign_in: SignIn) -> str:
        token = token_source.token_urlsafe(24)
        self._sign_ins[token] = replace(sign_in, at=self._clock())
        return f"{self._base_url}/api/signed-in/{token}"

    def rewait(self, integration: str, job: str, question: str) -> bool:
        """The link they got in the last few minutes still works: its callback resumes this
        wait instead, so they aren't texted another."""
        now = self._clock()
        for token, sign_in in self._sign_ins.items():
            if sign_in.integration == integration and sign_in.at and now - sign_in.at < LINK_LIFE:
                self._sign_ins[token] = replace(sign_in, job=job, question=question)
                return True
        return False

    async def signed_in(self, token: str) -> tuple[SignIn, bool] | None:
        """Composio sent them back. It counts only if Composio itself says the account is
        ACTIVE (the redirect's own status could be anyone's); either way the job hears it."""
        sign_in = self._sign_ins.pop(token, None)
        if sign_in is None or self.composio is None:
            return None
        found = await self.get(sign_in.phone, sign_in.integration)
        part = found.composio if found else None
        state = "not started"
        if part is not None and part.connected_account_id:
            try:
                state = await self.composio.status(part.connected_account_id)
            except ComposioError as exc:
                state = str(exc)
        ok = state == "ACTIVE"
        result = (
            f"they signed in to {sign_in.app}: the account is active. Check it with one read "
            "(app_run), then mark_ready."
            if ok
            else f"their {sign_in.app} sign-in didn't go through (status {state})"
        )
        if self.resolve is not None:
            await self.resolve(sign_in.job, sign_in.question, result)
        return sign_in, ok


def _load(row: IntegrationRow) -> Integration:
    return Integration.model_validate(
        {**row.data, "id": row.id, "app": row.app, "account": row.account, "status": row.status}
    )
