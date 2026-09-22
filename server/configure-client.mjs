// Local transport uses authentication supplied by each Codex process.
import { existsSync, readFileSync, readdirSync, mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { scanTomlDocument } from '../router/src/toml-structure.mjs';
import { writePrivateFile } from '../router/src/file-security.mjs';

const provider = 'jev-local';
export function configure(contents, origin, catalog) {
  const url = new URL(origin);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Expected HTTP origin');
  let doc = scanTomlDocument(contents);
  const remove = new Set();
  doc.headers.forEach((header, index) => {
    if (header.path[0] === 'model_providers' && [provider, 'jev-central', 'jev-buildbox'].includes(header.path[1])) {
      for (let line = header.index; line < (doc.headers[index + 1]?.index ?? doc.lines.length); line++) remove.add(line);
    }
  });
  const values = { model: 'jev/auto', model_provider: provider, model_catalog_json: catalog };
  for (const entry of doc.assignments) {
    if (!entry.tablePath.length && entry.key.length === 1 && entry.key[0] in values) {
      if (entry.kind !== 'string') throw new Error('Expected string setting: ' + entry.key[0]);
      remove.add(entry.index);
    }
    if ([...entry.tablePath, ...entry.key][0] === 'profiles' && (entry.kind !== 'string' || [...entry.tablePath, ...entry.key].includes('model_provider'))) throw new Error('Explicit profile provider needs review');
  }
  const root = Object.entries(values).map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join('\n');
  return root + '\n' + doc.lines.filter((_, index) => !remove.has(index)).join('\n').trimEnd() + '\n\n' +
    `[model_providers.${provider}]\nname = "Jev Assist (local account)"\nbase_url = "http://127.0.0.1:4321/v1"\nwire_api = "responses"\nrequires_openai_auth = true\nsupports_websockets = false\n`;
}

export function configureHooks(contents) {
  const data = JSON.parse(contents || '{}');
  if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('Invalid hooks file');
  data.hooks ??= {};
  if (!data.hooks || Array.isArray(data.hooks) || typeof data.hooks !== 'object') throw new Error('Invalid hooks map');
  const script = fileURLToPath(new URL('./observer.mjs', import.meta.url));
  const command = `node "${script}"`;
  for (const event of ['PostToolUse','Stop']) {
    const groups = data.hooks[event] || [];
    if (!Array.isArray(groups)) throw new Error('Invalid hook groups');
    const kept = groups.flatMap(group => {
      if (!Array.isArray(group?.hooks)) return [group];
      const hooks = group.hooks.filter(hook => !(['Jev Assist observation', 'Jev Coding observation'].includes(hook?.statusMessage) && /^(?:node|"[^"\r\n]*node(?:\.exe)?") "[^"\r\n]*[\\/]server[\\/](?:canny-observer|observer)\.mjs"$/.test(hook.command)));
      return hooks.length ? [{...group, hooks}] : [];
    });
    data.hooks[event] = [...kept, {...(event === 'PostToolUse' ? {matcher:'Bash|apply_patch'} : {}),
      hooks:[{type:'command', command, timeout:15, statusMessage:'Jev Assist observation'}]}];
  }
  return JSON.stringify(data, null, 2) + '\n';
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [origin, catalog, scope, onlyHome] = process.argv.slice(2);
  if (!origin || !catalog || !existsSync(catalog)) throw new Error('Usage: configure-client.mjs ORIGIN CATALOG_FILE');
  if (scope && (scope !== '--home' || !onlyHome)) throw new Error('Expected --home DIRECTORY');
  const homes = new Set(onlyHome ? [path.resolve(onlyHome)] : [path.join(os.homedir(), '.codex'), process.env.CODEX_HOME].filter(Boolean));
  const accounts = path.join(os.homedir(), 'AppData', 'Roaming', 'orca', 'codex-accounts');
  if (!onlyHome && existsSync(accounts)) for (const item of readdirSync(accounts, { withFileTypes: true })) {
    const home = path.join(accounts, item.name, 'home');
    if (item.isDirectory() && existsSync(path.join(home, 'config.toml'))) homes.add(home);
  }
  // Validate every home before changing any of them.
  const plans = [...homes].map(home => {
    const file = path.join(home, 'config.toml');
    const before = existsSync(file) ? readFileSync(file, 'utf8') : '';
    const hooksFile = path.join(home, 'hooks.json');
    const hooksBefore = existsSync(hooksFile) ? readFileSync(hooksFile, 'utf8') : '';
    return {home, file, before, after: configure(before, origin, catalog), hooksFile, hooksBefore, hooksAfter: configureHooks(hooksBefore)};
  });
  for (const {home, file, before, after, hooksFile, hooksBefore, hooksAfter} of plans) {
    mkdirSync(home, {recursive: true});
    if (!existsSync(file + '.before-jev-local')) writePrivateFile(file + '.before-jev-local', before);
    writePrivateFile(file, after);
    if (!existsSync(hooksFile + ".before-jev-observer")) writePrivateFile(hooksFile + ".before-jev-observer", hooksBefore || "{}");
    writePrivateFile(hooksFile, hooksAfter);
  }
  writePrivateFile(fileURLToPath(new URL('./client.json', import.meta.url)), JSON.stringify({origin}));
  console.log(JSON.stringify({configuredHomes: plans.length, provider, origin, localService: true}));
}
