# Persona onboarding take-home

A web simulation of Persona's onboarding: collects an agent name over text, then attempts a voice call to collect the user's name, a connected Gmail, and something they need help with, falling back to text at every step.

- `docs/proposed-design/` — design notes (draft, see the README there)
- `backend/` — FastAPI + pydantic-ai, one long-running process, SQLite via SQLModel
- `frontend/` — Vite + React phone UI (see `docs/proposed-design/14-frontend-contract.md`)
