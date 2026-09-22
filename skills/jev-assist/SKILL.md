---
name: jev-assist
description: Obtain model and reasoning decisions from Jev Assist over HTTP while executing with the local Codex ChatGPT account.
---

# Jev Assist

The installed local client asks **http://127.0.0.1:4320/v1/route** for a model
and reasoning effort, then calls OpenAI directly with the current Codex account.
Work normally in the local project. Authentication and full execution context
stay on this computer; the central service receives bounded task evidence only.
The selected execution models are Luna, Terra, Sol and Astra. Native selections
bypass Jev scoring. Rate limits are returned without changing accounts/providers.
Do not submit duplicate decision requests for sessions already using the client.

A decision-only HTTP request:

```http
POST /v1/route
Content-Type: application/json

{"state":"Remaining task and relevant recent context"}
```

The response contains `id`, `model` and `effort`. This endpoint does not execute
the task. A skill alone cannot change the current Codex process's model; install
the dashboard client and start a new session for automatic per-request routing.
Do not configure Codex to send execution requests to the central service.

Open **http://127.0.0.1:4320/dashboard** to inspect difficulty dimensions,
model shares and local-client completion reports. Reports contain status, timing
and token counts, not user messages or model output. They are client-reported
execution telemetry, not independent quality verification.

`GET /health` checks central reachability. It does not verify the local account
or OpenAI availability. After installation, restart Codex/Orca or start a new
Codex process so it reloads the local connection settings.

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
