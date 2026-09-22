# AGENTS.md — autonomous install & operations playbook

## Central decisions, local account execution

The dashboard client installs a loopback transport at `127.0.0.1:4321`.
Codex uses provider `jev-local` with `requires_openai_auth = true`: each process
supplies its own ChatGPT authentication. The transport sends only bounded task
evidence to central `POST /v1/route`, receives model/effort, and sends the full
request directly to OpenAI. Credentials are never copied from account files or
sent centrally. `POST /v1/outcome` records numeric completion telemetry only.
These are client reports, not independent verification. Explicit native choices
preserve the selected model; only Luna, Terra, Sol and Astra may execute.

The public gateway rejects `/v1/responses` with 410. It must never resume shared
server-account execution. Historical central execution records remain visible.
Install from the dashboard command, then start a new Codex process. Already
running sessions retain their old provider connection. The local scheduled task
starts at login; no SSH connection or external API key is required.

The embedded server execution code below is retained for its loopback transport
tests and legacy standalone installations, not the distributed HTTP client.


This file lets a coding agent (Codex, Claude Code, Cursor, …) install, verify
and operate the **Jev Assist** on a user's machine without asking for
context it can discover itself. Follow it top to bottom. Do not skip the
verification step after each stage — they are what makes autonomy safe here.

## What you are installing

