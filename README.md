# Jev Assist

[![ci](https://github.com/rapina/jev-assist/actions/workflows/ci.yml/badge.svg)](https://github.com/rapina/jev-assist/actions/workflows/ci.yml)

Jev decides which OpenAI model and reasoning effort to use. Your local Codex
executes with **your own ChatGPT account**.

```text
Local Codex -> local client (127.0.0.1:4321)
                 |-> bounded task evidence -> central Jev Assist -> Jev
                 |<- model + reasoning effort
                 |-> full conversation + local account -> OpenAI
                 `-> status, timing, token counts -> dashboard
```

The central service never receives account authentication or the full execution
conversation. It receives limited recent task/context excerpts for classification.
The dashboard displays difficulty dimensions, model shares and client-reported
execution metadata. It does not display user messages or model output.

## Routing

Jev independently scores visual, architecture, coding and risk demands.
Architecture-heavy work can use Sol; combined visual/architecture work uses Astra;
bounded coding uses Terra; mechanical work uses Luna. Exact thresholds live in
[server/routing_policy.py](server/routing_policy.py).

Execution is restricted to Luna, Terra, Sol and Astra. Rate limits are returned
per request. There is no DeepSeek fallback, account switching or persistent
server-wide quota lockout. Model availability depends on the local Codex account.

## Windows client

Requires Node.js 22.19+ and Codex signed in with ChatGPT.

Open your team's Jev Assist `/dashboard` URL and run its installation command.
Alternatively, clone this repository and use your service's HTTP origin:

```powershell
git clone https://github.com/rapina/jev-assist.git
cd jev-assist
.\server\install-client.ps1 -ServiceUrl 'http://YOUR_SERVICE:4320'
```

The installer adds the user-level skill, model catalog, local connection settings
and a scheduled task for the local transport. It configures existing Codex/Orca
account homes and backs up their configuration. Start a new Codex process after
installation; existing sessions retain their previous provider connection.

Select `jev/auto` for automatic routing or a native model for a fixed selection.
A skill by itself cannot change a running Codex process's model.

## Service and API

The service provides `POST /v1/route` for decisions and `POST /v1/outcome` for
client-reported completion metadata. `/v1/responses` is disabled on the public
gateway: execution belongs on the workstation. The dashboard is `/dashboard`.

The service is intended for a trusted internal network. Restrict its firewall
to your clients. Publishing this source does not make the service an authenticated
Internet-facing API. TypeSafe credentials belong on the service host.

Operator references: [AGENTS.md](AGENTS.md), [server/INSTALL.md](server/INSTALL.md).
The root installers and embedded router retain a legacy standalone deployment;
workstation users should use the dashboard client installer above.

## Development

```powershell
python -X utf8 -m unittest discover -s server -p 'test_*.py'
node --test server/test-local-client.mjs server/test-configure-client.mjs server/test-lan-gateway.mjs
```

The local execution client is in [server/local-client.mjs](server/local-client.mjs).
The dashboard is in `server/dashboard.*`. Completion reports are client telemetry,
not independent result-quality verification. Verification observations are hidden.

## Attribution

Derived from [0xNatoshi/jev-codex-router](https://github.com/0xNatoshi/jev-codex-router).
The embedded Codex Router fork is documented in [ROUTER_FORK.md](ROUTER_FORK.md).
Third-party licenses and provenance are retained under `router/` and `vendor/`.
MIT license; see [LICENSE](LICENSE).

## When the service is unavailable

Automatic routing waits up to four seconds for a decision, then executes locally
with Sol and the current valid reasoning effort (medium by default). The next
request tries Jev again. A fixed model selection executes immediately; central
telemetry never blocks it. Requests made while central reporting is unavailable
may be absent from the dashboard. OpenAI errors are still returned normally.

If the local transport is also stopped, launch ordinary Codex directly:

```powershell
codex -c model_provider=openai -m gpt-5.6-sol
```

This bypasses Jev for that process and uses the local ChatGPT account. To return
to automatic routing, restore the local transport and start Codex normally.
