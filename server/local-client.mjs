// Account credentials and canonical conversations stay on the client machine.
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { once } from 'node:events';

export const MODELS = ['gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol', 'gpt-6-astra'];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const PORT = 4321;

export function dossier(payload) {
  const items = Array.isArray(payload.input) ? payload.input : [{role:'user',content:payload.input}];
  const text = item => {
    const content = item.content ?? item.output ?? '';
    if (typeof content === 'string') return content;
    if (!Array.isArray(content)) return '';
    return content.map(c => typeof c.text === 'string' ? c.text : c.type === 'input_image' ? '[image attached]' : '').join('\n');
  };
  const users = items.filter(i => i.role === 'user' && !/^\s*(?:# AGENTS\.md|<environment_context>)/.test(text(i))).slice(-3);
  const assistant = items.filter(i => i.role === 'assistant').at(-1);
  const tools = items.filter(i => i.type === 'function_call_output').slice(-3);
  return JSON.stringify({tasks:users.map(i=>text(i).slice(0,2000)),
    assistant:assistant ? text(assistant).slice(-1500) : '',
    tools:tools.map(i=>text(i).slice(0,700)),
    phase:items.at(-1)?.type === 'function_call_output' ? 'tool_step' : 'user_turn'});
}

function reply(res, status, message) {
  if (res.headersSent) { res.destroy(); return; }
  res.writeHead(status, {'content-type':'application/json'});
  res.end(JSON.stringify({error:{message}}));
}

export function createLocalClient({origin, upstream = 'https://chatgpt.com/backend-api/codex/responses', fetcher = fetch}) {
  const central = new URL(origin);
  if (!['http:', 'https:'].includes(central.protocol) || central.origin !== origin || central.username || central.password) throw new Error('Invalid service origin');
  const post = async (route, body) => {
    const response = await fetcher(origin + route, {method:'POST', headers:{'content-type':'application/json'},
      body:JSON.stringify(body), redirect:'error', signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Central decision service unavailable');
    return response.json();
  };
  const server = http.createServer(async (req, res) => {
    let route, started = Date.now(), terminal, usage;
    const abort = new AbortController();
    res.on('close', () => { if (!res.writableFinished) abort.abort(); });
    try {
      if (req.headers.host !== `127.0.0.1:${server.address().port}` || req.headers.origin || req.headers['sec-fetch-site'] === 'cross-site') return reply(res,403,'Local clients only');
      if (req.method === 'GET' && req.url === '/health') {
        res.setHeader('content-type','application/json'); res.end(JSON.stringify({ok:true,service:'jev-local-client',origin})); return;
      }
      if (req.method !== 'POST' || req.url !== '/v1/responses') return reply(res,404,'Unsupported route');
      if (!/^Bearer \S+$/.test(req.headers.authorization || '')) return reply(res,401,'Use your local Codex ChatGPT login');
      let size=0; const chunks=[];
      for await (const chunk of req) { size+=chunk.length; if(size>64*1024*1024) return reply(res,413,'Request too large'); chunks.push(chunk); }
      let payload;
      try { payload=JSON.parse(Buffer.concat(chunks).toString()); } catch { return reply(res,400,'Invalid JSON'); }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return reply(res,400,'Invalid request');
      const automatic = payload.model === 'jev/auto';
      if (!automatic && !MODELS.includes(payload.model)) return reply(res,400,'Only configured OpenAI models are supported');
      if (!automatic && !EFFORTS.includes(payload.reasoning?.effort || 'medium')) return reply(res,400,'Invalid effort');
      route = await post('/v1/route', automatic ? {state:dossier(payload)} : {model:payload.model,effort:payload.reasoning?.effort || 'medium'});
      if (!MODELS.includes(route.model) || !EFFORTS.includes(route.effort) || typeof route.id !== 'string') throw new Error('Invalid route');
      payload.model=route.model;
      payload.reasoning={...payload.reasoning,effort:route.effort};
      payload.service_tier='default'; payload.store=false;
      // Forward authentication received from this Codex process only to OpenAI.
      const headers={'content-type':'application/json','accept':'text/event-stream','authorization':req.headers.authorization};
      for (const key of ['chatgpt-account-id','openai-beta','originator','version','user-agent','session_id','conversation_id','x-codex-turn-state','x-codex-turn-metadata']) {
        if (typeof req.headers[key] === 'string') headers[key]=req.headers[key];
      }
      const response = await fetcher(upstream,{method:'POST',headers,body:JSON.stringify(payload),redirect:'error',signal:abort.signal});
      const outgoing={'content-type':response.headers.get('content-type') || 'application/json'};
      for (const key of ['retry-after','x-request-id','x-codex-turn-state']) if(response.headers.has(key)) outgoing[key]=response.headers.get(key);
      res.writeHead(response.status,outgoing);
      let pending=''; const decoder=new TextDecoder();
      for await (const chunk of response.body) {
        // Parse only completion metadata; never retain or report output text.
        pending+=decoder.decode(chunk,{stream:true});
        let end;
        while((end=pending.indexOf('\n'))>=0) {
          const line=pending.slice(0,end); pending=pending.slice(end+1);
          if(line.startsWith('data: ')) try {
            const event=JSON.parse(line.slice(6));
            if(['response.completed','response.failed','response.incomplete'].includes(event.type)) {
              terminal=event.type; usage=event.response?.usage;
            }
          } catch {}
        }
        if(pending.length>2*1024*1024) pending='';
        if(!res.write(chunk)) await once(res,'drain',{signal:abort.signal});
      }
      res.end();
      const status = response.status === 200 && terminal !== 'response.completed' ? 502 : response.status;
      await post('/v1/outcome',{id:route.id,model:route.model,status,total_ms:Date.now()-started,usage}).catch(()=>{});
    } catch {
      reply(res,502,'Jev local connection failed; no alternate account or provider was used');
      if(route?.id) await post('/v1/outcome',{id:route.id,model:route.model,status:terminal === 'response.completed' ? 200 : 502,total_ms:Date.now()-started,usage}).catch(()=>{});
    }
  });
  server.requestTimeout=900000;
  server.on('upgrade', (_req,socket)=>socket.destroy());
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const {origin}=JSON.parse(readFileSync(new URL('./client.json',import.meta.url),'utf8'));
  createLocalClient({origin}).listen(PORT,'127.0.0.1');
}
