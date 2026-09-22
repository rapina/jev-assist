import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createLocalClient, MAX_REQUEST_BYTES } from './local-client.mjs';

test('local transport allows image-bearing requests beyond the old 64 MiB cap', () => {
  assert.equal(MAX_REQUEST_BYTES, 256 * 1024 * 1024);
});

test('central decides; local credentials and complete input go only to OpenAI; 429 does not latch', async t => {
  const calls=[]; let status=429;
  const server=createLocalClient({origin:'http://central.invalid',fetcher:async (url,options)=>{
    calls.push({url,...options,body:JSON.parse(options.body)});
    if(url.endsWith('/v1/route')) return Response.json({id:'fixture',model:'gpt-5.6-terra',effort:'medium'});
    if(url.endsWith('/v1/outcome')) return Response.json({ok:true});
    assert.equal(url,'https://chatgpt.com/backend-api/codex/responses');
    return new Response(status===429 ? '{"error":"rate limit"}' : 'data: {"type":"response.completed","response":{"usage":{"input_tokens":10,"output_tokens":1}}}\n\n',
      {status,headers:{'content-type':status===429 ? 'application/json' : 'text/event-stream'}});
  }});
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  t.after(()=>{server.closeAllConnections();server.close();});
  const input=[{role:'developer',content:'PRIVATE_SYSTEM_CONTEXT'},{role:'user',content:'Implement clamp'},
    {type:'function_call_output',call_id:'x',output:'a'.repeat(4000)+'PRIVATE_TOOL_TAIL'}];
  const send=async model=>fetch(`http://127.0.0.1:${server.address().port}/v1/responses`,{method:'POST',
    headers:{authorization:'Bearer LOCAL_ONLY','chatgpt-account-id':'LOCAL_ACCOUNT','content-type':'application/json'},
    body:JSON.stringify({model,input,stream:true})});
  assert.equal((await send('jev/auto')).status,429);
  status=200;
  assert.equal(await (await send('jev/auto')).text(),'data: {"type":"response.completed","response":{"usage":{"input_tokens":10,"output_tokens":1}}}\n\n');
  assert.equal((await send('deepseek/deepseek-v4.1-flash')).status,400);
  const native=calls.filter(c=>c.url.startsWith('https://chatgpt.com/'));
  assert.equal(native.length,2);
  for(const call of native) {
    assert.equal(call.headers.authorization,'Bearer LOCAL_ONLY');
    assert.equal(call.headers['chatgpt-account-id'],'LOCAL_ACCOUNT');
    assert.equal(call.body.model,'gpt-5.6-terra');
    assert.deepEqual(call.body.input,input);
  }
  for(const call of calls.filter(c=>c.url.startsWith('http://central.invalid'))) {
    assert.equal(call.headers.authorization,undefined);
    assert.doesNotMatch(JSON.stringify(call),/LOCAL_ONLY|LOCAL_ACCOUNT|PRIVATE_SYSTEM_CONTEXT|PRIVATE_TOOL_TAIL/);
  }
});

test('central outage falls back locally, fixed models never wait, and recovery is immediate', async t => {
  let online=false; const executed=[];
  const server=createLocalClient({origin:'http://central.invalid',fetcher:async(url,options)=>{
    const body=JSON.parse(options.body);
    if(url.endsWith('/v1/route')) {
      if(body.model) return new Promise(()=>{}); // Telemetry cannot delay a fixed selection.
      if(!online) throw new Error('offline');
      return Response.json({id:'recovered',model:'gpt-5.6-terra',effort:'low'});
    }
    if(url.endsWith('/v1/outcome')) return Response.json({ok:true});
    executed.push([body.model,body.reasoning.effort]);
    return new Response('data: {"type":"response.completed","response":{}}\n\n');
  }});
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  t.after(()=>{server.closeAllConnections();server.close();});
  const send=async model=>{
    const response=await fetch(`http://127.0.0.1:${server.address().port}/v1/responses`,{method:'POST',
      headers:{authorization:'Bearer LOCAL_ONLY','content-type':'application/json'},
      body:JSON.stringify({model,input:'fixture',stream:true,reasoning:{effort:'high'}}),signal:AbortSignal.timeout(2000)});
    await response.text(); return response;
  };
  assert.equal((await send('jev/auto')).headers.get('x-jev-routing'),'local_fallback');
  assert.equal((await send('gpt-6-astra')).status,200);
  online=true;
  assert.equal((await send('jev/auto')).headers.get('x-jev-routing'),'central');
  assert.deepEqual(executed,[['gpt-5.6-sol','high'],['gpt-6-astra','high'],['gpt-5.6-terra','low']]);
});
