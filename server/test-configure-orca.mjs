import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {activeSettingsPath, configureCodexArgs} from './configure-orca.mjs';

test('replaces only managed Codex overrides and remains idempotent', () => {
  const catalog = path.join(os.tmpdir(), 'jev catalog', 'models.json');
  const before = '--dangerously-bypass-approvals-and-sandbox -c model=x --config=openai_base_url=http://old/v1 -c "model_catalog_json=C:/old models.json"';
  const once = configureCodexArgs(before, 'http://127.0.0.1:4202', catalog);
  const twice = configureCodexArgs(once, 'http://127.0.0.1:4202', catalog);
  assert.equal(once, twice);
  assert.match(once, /--dangerously-bypass-approvals-and-sandbox/);
  assert.match(once, /-c model=x/);
  assert.doesNotMatch(once, /http:\/\/old/);
  assert.match(once, /openai_base_url=http:\/\/127\.0\.0\.1:4202\/v1/);
  assert.match(once, /"model_catalog_json=.*jev catalog.*models\.json"/);
});

test('resolves the active Orca profile instead of an account home', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jev-orca-'));
  mkdirSync(path.join(root, 'profiles', 'work'), {recursive: true});
  writeFileSync(path.join(root, 'orca-profile-index.json'), JSON.stringify({activeProfileId: 'work', profiles: [{id: 'work'}]}));
  assert.equal(activeSettingsPath(root), path.join(root, 'profiles', 'work', 'orca-data.json'));
});
