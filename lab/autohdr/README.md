# AutoHDR agent connection (checked 28 October 2026, no account)

There are two AutoHDR "agent" products:

- **The Slack agent.** `agent-install.autohdr.com/install` only offers "Add to Slack", which provisions a hosted
  assistant in a Slack workspace. `agent.autohdr.com` is its skills catalogue, and it is empty until you sign in.
  ShootAgent cannot call either one.
- **The MCP server.** AutoHDR's API page (`new.autohdr.com/api`, marked BETA) links a remote MCP server at
  `https://mcp.realestatephotoediting.com/mcp`. This is the one an agent can call.

The page says: "Beta Access Now Live for Standard Plans and Above". Standard costs $265/month for 500 credits.
The free plan gives 10 credits a month, with extra credits at $0.80 each.

## How the MCP server authenticates

The details below come from the server's own discovery documents.

- **Transport:** streamable HTTP. An unauthenticated `initialize` returns `401` with `WWW-Authenticate: Bearer
  resource_metadata=".../.well-known/oauth-protected-resource/mcp"`.
- **Authorization server:** `https://new.autohdr.com/api/auth`. Its endpoints are
  `/oauth2/authorize`, `/oauth2/token` and `/oauth2/register` (dynamic client registration).
- **Grants:** `authorization_code` with PKCE `S256`, `refresh_token`, and `client_credentials`. The last one is
  for "API apps", which the API tab creates as a client ID and secret.
- **Scopes:** `photoshoots:read`, `photoshoots:write`, `images:edit`, `presets:read`, `presets:run`,
  `offline_access`.
- **No static API key.** Claude Code stores the OAuth token itself after `/mcp`, then "Authenticate".

The tool list needs an authorised session, so it was not read. The scopes and the page's three headings outline
what the tools cover. Those headings are "Photoshoot creation: pick a look, start a shoot", "Post-processing:
make any re-edit" and "Agent Tools".

## State on the demo Mac

- **Configured:** `claude mcp add --scope local --transport http autohdr https://mcp.realestatephotoediting.com/mcp`
  was run. Claude Code keys local scope to the main checkout, `~/Programming Projects/shootagent`. Its status is
  "Needs authentication".
- **Remove it with:** `claude mcp remove autohdr -s local`, run from that folder.
- **Not done:** no AutoHDR account exists yet, and no test edit ran.
