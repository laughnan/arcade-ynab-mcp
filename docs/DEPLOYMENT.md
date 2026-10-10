# Deployment and credential verification

How to check that what's running on Arcade Cloud matches this repository, which
credentials it uses, and that its gateways are private. Run through this after every
deploy and whenever the credential setup changes. Nothing here needs a secret value to
be copied anywhere: never paste secrets into tickets, logs, chat or this repository.

## Expected credential model

This repository uses per-user OAuth. There are exactly two kinds of credential:

| Credential | Where it lives | Who can use it |
|---|---|---|
| YNAB OAuth app client ID and client secret | The Arcade custom OAuth provider with ID `ynab` (Arcade dashboard → Connected apps) | Arcade, only to run the sign-in flow and refresh tokens |
| Each user's YNAB access and refresh tokens | Stored by Arcade after that user signs in to YNAB | Only tool calls made by that user; injected through `Context` per request |

What the code does:

- Every tool declares `requires_auth=OAuth2(id="ynab")` and reads the caller's token
  with `context.get_auth_token_or_empty()` (`src/arcade_ynab/client.py`).
- No tool declares `requires_secrets`, and the server reads no environment variables.
  Tests enforce both (`tests/test_server.py`, `tests/test_credentials.py`).

So a **YNAB personal access token** saved as an Arcade secret is not used by this code.
If one exists, either the deployed code isn't this repository, or the secret is
leftover and should be removed (see below).

## 1. Identify the deployed revision

```bash
arcade login
arcade server list            # deployed servers, versions and status
arcade server logs ynab       # startup logs show the registered tools
```

- The server name is `ynab` and its version comes from `server.py` (`MCPApp(version=...)`)
  and `pyproject.toml`. Check both match the commit you think is deployed.
- Arcade doesn't record a git commit. To know exactly what's running, deploy from a
  clean checkout of a known commit and note the commit next to the version:

  ```bash
  git status --porcelain       # must be empty
  git rev-parse HEAD
  arcade deploy -e src/arcade_ynab/server.py
  ```

- Check the logs list the same tools as `tests/test_server.py` and nothing else (for
  example, none of the scaffold's sample tools).

## 2. Identify the credential mechanism

```bash
arcade secret list            # names only; never print or copy values
```

- **Expected:** no YNAB-related secret (such as `YNAB_ACCESS_TOKEN` or `YNAB_TOKEN`). The
  only YNAB credential on the Arcade side is the client secret inside the `ynab` OAuth
  provider, which is configured in the dashboard, not as a tool secret.
- In the dashboard, open **Connected apps** and check the custom provider `ynab` exists,
  with the settings in [SPEC.md](SPEC.md#setup-one-time-done-by-the-maintainer-in-each-services-ui).
- When you call a tool for the first time, you should be sent through YNAB's sign-in and
  consent screen. If tools work without that, a different credential path is in use.

If a YNAB personal access token is stored as a secret:

1. If the deployed code is this repository, nothing reads it. Remove it with
   `arcade secret unset <NAME>` and revoke the token in YNAB (Account Settings →
   Developer Settings), since it has been stored somewhere it isn't needed.
2. If the deployed code is different and does use it, every authorized caller of the
   gateway acts with that one token's full access to the YNAB account. Restrict the
   gateway to yourself (step 3), and either move to this repository's per-user OAuth or
   document that shared-token behavior in the README and SPEC.

## 3. Check the gateways are private

For each gateway in the Arcade dashboard:

- **Auth mode** is **Arcade Auth**, so only signed-in members of the Arcade project can
  use it.
- **Project members** are only the people who should reach your YNAB data (for a
  personal setup, only you). Remove anyone else.
- **Selected tools** match the lists in [GATEWAYS.md](GATEWAYS.md) (if present) or the
  set you intend; check no write tools are in a read-only gateway.

## 4. Check unauthenticated requests are rejected

Use a request that changes nothing. Listing tools is read-only:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST "https://api.arcade.dev/mcp/<gateway-slug>" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expect `401` (or `403`). A `200` with a tool list means anyone with the URL can see,
and probably call, the tools: switch the gateway to Arcade Auth before doing anything
else. A health or status endpoint answering without auth isn't evidence either way;
only tool listing and tool calls matter.

## Recording the result

Note the date, commit, server version, gateway slugs, auth mode, member count, tool
count and the unauthenticated status code wherever you track deployments (not in this
repository if it's public, since gateway slugs identify your endpoints).
