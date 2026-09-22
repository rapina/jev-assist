import {createRequire} from 'node:module';
import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';

const MANAGED_KEYS = new Set(['openai_base_url', 'model_catalog_json']);

function rawTokens(value) {
  return String(value || '').match(/"(?:\\.|[^"\\])*"|'[^']*'|\S+/g) || [];
}

function unquote(value) {
  if (value.length >= 2 && ((value[0] === '"' && value.at(-1) === '"') || (value[0] === "'" && value.at(-1) === "'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function managedAssignment(value) {
  const key = unquote(value).split('=', 1)[0];
  return MANAGED_KEYS.has(key);
}

export function configureCodexArgs(current, baseUrl, catalogFile) {
  const url = new URL(baseUrl);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Expected a loopback HTTP origin');
  }
  const tokens = rawTokens(current);
  const kept = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if ((token === '-c' || token === '--config') && index + 1 < tokens.length && managedAssignment(tokens[index + 1])) {
      index += 1;
      continue;
    }
    const inline = token.match(/^(?:-c|--config)=(.*)$/s);
    if (inline && managedAssignment(inline[1])) continue;
    kept.push(token);
  }
  const catalog = path.resolve(catalogFile).replaceAll('\\', '/');
  if (catalog.includes('"')) throw new Error('Catalog path cannot contain a quote');
  const catalogAssignment = `model_catalog_json=${catalog}`;
  const quotedCatalog = /\s/.test(catalogAssignment) ? `"${catalogAssignment}"` : catalogAssignment;
  return [...kept, '-c', `openai_base_url=${url.origin}/v1`, '-c', quotedCatalog].join(' ').trim();
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function activeSettingsPath(userDataPath) {
  const indexPath = path.join(userDataPath, 'orca-profile-index.json');
  for (const candidate of [indexPath, `${indexPath}.bak`]) {
    try {
      const parsed = JSON.parse(readFileSync(candidate, 'utf8'));
      const profileId = parsed.activeProfileId;
      if (Array.isArray(parsed.profiles) && typeof profileId === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(profileId)
          && parsed.profiles.some(profile => isRecord(profile) && profile.id === profileId)) {
        return path.join(userDataPath, 'profiles', profileId, 'orca-data.json');
      }
    } catch {}
  }
  return path.join(userDataPath, 'orca-data.json');
}

function updateSettings(settings, codexArgs) {
  return {
    ...settings,
    agentDefaultArgs: {
      ...(isRecord(settings?.agentDefaultArgs) ? settings.agentDefaultArgs : {}),
      codex: codexArgs,
    },
  };
}

function writeOffline(userDataPath, codexArgs) {
  const file = activeSettingsPath(userDataPath);
  let state = {};
  if (existsSync(file)) {
    state = JSON.parse(readFileSync(file, 'utf8'));
    if (!isRecord(state)) throw new Error(`Orca settings are not an object: ${file}`);
  }
  state.settings = updateSettings(isRecord(state.settings) ? state.settings : {}, codexArgs);
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${randomUUID()}.tmp`);
  writeFileSync(temporary, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
  renameSync(temporary, file);
  return file;
}

function runtimeClientPath() {
  const localAppData = process.env.LOCALAPPDATA;
  if (!localAppData) return null;
  const candidate = path.join(localAppData, 'Programs', 'orca', 'resources', 'app.asar.unpacked', 'out', 'cli', 'runtime-client.js');
  return existsSync(candidate) ? candidate : null;
}

async function configureRunningOrca(modulePath, baseUrl, catalogFile) {
  const require = createRequire(import.meta.url);
  const {RuntimeClient, getDefaultUserDataPath} = require(modulePath);
  const client = new RuntimeClient(undefined, 10_000, null, null);
  const response = await client.call('settings.get', undefined, {timeoutMs: 2_000});
  const settings = response?.result?.settings;
  if (!isRecord(settings)) throw new Error('Orca returned invalid settings');
  const codexArgs = configureCodexArgs(settings.agentDefaultArgs?.codex || '', baseUrl, catalogFile);
  await client.call('settings.update', {agentDefaultArgs: updateSettings(settings, codexArgs).agentDefaultArgs}, {timeoutMs: 10_000});
  return {appliedBy: 'runtime', settingsPath: activeSettingsPath(getDefaultUserDataPath()), codexArgs};
}

export async function configureOrca({baseUrl, catalogFile, userDataPath = path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'orca')}) {
  if (!existsSync(catalogFile)) throw new Error(`Model catalog does not exist: ${catalogFile}`);
  let current = '';
  const settingsFile = activeSettingsPath(userDataPath);
  try {
    const state = JSON.parse(readFileSync(settingsFile, 'utf8'));
    current = state?.settings?.agentDefaultArgs?.codex || '';
  } catch {}
  const codexArgs = configureCodexArgs(current, baseUrl, catalogFile);
  const modulePath = runtimeClientPath();
  if (modulePath) {
    try {
      return await configureRunningOrca(modulePath, baseUrl, catalogFile);
    } catch {}
  }
  return {appliedBy: 'offline', settingsPath: writeOffline(userDataPath, codexArgs), codexArgs};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [baseUrl, catalogFile] = process.argv.slice(2);
  if (!baseUrl || !catalogFile) throw new Error('Usage: configure-orca.mjs BASE_URL CATALOG_FILE');
  const result = await configureOrca({baseUrl, catalogFile});
  console.log(JSON.stringify({appliedBy: result.appliedBy, settingsPath: result.settingsPath}));
}
