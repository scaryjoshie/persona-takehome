# Gmail and integrations

## OAuth facts (verified against Google docs, 2026-09-25)

- An app in **production** status requesting only non-sensitive scopes (`openid`, `email`, `profile`) is available to any Google account, needs no verification, and shows no "unverified app" warning. Brand verification is only for showing a logo and display name.
- `gmail.readonly`, `gmail.metadata`, and `gmail.compose` are **restricted** scopes: verification plus a CASA security assessment for production, recertified yearly, "can potentially take several weeks." `gmail.send` is sensitive; `gmail.labels` is non-sensitive.
- In **testing** status: max 100 listed test users, and authorizations expire seven days after consent. Exception: apps requesting only name/email/profile need no test-user list and do not expire. Also a 100-refresh-token cap per account per client.
- Refresh tokens are only returned with offline access, and typically only on first consent; use `prompt=consent` to force one.

## The decision

Real Google sign-in for the connection, with a clearly labeled mock inbox for the "first win" moment.

- Sign-in proves a real connection and gives us the account email ("connected to you@gmail.com"), works for any reviewer, no warning screen.
- The mock inbox is what the agent surfaces after connecting, tied to the stated help need. Labeled as mock in the UI and the write-up.
- If the real inbox is wanted for reviewers: a separate testing-status project with the reviewers' emails added, which has to be requested from the company early.

## The value moment (open)

Persona's real onboarding defaulted to a generic inbox summary instead of connecting its first action to the stated need. Principles for doing better:

- Tie the first win to their stated need. "Keeping up with school" means the first action is about school, not a general summary.
- Specific beats general, action beats summary. "You have an unanswered email from your professor about Friday's deadline. Want me to draft a reply?"
- Use what is already accessible. Canvas sends notification emails; with only Gmail connected the agent could already say "two assignments due this week."
- When they do not know what they need, look first: offer two or three concrete things from their actual data.
- Do it live: if Gmail connects mid-call, surface one real finding before hanging up.

What the mock inbox should contain is still undecided. See [13-open-questions.md](13-open-questions.md).

## Provider registry and the Integration table

Each integration is a provider definition in code: auth flow, scopes, capabilities the agent may call. Per-user connections are rows in one generic `Integration` table (see [08-storage.md](08-storage.md)). Google is the first provider. Agent-facing tools talk to the registry, never to Google directly.

## Self-building integrations (parking lot, future direction only)

Idea: when a user wants something like Canvas, the agent delegates research to the backend, which reads the API docs and produces a structured provider description (base URL, auth method, a few verified read-only endpoints) rather than free-form code. Reads automatic; writes need user approval.

The hard part is access, not the API: each school runs its own Canvas site and the standard connect flow needs admin-issued credentials. The workaround is a user-generated personal access token, which some schools disable. So the agent can learn the API but cannot grant itself permission. That is the pattern for most integrations and where the design would have to focus.

Not building this for the take-home. It goes in the write-up as a future direction that the provider registry is shaped to accommodate.
