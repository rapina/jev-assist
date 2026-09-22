import assert from 'node:assert/strict';
import test from 'node:test';
import { configure } from './configure-client.mjs';

test('remote default preserves unrelated settings and is idempotent', () => {
  const before = 'model = "old"\n# retain\ninstructions = """\nmodel = "embedded"\n"""\n[features]\nx = true\n';
  const args = ['http://127.0.0.1:4320', 'C:\\private\\models.json'];
  const after = configure(before, ...args);
  assert.match(after, /^model = "jev\/auto"/);
  assert.ok(after.includes('model = "embedded"'));
  assert.ok(after.includes('[features]\nx = true'));
  assert.ok(after.includes('supports_websockets = false'));
  assert.ok(after.includes('requires_openai_auth = true'));
  assert.ok(after.includes('http://127.0.0.1:4321/v1'));
  assert.ok(!after.includes('127.0.0.1:4320'));
  assert.ok(!after.includes('.auth]'));
  assert.equal(configure(after, ...args), after);
  const migrated = configure('model_provider="jev-buildbox"\n[model_providers.jev-buildbox]\nname="old"\n', ...args);
  assert.ok(!migrated.includes('jev-buildbox'));
  assert.match(migrated, /model_provider = "jev-local"/);
  assert.throws(() => configure('[profiles.direct]\nmodel_provider="openai"', ...args), /profile provider/);
  for (const profile of ['profiles.direct.model_provider="openai"', 'profiles={direct={model_provider="openai"}}']) assert.throws(() => configure(profile, ...args), /profile provider/);
  assert.throws(() => configure('', 'http://user:secret@host', args[1]), /HTTP origin/);
});
