# Research report: Google sign-in + Gmail for a FastAPI demo (2026-09-25)

Produced by a research subagent from Google's current docs. Reproduced as received (three messages joined). Verify anything load-bearing before relying on it.

## 1. Google Cloud setup

1. **Project**: create one at https://console.cloud.google.com.
2. **Google Auth Platform** (the 2025 replacement for the old "OAuth consent screen" page): https://console.developers.google.com/auth/overview → Get started wizard: App information (app name, user support email) → Audience (Internal/External) → Contact information → Finish. Afterwards four pages: **Branding**, **Audience** (user type, publishing status, test users), **Clients** (OAuth client IDs), **Data Access** (scopes), plus a **Verification Center**.
3. **User type**: **External**.
4. **Publishing status** (Audience page):
   - **Testing**: only accounts on the test-user allowlist (cap 100) can authorize, *except* if the request only asks for basic identity scopes (`openid`, `email`, `profile`), in which case anyone can sign in. Test users see a "testing" warning UI. Refresh tokens expire 7 days after consent. Restricted scopes (gmail.readonly) work for test users without any verification. Non-test users requesting more than basic scopes get `error=access_denied`.
   - **In production**: any Google account. Unverified + non-sensitive scopes: works, app name/logo hidden until brand verification. Unverified + sensitive/restricted scopes: "unverified app" danger screen, hard cap of 100 users; Restricted (Gmail) scopes additionally require restricted-scope verification plus an annual CASA assessment. Not feasible for a demo. No 7-day expiry in production.
5. **Client**: Clients → Create client → **Web application**. Add Authorized redirect URIs (§6). Since 2025 the **client secret is shown only once at creation**; copy it immediately. Changes can take "5 minutes to a few hours" to propagate (usually seconds).
6. **Enable APIs**: **Gmail API**. Nothing extra for the email address: the OpenID `userinfo` endpoint and ID token need no API enabled. People API not needed.
7. **Data Access**: add the scopes you'll request (openid, email, profile, gmail.readonly).

## 2. Scopes

**(a) Sign-in + email**: `openid email profile` (short forms accepted). Non-sensitive, no verification, and in Testing any user can grant them. Drop `profile` if only the address is needed.

**(b) Reading the inbox**:

| Scope | Class | `messages.list q` | `messages.get` formats | Returns |
|---|---|---|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | **Restricted** | yes | minimal/metadata/full/raw | headers, snippet, body |
| `https://www.googleapis.com/auth/gmail.metadata` | **Restricted** | **no** | metadata/minimal only | id, labels, headers; snippet observed but not documented |

Both are Restricted, so `gmail.metadata` buys nothing and loses `q`. The only non-sensitive Gmail scopes are `gmail.labels` and add-on scopes; **there is no non-restricted way to get subjects/snippets.** Use `gmail.readonly`.

**Strategy**: one External project in **Testing**. Sign-in requests only `openid email`, exempt from the test-user allowlist, so any reviewer can connect. The Gmail step is a second, incremental consent for `gmail.readonly`, which works only for accounts added as test users. No second project needed.

## 3. The auth flow (plain httpx)

**Authorization URL** `https://accounts.google.com/o/oauth2/v2/auth` with:
- `client_id`, `redirect_uri` (exact match to a registered URI), `response_type=code`
- `scope` (space-delimited; start with `openid` to get an ID token)
- `state` (random, stored server-side or in a signed cookie; verify on callback)
- `access_type=offline` (required for a refresh token)
- `prompt`: `consent` (always show consent; guarantees a fresh refresh token), `select_account`; combine as `prompt=consent select_account`
- `include_granted_scopes=true` (incremental auth)
- `login_hint=<email>` if known
- **PKCE**: `code_challenge` + `code_challenge_method=S256`, then `code_verifier` at exchange. Optional for confidential web clients, recommended.

**Token exchange** POST `https://oauth2.googleapis.com/token` (form-encoded): `code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code`, plus `code_verifier` if PKCE. Response: `access_token`, `expires_in` (3600), `token_type`, `scope` (what was actually granted; users can untick), `id_token`, `refresh_token` (**only if** offline and first consent, or `prompt=consent`).

**Refresh** POST same endpoint: `client_id`, `client_secret`, `refresh_token`, `grant_type=refresh_token`. No new refresh token. Refresh tokens die on: 7 days in Testing, 6 months unused, password change when the grant includes Gmail scopes, user revocation, or exceeding **100 live refresh tokens per client per account**. Failure: HTTP 400 `{"error":"invalid_grant","error_description":"Token has been expired or revoked."}`. Treat as final: delete the row and prompt to reconnect.

**Email**: decode the `id_token` JWT (claims `sub`, `email`, `email_verified`). Decoding without signature verification is acceptable for a demo since it came from Google over TLS; otherwise verify via JWKS. Fallback: GET `https://openidconnect.googleapis.com/v1/userinfo` with the Bearer token. Key users on `sub`, not email.

