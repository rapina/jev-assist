import http from 'node:http';
import { closeSync, constants, fstatSync, lstatSync, openSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { privateFileIsProtected } from '../router/src/file-security.mjs';

const BACKEND_ORIGIN = 'http://127.0.0.1:4319';
const DASHBOARD_GET = new Set(['/dashboard', '/dashboard/', '/dashboard/dashboard.js', '/dashboard/dashboard.css', '/dashboard/install-client.ps1', '/dashboard/uninstall-client.ps1', '/dashboard/client.zip',
   '/dashboard/api/records', '/dashboard/api/record', '/dashboard/api/observations']);
const HOP_HEADERS = new Set(['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade']);

function headersWithoutHops(headers) {
  const excluded = new Set([...HOP_HEADERS, ...(headers.connection || '').split(',').map((part) => part.trim().toLowerCase())]);
  return Object.fromEntries(Object.entries(headers).filter(([key]) => !excluded.has(key)));
}

function reply(response, status, value, extra = {}) {
  if (response.destroyed) return;
  if (response.headersSent) { response.destroy(); return; }
  const data = Buffer.from(JSON.stringify(value));
  response.writeHead(status, { 'content-type': 'application/json', 'content-length': data.length,
    'cache-control': 'no-store', 'connection': 'close', 'x-content-type-options': 'nosniff', ...extra });
  response.end(data);
}

export function createGatewayServer({ internalKey,
  origin = 'http://127.0.0.1:4320', backendPort = 4319 } = {}) {
  if (typeof internalKey !== 'string' || !internalKey.trim()) {
    throw new Error('Internal credential is required');
  }
  const publicUrl = new URL(origin);
  if (publicUrl.protocol !== 'http:' || publicUrl.username || publicUrl.password || origin !== publicUrl.origin) {
    throw new Error('JEV_GATEWAY_ORIGIN must be an HTTP origin without a path');
  }
  const server = http.createServer({ maxHeaderSize: 16 * 1024, headersTimeout: 15000, requestTimeout: 15000 }, (request, response) => {
    if (request.headers.host !== publicUrl.host || !request.url.startsWith('/') || request.url.startsWith('//')) {
      return reply(response, 403, { error: 'Invalid gateway host or request target' });
    }
    const pathname = request.url.split('?')[0];
    if (request.method === 'POST' && pathname === '/v1/responses') return reply(response, 410, {error:'Central execution is disabled. Reinstall the Jev Assist client for local-account execution.'});
    const authRoute = (request.method === 'POST' && ['/v1/route', '/v1/outcome', '/v1/observations', '/v1/ask'].includes(pathname))
      || (request.method === 'GET' && pathname === '/v1/models');
    const dashboardRoute = request.method === 'GET' && DASHBOARD_GET.has(pathname);
    if (request.headers.origin !== undefined && request.headers.origin !== origin
        || request.headers['sec-fetch-site'] === 'cross-site'
        || authRoute && request.headers.origin !== undefined) {
      return reply(response, 403, { error: 'Browser origin not permitted' });
    }
    if (request.method === 'GET' && pathname === '/health') {
      return reply(response, 200, { ok: true, service: 'jev-lan-gateway' });
    }
    if (!authRoute && !dashboardRoute) return reply(response, 404, { error: 'Route not available' });
    const length = request.headers['content-length'];
    if (request.headers['transfer-encoding'] || request.method === 'POST' && (!length || !/^\d+$/.test(length))) {
      return reply(response, 400, { error: 'A fixed Content-Length is required' });
    }
    const maximum = pathname === '/v1/route' ? 24000 : pathname === '/v1/outcome' ? 4096 : pathname === '/dashboard/session' ? 1024 : pathname === '/v1/ask' ? 512 * 1024 : pathname === '/v1/observations' ? 65536 : 16384;
    if (length !== undefined && (!/^\d+$/.test(length) || Number(length) > maximum || request.method === 'GET' && Number(length) > 0)) {
      return reply(response, 413, { error: 'Request body is not permitted or exceeds its limit' });
    }
    const headers = headersWithoutHops(request.headers);
    for (const key of Object.keys(headers)) if (['host', 'authorization', 'forwarded'].includes(key) || key.startsWith('x-forwarded-')) delete headers[key];
    headers.host = '127.0.0.1:4319';
    if (length !== undefined) headers['content-length'] = length;
    headers.authorization = `Bearer ${internalKey}`;
    delete headers.cookie;
    if (headers.origin !== undefined) headers.origin = BACKEND_ORIGIN;

    const upstream = http.request({ hostname: '127.0.0.1', port: backendPort, method: request.method, path: request.url,
      headers, agent: false }, (incoming) => {
      incoming.on('error', () => reply(response, 502, { error: 'Upstream response interrupted' }));
      incoming.on('aborted', () => reply(response, 502, { error: 'Upstream response interrupted' }));
        const outgoingHeaders = headersWithoutHops(incoming.headers);
        delete outgoingHeaders.authorization;
        if (outgoingHeaders.location?.startsWith(BACKEND_ORIGIN + '/')) outgoingHeaders.location = origin + outgoingHeaders.location.slice(BACKEND_ORIGIN.length);
        response.writeHead(incoming.statusCode || 502, outgoingHeaders);
        incoming.pipe(response);
    });
    upstream.setTimeout(900000, () => upstream.destroy(new Error('timeout')));
    upstream.on('error', () => reply(response, 502, { error: 'Upstream unavailable' }));
    request.on('aborted', () => upstream.destroy());
    request.on('error', () => upstream.destroy());
    response.on('close', () => { if (!response.writableFinished) upstream.destroy(); });
    request.pipe(upstream);
  });
  server.maxConnections = 64;
  server.keepAliveTimeout = 5000;
  server.setTimeout(900000, (socket) => socket.destroy());
  server.on('upgrade', (_request, socket) => socket.destroy());
  server.on('clientError', (_error, socket) => {
    if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    else socket.destroy();
  });
  return server;
}

export function readGatewayKeys(env = process.env) {
  const state = env.MODEL_ROUTER_STATE_DIR || env.CODEX_ROUTER_STATE_DIR || env.KIMI_CODEX_STATE_DIR
    || path.join(env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'codex-router');
  const files = [path.join(state, 'generic-provider-credentials', 'jev.key')];
  const handles = [];
  try {
    for (const file of files) {
      if (!lstatSync(file).isFile() || !privateFileIsProtected(file)) throw new Error('Credential must be a protected regular file');
      const fd = openSync(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0)); handles.push(fd);
      const info = fstatSync(fd);
      if (!info.isFile() || info.size > 4096 || process.platform !== 'win32' && (info.uid !== process.getuid() || info.mode & 0o077)) {
        throw new Error('Credential must be owner-only');
      }
    }
    const keys = handles.map((fd) => readFileSync(fd, 'utf8').trim());
    if (keys.some((key) => key.length < 16 || /\s/.test(key))) throw new Error('Credential is empty or invalid');
    return { internalKey: keys[0] };
  } catch { throw new Error('Gateway credentials are missing, invalid, or not owner-only'); }
  finally { for (const fd of handles) closeSync(fd); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== '--config')) throw new Error('Usage: lan-gateway.mjs [--config file.json]');
    const config = args.length ? JSON.parse(readFileSync(args[1], 'utf8')) : {};
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Invalid gateway config');
    for (const key of ['host', 'origin', 'stateDir']) {
      if (config[key] !== undefined && (typeof config[key] !== 'string' || !config[key].trim())) throw new Error('Invalid gateway config');
    }
    const port = config.port ?? 4320;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid gateway port');
    const env = { ...process.env };
    if (config.stateDir) env.MODEL_ROUTER_STATE_DIR = config.stateDir;
    const server = createGatewayServer({ ...readGatewayKeys(env), origin: config.origin || env.JEV_GATEWAY_ORIGIN || 'http://127.0.0.1:4320' });
    server.on('error', () => { console.error('Gateway could not listen'); process.exitCode = 1; });
    server.listen(port, config.host || env.JEV_GATEWAY_HOST || '127.0.0.1', () => console.log(`Jev LAN gateway ready on port ${port}`));
  } catch { console.error('Gateway startup failed; check protected key files and configured origin'); process.exitCode = 1; }
}