A self-contained monorepo with an embedded Codex Router fork (`router/`) and a
local Jev decision server (`server/`). Together they add **"Jev Assist"**
(`jev/auto`) to the Codex picker. Every call is classified by
[Jev](https://docs.typesafe.ai) (TypeSafe System One) and served by the
cheapest sufficient model at an adapted thinking depth. Local hops stay on
loopback; Jev calls go to TypeSafe. Authenticated model requests are fail-open
on Jev classification errors; there is a kill switch.

## Hard rules (never violate)

1. **Never print, log, commit, or transmit secrets** — the TypeSafe API key,
   the router `caller-secret`, or ChatGPT tokens. Reference them by file path.
2. **Edit the source, never the artifact.** `router/src/` is the embedded
   router's source and is meant to be edited: a behaviour bug is fixed there,
   committed in this repository, with the tests that cover it.
   What is off limits is the *generated and managed* output — `litellm.yaml`
   under the router's state directory is rendered from `src/litellm-config.mjs`
   whenever the catalog changes, and the `codex-router-managed` blocks of
   `~/.codex/config.toml` are written by the CLI, so a hand edit there is
   overwritten rather than applied. Change the generator, or drive the CLI and
   the documented state files (`user-models.json`, `generic-providers.json`),
   and leave the artifacts to be regenerated.
3. The server binds `127.0.0.1` only. Never expose it on another interface.
   Authorized remote clients use the separate `server/lan-gateway.mjs` boundary,
   with a restricted firewall rule. Client authentication is intentionally omitted
   on this internal service; upstream credentials remain inside the server. Never publish the
   embedded router's internal caller/session endpoints to the network.
4. If `launchctl` is restricted in your environment (supervised agents often),
   skip the service install — use the watchdog pattern and let the user run
   `server/install-service.sh` from their own Terminal instead. Never fight
   the restriction.
5. Treat prompt excerpts in local logs (`jev-router-live.jsonl`,
   `shadow-log.jsonl`) and dashboard `jev-audit.sqlite3` projections/reviews as
   private user data: read locally, never republish.

## Prerequisites (check, and report what you found)

- **macOS or Windows** with **Codex**. No separate Codex Router checkout is required.
- **Node.js ≥ 22.19** — `node -v`.
- **Python ≥ 3.11** — `python3 -V`.
- A **TypeSafe API key** for Jev. The server looks for `TYPESAFE_API_KEY` in
  `~/.hermes/.env` first, then `~/.jev.env`, then the process environment.
  If none exists, **stop and ask the user where their key file is — never ask
  for the key value itself in chat.**

## Install, step by step

### 1 — Verify the embedded source

```bash
cd <repo>
test -x router/bin/codex-router
node -p 'require("./router/package.json").name'
# expect: codex-model-router
```

### 2 — Install the complete stack

```bash
./install.sh
```

On Windows, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./install.ps1`
from the root instead. `-PrepareOnly` prepares dependencies in an isolated
temporary Codex home; `-SkipSmoke` omits the live request. The root installer
reuses `router/install.ps1` and installs a per-user Jev scheduled task. Keep
the checkout at a stable path; services run its source directly. Preserve
`CODEX_HOME` and router state overrides, including account-specific desktop
homes. The Windows runtime needs `server/requirements-windows.txt` in
`router/.venv`. Use `jev-assist` in a new terminal or
`./jev-assist.ps1` in the checkout. `service status|stop|uninstall`
controls only Jev; `router` delegates to the embedded runtime. Do not replace
a task belonging to another checkout or Codex home.

The optional skill is in `skills/jev-assist`. `./install-skill.ps1`
installs it for the current agent home without replacing an existing skill.
Runtime installation does not require skill installation. Team distribution
must use this fork's published URL, not upstream's unmodified Windows files.

The macOS installation uses `router/` directly,
preserves existing provider selection, idempotently configures the `jev`
provider and `jev/auto` model, provisions the protected local credential,
enables native ChatGPT sharing, installs both launchd services, publishes the
picker and runs the end-to-end smoke test. It never clones another repository.

If `launchctl` is restricted, run `./install.sh --prepare-only`, perform
non-service diagnostics, then ask the user to run `./install.sh` in their own
Terminal. Do not redirect the installation to a second checkout.

### 3 — Restart Codex

Fully quit and reopen the Codex app so it reloads the picker catalog, then the
user can select **Jev Assist**.

## End-to-end verification (must pass before declaring success)

```bash
python3 server/smoke.py
```

Expect HTTP 200 and status `completed`, with a selected native model. The
script refuses a stale running policy and never prints credentials. Then:

```bash
tail -1 ~/.codex/codex-router/jev-router-live.jsonl
# expect one JSON line: gate=apply, tier, conf, depth, model, effort, speed,
# jev_ms, total_ms, status=200, out=sse
```

## Operations

- **Dashboard**: open the configured HTTP service's `/dashboard` directly.
  Inspect bounded Jev inputs/answers, policy application, execution attempts and
  automatic verification observations. No manual review or export workflow.

- **Decision log**: `~/.codex/codex-router/jev-router-live.jsonl` — one line per
  routed turn.
- **Ask surface**: `POST /ask` (also `/v1/ask`) — typed pass-through to System
  One for local callers with their own question set (state ≤ 120k chars, ≤ 40
  questions, caller state never logged). `502 jev: HTTP Error 402` means the
  TypeSafe account is out of credits; `503` means no key was found.
- **Kill switch** (instant, no restart): `touch ~/.codex/codex-router/jev-router.off`
  → the server relays to astra without calling Jev. Remove the file to re-enable.
- **OpenAI execution only**: never substitute external providers. Return rate limits
  per request; do not persist server-wide quota lockouts.
- **Thread display**: streamed reasoning summaries get the routed tag appended
  in place ( · 🧠sol:low · , separators on both sides so the next summary part
  never glues to the tag; one glyph per route — ⚡luna, 🧠sol, 🚀astra,
  🌍terra) — the picked model shows inside each call's thinking
  block in the Codex thread.
- **Shadow mode**: `touch ~/.codex/codex-router/jev-router.shadow` → decisions
  are logged (`would` field) while every call is still served by astra.
- **All-Sol measurement cohort**: write
  `{"percent":10,"until":"<ISO-8601>"}` to
  `~/.codex/codex-router/jev-router.sol-baseline.json`. Assignment is stable by
  hashed prompt-cache scope; mandatory Astra and shadow semantics
  still win. Remove the file to stop the experiment.
- **Debug capture** (bounded): `touch ~/.codex/codex-router/jev-router.debug`
  → request shapes in `jev-router-debug.jsonl` and transport counters in
  `jev-router-debug-stream.log`. Remove the file to stop.
  Logs are 0600, rotate at 8 MiB and retain one backup. No new prompt excerpts
  or raw model streams are recorded; old captures are protected, not deleted.
- **Tune the policy**: the shared contract in `server/routing_policy.py`. Keep decisions
  joint and evidence-based; restart the server after edits. Cache affinity
  (`last_model`, measured state/read percentage/age and context size) is a cost
  signal inside Jev's typed model choice, never a code-side model override.
- **Backtest**: `python3 poc/backtest_savings.py --days 7` (see BACKTEST.md).
- **Router CLI**: `bin/jev-assist router <command>` delegates to the
  embedded runtime.
- **Update**: `bin/jev-assist update` updates this monorepo and reruns the
  unified installer; it never pulls a separate router checkout.
- **Disable**: `bin/jev-assist router providers generic disable jev`
  (keeps state); full rollback: also
  `bin/jev-assist router chatgpt-session disable` and stop the
  service (`launchctl bootout gui/$(id -u)/com.thibaultsaintjean.jev-router`).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `{"detail":"Unauthorized"}` from the caller edge | native sharing off | `bin/jev-assist router chatgpt-session enable` |
| `{"detail":"Stream must be set to true"}` | the caller edge streams only | send `"stream": true`; the bundled server forces it |
| HTTP 502 `provider_api_proxy_error` on jev-auto | server-side error | check the `status`/`out` fields in `jev-router-live.jsonl`, and the server's stderr log |
| "Jev Assist" absent from the picker | not published/visible, or Codex not restarted | `refresh-catalog`, `control picker set jev/auto show`, full Codex restart |
| `Unknown API gateway model: jev-auto` | catalog not republished | `bin/jev-assist router refresh-catalog` |
| Jev returns HTTP 422 | request body missing `"model"` | always send `"model": "jev-latest"` to the System One API |
| Native calls fail after a few days | shared session expired | re-run `chatgpt-session enable` |
| `launchctl` rejected inside a supervised agent | environment restriction | run `./install.sh --prepare-only`; let the user run `./install.sh` in Terminal |

## Latency & cost notes

- Policy `axes-v12` asks six Choice questions in one request: visual, architecture,
  coding and risk demands (0-4 or unknown), effort and route lease. Each axis
  has independent criteria. Complex architecture does not imply visual work.
- Risk >=3 or visual >=3 uses Astra; visual and architecture both >=2 also use
  Astra. Architecture/coding >=3 or visual/risk >=2 uses Sol. Other nonzero
  demands use Terra; all-zero mechanical work uses Luna. Unknown axes use Sol
  unless known demands require Astra. No keyword overrides, target model share
  or confidence-based substitution. Judge remaining work, not completed phases.
- Preserve independent effort, standard speed and full executor replay. Jev sees
  bounded task context and tool evidence only. Clean continuations may reuse
  routes; errors, compaction, new turns and changed tool chains reassess them.
  Native client model requests bypass selection and preserve model/effort.
- Dashboard gauges use axis scores, never model-derived difficulty. Completed
  request shares cover all retained records with auto/fixed filters; they do not
  measure cost efficiency or quality. Do not expose user messages.
- Provider/schema failures remain distinct: Astra at medium, logged as a
  technical fallback. Kill switch still applies; native failures are returned without substitution.
- Jev usage and upstream per-attempt tokens are logged when available. Run
  `python3 server/report_routing.py --days 7 --policy current` for native-only
  credit estimates, lease savings, the routed/all-Sol cohort comparison and
  observed prompt-cache reads/writes by model/session; unknown usage remains
  unknown and reasoning tokens are not counted twice.
- `BACKTEST.md` documents the old policy's fixed-token simulation. It is not a
  measurement of current quota savings or result quality.
