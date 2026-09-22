import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createGatewayServer, readGatewayKeys } from './lan-gateway.mjs';
import { writePrivateFile } from '../router/src/file-security.mjs';

const internalKey = 'internal-fixture-credential';
const origin = 'http://127.0.0.1:4320';
async function listen(server) { server.listen(0, '127.0.0.1'); await once(server, 'listening'); return server.address().port; }
async function fixture(t, handler, extra = {}) {
  const backend = http.createServer(handler), backendPort = await listen(backend);
  const gateway = createGatewayServer({ internalKey, origin, backendPort, ...extra });
  const port = await listen(gateway);
  t.after(async () => { gateway.closeAllConnections(); backend.closeAllConnections(); await Promise.all([new Promise((resolve) => gateway.close(resolve)), new Promise((resolve) => backend.close(resolve))]); });
  return { gateway, port };
}
function request(port, pathname, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path: pathname, method,
      headers: { host: '127.0.0.1:4320', ...(body === undefined ? {} : { 'content-length': Buffer.byteLength(body) }), ...headers } }, (res) => {
      const chunks = []; res.on('data', (chunk) => chunks.push(chunk)); res.on('error', reject);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject); req.end(body);
  });
}

test('internal network needs no client bearer; host and browser boundaries precede upstream access', async (t) => {
  let calls = 0;
  const { port } = await fixture(t, (_req, res) => { calls++; res.end('{}'); });
  assert.equal((await request(port, '/health')).status, 200);
  assert.equal((await request(port, '/v1/responses', {method:'POST',body:'{}'})).status, 410);
  assert.equal((await request(port, '/v1/models', { headers: { host: 'evil.invalid' } })).status, 403);
  assert.equal((await request(port, '/v1/models', { headers: { authorization: 'Bearer ignored-client-value', origin } })).status, 403);
  assert.equal((await request(port, '/ask', { method: 'POST', body: '{}' })).status, 404);
  assert.equal((await request(port, '/v1/models')).status, 200);
  assert.equal((await request(port, '/v1/models')).status, 200);
  assert.equal((await request(port, '/v1/observations', {method:'POST', body:'{}'})).status, 200);
  assert.equal((await request(port, '/v1/ask', {method:'POST', body:'{}'})).status, 200);
  assert.equal(calls, 4);
});

test('stream relay preserves payload and strips external capability and hop headers', async (t) => {
  const payload = JSON.stringify({ model: 'jev/auto', input: 'fixture input', stream: true });
  let observed;
  const { port } = await fixture(t, (req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      observed = { headers: req.headers, body: Buffer.concat(chunks).toString() };
      res.writeHead(200, { 'content-type': 'text/event-stream', connection: 'keep-alive, x-remove', 'x-remove': 'fixture' });
      res.write('data: {"type":"start"}\n\n'); setTimeout(() => res.end('data: {"type":"response.completed"}\n\n'), 10);
    });
  });
  const result = await request(port, '/v1/route', { method: 'POST', body: payload,
    headers: { authorization: 'Bearer ignored-client-value', 'content-type': 'application/json', connection: 'keep-alive, x-remove',
      'x-remove': 'fixture', cookie: 'private=fixture', 'x-forwarded-host': 'evil.invalid', 'openai-beta': 'responses=v1' } });
  assert.equal(result.status, 200); assert.match(result.body, /response.completed/);
  assert.equal(observed.body, payload); assert.equal(observed.headers.authorization, `Bearer ${internalKey}`);
  assert.equal(observed.headers.host, '127.0.0.1:4319'); assert.equal(observed.headers.cookie, undefined);
  assert.equal(observed.headers['x-remove'], undefined); assert.equal(observed.headers['x-forwarded-host'], undefined);
  assert.equal(observed.headers['openai-beta'], 'responses=v1'); assert.equal(result.headers['x-remove'], undefined);
});

test('dashboard opens without client cookies and same-origin is translated', async (t) => {
  let observed;
  const { port } = await fixture(t, (req, res) => {
    observed = req.headers;
    res.writeHead(req.headers.authorization === `Bearer ${internalKey}` ? 200 : 401,
      { 'set-cookie': 'jev_dashboard=fixture; HttpOnly; SameSite=Strict; Path=/dashboard' }); res.end('{}');
  });
  assert.equal((await request(port, '/dashboard/uninstall-client.ps1')).status, 200);
  assert.equal((await request(port, '/dashboard/api/records')).status, 200);
  assert.equal((await request(port, '/dashboard/api/records', { headers: { origin: 'http://evil.invalid' } })).status, 403);
  assert.equal((await request(port, '/dashboard', { headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
  const result = await request(port, '/dashboard/api/records', { method: 'GET',
    headers: { origin, cookie: 'jev_dashboard=fixture', authorization: 'Bearer ignored-client-value', 'content-type': 'application/json' } });
  assert.equal(result.status, 200); assert.equal(observed.authorization, `Bearer ${internalKey}`);
  assert.equal(observed.origin, 'http://127.0.0.1:4319'); assert.equal(observed.cookie, undefined);
  assert.match(result.headers['set-cookie'][0], /HttpOnly/);
});

test('response chunks arrive before upstream completion and client abort closes upstream', { timeout: 3000 }, async (t) => {
  let closed;
  const upstreamClosed = new Promise((resolve) => { closed = resolve; });
  const { port } = await fixture(t, (_req, res) => {
    res.on('close', closed);
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  await new Promise((resolve, reject) => {
    const client = http.get({ hostname: '127.0.0.1', port, path: '/v1/models',
      headers: { host: '127.0.0.1:4320', authorization: 'Bearer ignored-client-value' } }, (res) => {
      res.once('data', (chunk) => { assert.match(chunk.toString(), /first/); res.destroy(); resolve(); });
      res.on('error', reject);
    });
    client.on('error', reject);
  });
  await upstreamClosed;
});

test('missing credentials fail closed before a listener exists', () => {
  assert.throws(() => readGatewayKeys({MODEL_ROUTER_STATE_DIR:'Z:/nonexistent-jev-fixture'}), /credential/i);
  assert.throws(() => createGatewayServer({}), /credential/i);
  assert.throws(() => createGatewayServer({ internalKey, origin: `${origin}/bad` }), /origin/);
});

test('startup reads only protected fixture keys', (t) => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'jev-gateway-keys-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const credentialDir = path.join(directory, 'generic-provider-credentials');
  mkdirSync(credentialDir);
  const keyFile = path.join(credentialDir, 'jev.key');
  const env = { MODEL_ROUTER_STATE_DIR: directory };
  writeFileSync(keyFile, internalKey, { mode: 0o644 });
  assert.throws(() => readGatewayKeys(env), /owner-only/);
  writePrivateFile(keyFile, internalKey);
  writePrivateFile(path.join(credentialDir, 'jev.key'), internalKey);
  assert.deepEqual(readGatewayKeys(env), { internalKey });
});
