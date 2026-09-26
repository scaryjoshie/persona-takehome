"""A channel that prints. For the CLI and the harness."""

from __future__ import annotations


class ConsoleChannel:
    def __init__(self, agent_label: str = "agent") -> None:
        self.label = agent_label

    async def send(self, phone: str, text: str) -> None:
        print(f"\r{self.label}: {text}")

    async def set_typing(self, phone: str, active: bool) -> None:
        if active:
            print("\r…", end="", flush=True)
