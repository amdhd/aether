# Security guardrails

Treat these as invariants when editing security-sensitive flows:

- `api/app/api/deps.py` and CRUD/tool handlers: derive identity from validated access tokens; scope every read/write to that user. Return 404 for another user's resource.
- `api/app/core/security.py`, `api/app/services/*tokens.py`, and auth routes: preserve expiry, token-purpose checks, refresh rotation/replay detection, password-change revocation, and one-use reset tokens. Never log tokens or credentials.
- Google integration routes: bind OAuth callbacks to the initiating browser before exchanging codes. Keep cookies HttpOnly, use secure cookies in production, and preserve callback cache/referrer protections. Test missing and mismatched browser state.
- Agent loop/tools: tool output, retrieved notes, and uploads are untrusted data. Prompt fences do not authorize destructive actions. New destructive/external tools must require server-enforced approval bound to the user and exact operation; model-produced approval is insufficient.
- Attachments: retain byte/type limits before parsing and ownership checks before retrieval. Do not execute uploaded content or use supplied filenames as filesystem paths.
- Production config and email: fail closed for missing secrets; never fall back to logging reset/verification links in production. Do not expose SMTP exception content.
- `.github/workflows/`, deployment files, and `infra/`: review permissions, trust boundaries, and secret handling on changes. Do not grant untrusted pull requests deployment credentials.
- Never read or commit real `.env` secrets, database files, private keys, state files, or plans during routine development. Use dummy credentials and isolated fixtures.
- Security changes need negative regression tests as well as successful-flow tests. Report tests that were not run; do not imply static review proves runtime safety.
