# AutoHDR agent connection (checked 28 October 2026)

The account is `sgu07966@gmail.com`, created with Sign in with Google and on the Free plan (10 credits a month,
$0 per month). Activating Free asked for no card.

**Result: the free account cannot use the MCP server or the API.** The OAuth consent page for the MCP server
(`new.autohdr.com/consent`) shows the following and disables **Allow**:

> The API is on Standard and above. Upgrade your plan to connect this app.

The flow was ended with Deny (`error=access_denied`). No token was issued, no tool list was read and no test
edit ran.

## What AutoHDR offers developers

There is no hackathon, developer, beta or promo-code option anywhere in the signed-in account.

| Place | What it holds |
|---|---|
| Account settings tabs | Automations, Style settings, Billing, Team, Earn |
| Billing | Plan, card, invoices and credit log; no code field |
| Earn | An affiliate application and a 50% referral commission |
| Pricing | Free: 10 credits a month, extra credits at $0.80 each |
| Pricing | Standard: $265 a month for 500 credits; Enterprise: $960 a month for 2,000 |
| API page (`new.autohdr.com/api`) | "Beta Access Now Live for Standard Plans and Above" |

The API page has two modes:

- **MCP:** an "Add to Claude" button for `https://mcp.realestatephotoediting.com/mcp`.
- **API:** "Create your API app", which issues a client ID and secret and can download them as a `.env` file,
  plus a link to the API docs. No app was created; per the consent page, its tokens would also need Standard.

## The REST API, from its public OpenAPI spec

The spec is at `https://external.realestatephotoediting.com/openapi.json` (`external-api` 0.1.0).

- **Auth:** OAuth 2.0.
  - `client_credentials` acts on your own account.
  - `authorization_code` acts for a user, with `offline_access` for refresh tokens.
  - The token URL is `https://new.autohdr.com/api/auth/oauth2/token`.
  - The `resource` parameter is required on the token request.
- **Endpoints, all under `/v1`:**
  - `me`, `credits`, `settings`, `capabilities` (transforms with live prices) and `models`.
  - Photoshoots: create with presigned uploads, or `from-urls`.
  - `commit` and `status`, then `photos`.
  - `photos/download`, which charges credits.
  - Per photo: history, `source-photos`, `version`, and `describe` (free).
  - `transforms`: run, list and poll.
  - `asset-uploads` for masks and reference images.
  - `presets`: list, get and `run`.
- **Documented errors:** `402 insufficient_credits` (nothing charged) and `403 transform_not_exposed`.
- **Timing:** the spec says HDR processing "typically a few minutes, plan on ~30 min".

## The MCP server

- **Transport:** streamable HTTP.
- **Authorization server:** `https://new.autohdr.com/api/auth`, with `/oauth2/authorize`, `/oauth2/token` and
  `/oauth2/register` (dynamic client registration).
- **Grants:** PKCE `S256`, `authorization_code`, `refresh_token` and `client_credentials`.
- **Scopes:** `photoshoots:read`, `photoshoots:write`, `images:edit`, `presets:read`, `presets:run`,
  `offline_access`.

Dynamic registration works on the Free plan; the plan check is on the consent page. The registration (client ID
and secret, no tokens) is saved at `~/.local/share/shootagent/autohdr/oauth.json` (0600), outside every repo.
It can be reused after an upgrade.

The flow ran with the MCP Python SDK 2.3.0 OAuth client, in a throwaway environment under `/tmp`, catching the
callback on `127.0.0.1:33418`.

## State on the demo Mac

- **Claude Code:** `autohdr` is configured with local scope, keyed to `~/Programming Projects/shootagent`.
  Its status is "Needs authentication".
- **Remove it with:** `claude mcp remove autohdr -s local`, run from that folder.
