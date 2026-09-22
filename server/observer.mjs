import { readFileSync, existsSync, mkdirSync, appendFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { normalize } from '../vendor/canny/dist/events.js';
import { handle } from '../vendor/canny/dist/hook.js';
import { loadConfig } from '../vendor/canny/dist/config.js';
import { read, summarize } from '../vendor/canny/dist/ledger.js';
import { writePrivateFile, privateFileIsProtected } from '../router/src/file-security.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
export function projection(ctx, entries, decision) {
  const summary = summarize(entries);
  return { id: sha(os.hostname() + '\0' + ctx.session), session: ctx.session.slice(0, 128),
    project: path.basename(ctx.cwd).slice(0, 160), client: os.hostname().slice(0, 128),
    mode: 'observe', updated: Date.now(), eventCount: entries.length,
    files: summary.codeFiles.slice(0, 100), verified: Boolean(summary.verified), verdict: decision.kind,
    events: entries.slice(-30).map(e => {
      const f = e.fact;
      return { at: e.ts, type: e.type, phase: e.phase, tool: e.tool,
        ...(f ? {kind: f.kind, files: (f.code || []).slice(0, 30), verify: f.verify,
          exitCode: f.exitCode, commandHash: f.command ? sha(f.command).slice(0,16) : undefined} : {}),
        ...(e.type === 'verdict' ? {decision: e.decision} : {}),
        ...(e.type === 'jev' ? {answers: e.answers, ms: e.ms, error: e.error} : {}) };
    }) };
}
export async function observe(raw, { directory, post, judgeEnabled = true } = {}) {
  const ctx = normalize(raw, 'codex');
  if (!['post','stop'].includes(ctx.phase) || ctx.session === 'unknown') return {};
  const state = directory || path.join(os.homedir(), '.codex', 'jev-observer');
  mkdirSync(state, {recursive: true});
  const file = path.join(state, sha(ctx.session) + '.jsonl');
  if (!existsSync(file)) writePrivateFile(file, '');
  if (!privateFileIsProtected(file)) throw new Error('Observer ledger is not private');
  const judge = async (value, questions) => {
    if (!judgeEnabled) return null;
    const start = Date.now(); let answers = null, error;
    try {
      const result = await post('/v1/ask', {state: value, questions});
      answers = Object.fromEntries(Object.entries(result.answers || {}).filter(([,v]) => typeof v?.noul === 'number').map(([k,v]) => [k,v.noul]));
    } catch { error = 'Judgment unavailable'; }
    appendFileSync(file, JSON.stringify({ts: Date.now(), type: 'jev', ms: Date.now()-start, answers, error})+'\n');
    return answers;
  };
  const decision = await handle(ctx, {file, config: loadConfig(ctx.cwd), judge});
  const body = projection(ctx, read(file), decision);
  // ponytail: latest snapshot per session; a later event retries an offline upload.
  // The local append-only ledger remains the evidence source, not a tamper-proof attestation.
  const pending = file + '.pending.json';
  writePrivateFile(pending, JSON.stringify(body));
  await post('/v1/observations', body);
  // No observation decision is serialized to Codex: observation never changes agent behavior.
  return {};
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const bundledSettings = fileURLToPath(new URL('./client.json', import.meta.url));
    const settings = JSON.parse(readFileSync(existsSync(bundledSettings) ? bundledSettings : path.join(os.homedir(), '.codex', 'jev-client.json'), 'utf8'));
    const post = async (route, data) => {
      const response = await fetch(settings.origin + route, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data), signal:AbortSignal.timeout(4000)});
      if (!response.ok) throw new Error('Central service unavailable');
      return response.json();
    };
    await observe(JSON.parse(readFileSync(0,'utf8')), {post});
  } catch {
    const file = path.join(os.homedir(), '.codex', 'jev-observer-error.txt');
    try { writePrivateFile(file, new Date().toISOString() + ' Observation or upload failed; local evidence may remain.\n'); } catch {}
  }
  process.stdout.write('{}');
}
