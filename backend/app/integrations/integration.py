"""An integration: one user's access to one service, written by a background job as data.

It's built from a few kinds of parts (code), and each app is a combination of them (data):
`auth` says how we're let in, `api` how we act. Secret values are never in here: an ApiKey only
names a secret, whose value lives encrypted in the integration's row, and an endpoint refers to
it as {secret:name}. More kinds of parts (Composio, MCP, a browser login) join the unions later.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Name = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")


class ApiKey(BaseModel):
    """A key, token or webhook URL they create and paste into the secure form."""

    kind: Literal["api_key"] = "api_key"
    secret: str = Name  # its name, e.g. "token" or "webhook_url"
    about: str  # what it is and where they get it; shown to them on the secure form


class Param(BaseModel):
    name: str = Name  # what the job calls it
    key: str = ""  # what the service calls it, if different ("context_codes[]")
    where: Literal["query", "json", "form", "path"] = "json"
    about: str = ""
    required: bool = True

    @property
    def sent_as(self) -> str:
        return self.key or self.name


class Endpoint(BaseModel):
    name: str = Name  # the tool is called <app>_<name>
    about: str  # what it does, for the job agent
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = ""  # after base_url; may hold {param} and {secret:name}
    params: list[Param] = []
    body: dict[str, Any] = {}  # fixed JSON fields sent every time (a GraphQL query, a parent)
    effect: Literal["read", "act"] = "act"  # an act waits for their yes


class HttpApi(BaseModel):
    kind: Literal["http"] = "http"
    base_url: str  # https only; may hold {secret:name} (a webhook URL is a secret)
    allowed_hosts: list[str]  # the only hosts it may reach; fixed once it's ready
    headers: dict[str, str] = {}  # may hold {secret:name}, e.g. "Bearer {secret:token}"
    endpoints: list[Endpoint] = []


Status = Literal["setting_up", "ready", "broken"]


class Integration(BaseModel):
    model_config = ConfigDict(extra="ignore")  # rows written by older code still load

    id: str = ""  # set when saved
    app: str = Name  # "slack", "canvas"
    account: str = ""  # which workspace or account, if they have more than one
    notes: str = Field(default="", max_length=3000)  # how to use it for them: what worked, quirks
    status: Status = "setting_up"
    auth: list[ApiKey] = []
    api: HttpApi | None = None
    template: str = ""  # built from a shipped template: its api is ours, not the agent's
    host: str = ""  # a template's per-user host ("canvas.school.edu")

    def reads(self, endpoint: Endpoint) -> bool:
        """Runs without asking. An agent-written endpoint reads only with a GET; a shipped
        template's POST can be a read (a search), because its api can't be rewritten."""
        return endpoint.effect == "read" and (endpoint.method == "GET" or bool(self.template))

    @property
    def secret_names(self) -> list[str]:
        return [part.secret for part in self.auth]

    def describe(self) -> str:
        """One line for the chat agent."""
        label = f"{self.app} ({self.account})" if self.account else self.app
        can = ", ".join(
            e.about.rstrip(".").lower() for e in (self.api.endpoints if self.api else [])
        )
        state = {"ready": "connected", "setting_up": "being set up", "broken": "needs fixing"}
        return f"{label}: {state[self.status]}" + (
            f"; can {can}" if can and self.status == "ready" else ""
        )
