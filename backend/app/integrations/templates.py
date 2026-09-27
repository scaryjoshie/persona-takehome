"""Known-good integrations for common services, so setting one up is a template and a secret
rather than research. The api comes from here and is rebuilt from here on every save, so a job
can't rewrite what a template may reach; it only adds notes. Sources for each are in
docs/proposed-design/17-integrations.md.

A template's base_url and allowed_hosts may hold {host}: a per-user host (a school's Canvas).
"""

from __future__ import annotations

import re

from app.integrations.integration import ApiKey, Auth, Endpoint, HttpApi, Integration, Param

HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
BEARER = {"Authorization": "Bearer {secret:token}"}


def _token(about: str) -> list[Auth]:
    return [ApiKey(secret="token", about=about)]


def _webhook(about: str) -> list[Auth]:
    return [ApiKey(secret="webhook_url", about=about)]


TEMPLATES: dict[str, Integration] = {
    "slack_webhook": Integration(
        app="slack",
        auth=_webhook(
            "Slack incoming webhook URL (in Slack: create an app at api.slack.com/apps, turn on "
            "Incoming Webhooks, Add New Webhook, pick the channel, copy the URL)"
        ),
        api=HttpApi(
            base_url="{secret:webhook_url}",
            allowed_hosts=["hooks.slack.com"],
            endpoints=[
                Endpoint(
                    name="post",
                    about="Post a message to the channel the webhook is for",
                    method="POST",
                    params=[Param(name="text", about="the message")],
                )
            ],
        ),
    ),
    "discord_webhook": Integration(
        app="discord",
        auth=_webhook(
            "Discord webhook URL (Server Settings, Integrations, Webhooks, New Webhook, pick "
            "the channel, Copy Webhook URL)"
        ),
        api=HttpApi(
            base_url="{secret:webhook_url}",
            allowed_hosts=["discord.com"],
            endpoints=[
                Endpoint(
                    name="post",
                    about="Post a message to the channel the webhook is for",
                    method="POST",
                    params=[Param(name="content", about="the message")],
                )
            ],
        ),
    ),
    "todoist": Integration(
        app="todoist",
        auth=_token("Todoist API token (Settings, Integrations, Developer, Copy API token)"),
        api=HttpApi(
            base_url="https://api.todoist.com/api/v1",
            allowed_hosts=["api.todoist.com"],
            headers=BEARER,
            endpoints=[
                Endpoint(
                    name="tasks",
                    about="Their tasks matching a filter",
                    path="/tasks/filter",
                    params=[
                        Param(
                            name="query", where="query", about='a filter, like "today | overdue"'
                        ),
                    ],
                    effect="read",
                ),
                Endpoint(name="projects", about="Their projects", path="/projects", effect="read"),
                Endpoint(
                    name="quick_add",
                    about="Add a task from natural language (due dates and #project included)",
                    method="POST",
                    path="/tasks/quick",
                    params=[Param(name="text", about='e.g. "call mom tomorrow 5pm #Personal"')],
                ),
                Endpoint(
                    name="complete",
                    about="Mark a task done",
                    method="POST",
                    path="/tasks/{task_id}/close",
                    params=[Param(name="task_id", where="path", about="the task's id")],
                ),
            ],
        ),
    ),
    "notion": Integration(
        app="notion",
        auth=_token("Notion personal access token (notion.so/developers/tokens)"),
        api=HttpApi(
            base_url="https://api.notion.com/v1",
            allowed_hosts=["api.notion.com"],
            headers={**BEARER, "Notion-Version": "2026-03-11"},
            endpoints=[
                Endpoint(
                    name="search",
                    about="Find pages by title",
                    method="POST",
                    path="/search",
                    params=[Param(name="query", about="words in the title")],
                    effect="read",
                ),
                Endpoint(
                    name="page_content",
                    about="A page's content",
                    path="/blocks/{page_id}/children",
                    params=[Param(name="page_id", where="path", about="the page's id")],
                    effect="read",
                ),
                Endpoint(
                    name="append",
                    about="Add content to the end of a page",
                    method="PATCH",
                    path="/blocks/{page_id}/children",
                    params=[
                        Param(name="page_id", where="path", about="the page's id"),
                        Param(name="children", about="Notion block objects to add"),
                    ],
                ),
            ],
        ),
    ),
    "canvas": Integration(
        app="canvas",
        auth=_token(
            "Canvas access token (Account, Settings, Approved Integrations, New Access Token; "
            "some schools turn this off)"
        ),
        api=HttpApi(
            base_url="https://{host}/api/v1",
            allowed_hosts=["{host}"],
            headers=BEARER,
            endpoints=[
                Endpoint(
                    name="todo", about="Their to-do list", path="/users/self/todo", effect="read"
                ),
                Endpoint(
                    name="upcoming",
                    about="Upcoming assignments and events",
                    path="/users/self/upcoming_events",
                    effect="read",
                ),
                Endpoint(
                    name="missing",
                    about="Assignments they haven't turned in",
                    path="/users/self/missing_submissions",
                    effect="read",
                ),
            ],
        ),
    ),
}


def build(name: str, *, host: str = "", account: str = "", notes: str = "") -> Integration:
    """An integration from a template, with its per-user host filled in."""
    template = TEMPLATES[name]
    assert isinstance(template.api, HttpApi)
    needs_host = "{host}" in template.api.base_url
    if needs_host and not HOST.match(host.lower()):
        raise ValueError(f"{name} needs their host, like canvas.school.edu")
    api = template.api.model_copy(
        update={
            "base_url": template.api.base_url.replace("{host}", host.lower()),
            "allowed_hosts": [
                h.replace("{host}", host.lower()) for h in template.api.allowed_hosts
            ],
        }
    )
    return template.model_copy(
        update={
            "template": name,
            "host": host.lower() if needs_host else "",
            "api": api,
            "account": account,
            "notes": notes,
        }
    )


def summary() -> str:
    """One line per template, for the job agent."""

    def host(t: Integration) -> str:
        return (
            ", needs their host"
            if isinstance(t.api, HttpApi) and "{host}" in t.api.base_url
            else ""
        )

    return "; ".join(f"{name} ({t.app}{host(t)})" for name, t in TEMPLATES.items())
