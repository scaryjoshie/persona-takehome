"""Integrations: services a user connected (other than Google), as data the agent writes.

See docs/proposed-design/17-integrations.md. The rest of the app touches this package at three
points: the job agent's extra toolset (tools.py), one "connected apps" line for the chat agent,
and the secure secret form (routes.py).
"""