**Lifetimes**: access token 1 hour; auth code single-use, minutes.

google-auth-oauthlib works too, but httpx is ~40 lines and avoids scope-mismatch exceptions when a user grants fewer scopes.

## 4. Reading Gmail (gmail.readonly)

1. `GET https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10&labelIds=INBOX&q=newer_than:7d -category:promotions -category:social` (5 units). Returns `{id, threadId}` per message.
2. Per id: `GET /gmail/v1/users/me/messages/{id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date` (20 units). Returns `snippet`, `internalDate`, `labelIds`, `payload.headers[]`.
3. Batch endpoint exists; for 10 messages, 10 concurrent httpx requests are simpler.

Quota: 6,000 units/min/user; irrelevant for a demo. Gotchas: use messages, not threads; `From` includes a display name; `snippet` is HTML-escaped; Workspace admins may block unverified apps.

## 5. Reset for testing

- **(i) Revoke**: `POST https://oauth2.googleapis.com/revoke?token=<refresh_or_access_token>` (form-encoded). Revoking a refresh token kills the whole grant. Returns 200, or 400 `invalid_token` if already dead (treat as success). Next authorization shows full consent and issues a new refresh token.
- **(ii) User side**: https://myaccount.google.com/permissions → the app → "Delete all connections".
- **(iii) Deleting our row alone** resets nothing on Google's side; the next authorize without `prompt=consent` may return **no** refresh token.
- **(iv) `prompt=consent`** always re-shows consent and mints a new refresh token; old tokens stay live and count toward the 100 cap.

**Recommended "Disconnect Gmail"**: load row, best-effort POST revoke with the refresh token (fall back to access token; ignore 400), delete row, clear session. Always send `access_type=offline&prompt=consent` on connect. In Testing, a long-idle row hits `invalid_grant`; delete and show "reconnect".

## 6. Local dev gotchas

- Register `http://localhost:8000/api/auth/google/callback` exactly. HTTPS required except `localhost`/`127.0.0.1`; exact match including port, path, trailing slash; no wildcards. Multiple URIs per client are fine.
- `redirect_uri_mismatch`: byte-for-byte difference. Causes: scheme behind a proxy (build the URI from config, not `request.url`), `127.0.0.1` vs `localhost`, port, trailing slash, or propagation delay.
- Cookies: `localhost` and `127.0.0.1` are different cookie hosts. Pick one for both the texted link and the redirect URI. `SameSite=Lax` works. Store `state`, `code_verifier`, and the phone server-side or in a signed cookie.
- Tunnels: ngrok/cloudflared hostnames are registrable; free ngrok URLs rotate.
- App name shows in Testing without verification (with an interstitial for non-basic scopes). Do not upload a logo (triggers brand verification).
- Corporate Workspace reviewers may be blocked by admin policy; they need a personal Gmail.

## Recommended setup for this project

- **(a) Projects**: one External project in **Testing**.
- **(b) Scopes**: step 1 (anyone): `openid email`. Step 2 (test users, incremental, `include_granted_scopes=true`): `gmail.readonly`. Detect a non-test-user via `error=access_denied` and degrade to "connected, email verified, inbox unavailable".
- **(c) Redirect URIs**: localhost:8000, 127.0.0.1:8000, the tunnel host, the prod host, all at `/api/auth/google/callback`. Enable the Gmail API. Copy the client secret at creation.
- **(d) Reset**: Disconnect = revoke + delete row + clear session. Connect always `access_type=offline&prompt=consent select_account`. On `invalid_grant`, delete the row and prompt reconnect.
- **(e) Ask reviewers for**: the Google address they will test with (add as a test user; immediate) and whether a Workspace admin allows unverified apps.

## Sources

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/identity/protocols/oauth2
- https://developers.google.com/identity/protocols/oauth2/production-readiness/overview
- https://support.google.com/cloud/answer/15549945 · https://support.google.com/cloud/answer/15544987 · https://support.google.com/cloud/answer/15549257 · https://support.google.com/cloud/answer/7454865
- https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification
- https://support.google.com/cloud/answer/13464321 · https://support.google.com/cloud/answer/9110914
- https://developers.google.com/identity/openid-connect/openid-connect
- https://developers.google.com/identity/protocols/oauth2/native-app
- https://developers.google.com/workspace/gmail/api/auth/scopes
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list · /get · https://developers.google.com/workspace/gmail/api/reference/rest/v1/Format
- https://developers.google.com/workspace/gmail/api/reference/quota · https://developers.google.com/workspace/gmail/api/guides/batch · https://developers.google.com/workspace/gmail/api/guides/filtering
- https://nango.dev/blog/google-oauth-invalid-grant-token-has-been-expired-or-revoked/
