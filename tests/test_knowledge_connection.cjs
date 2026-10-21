// Exercise the actual knowledge-page API/error helpers without network/model calls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('frontend/knowledge.html','utf8');
const script=Array.from(html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)).map(m=>m[1]).find(s=>s.includes('async function api'));
const helpers=script.slice(script.indexOf('  function errorText'),script.indexOf("  if(window.location.protocol==='file:')"));
function harness(protocol='http:'){
  const ids={},calls=[],errors=[];
  const node=()=>({textContent:'',hidden:false,disabled:false,children:[],replaceChildren(...children){this.children=children;}});
  const state={token:'old-token',status:{review_groups:{total:922}}};
  const context={state,Headers,Error,window:{location:{protocol,origin:protocol==='file:'?'null':'http://127.0.0.1:8774'}},
    $:id=>ids[id]||(ids[id]=node()),
    el:(tag,text)=>({...node(),tag,textContent:text}),
    first:(obj,keys,fallback)=>{for(const key of keys)if(obj?.[key]!==undefined&&obj[key]!==null)return obj[key];return fallback;},
    setWriteAvailability:()=>{},message:(id,text,error)=>errors.push({id,text,error}),
    fetch:async(path,options)=>{calls.push({path,options});throw new TypeError('Failed to fetch');}};
  vm.createContext(context);vm.runInContext(helpers,context);
  return {context,ids,calls,errors,state};
}
(async()=>{
  const h=harness();
  await assert.rejects(h.context.api('/api/knowledge/status'),/无法连接知识库服务（http:\/\/127\.0\.0\.1:8774）.*刷新状态/);
  assert.equal(h.calls[0].path,'/api/knowledge/status','keep same-origin relative API paths');
  assert.equal(h.calls[0].options.credentials,'same-origin');
  h.context.statusReadFailed(new Error('连接失败'));
  assert.equal(h.state.token,'');assert.equal(h.state.status,null);
  for(const id of ['stat-sources','stat-chunks','stat-approved','stat-pending'])assert.equal(h.ids[id].textContent,'—','unknown counts must not become zero');
  assert.match(h.ids['review-group-note'].textContent,/读取失败.*尚未确认/);
  assert.ok(!h.ids['review-group-note'].textContent.includes('正在读取'));
  h.context.fetch=async()=>({ok:false,status:403,json:async()=>({detail:'访问被拒绝'})});
  await assert.rejects(h.context.api('/api/knowledge/status'),/访问被拒绝/,'HTTP errors remain distinguishable from connection failures');
  h.context.fetch=async()=>({ok:false,status:503,json:async()=>{throw new Error('invalid json');}});
  await assert.rejects(h.context.api('/api/knowledge/status'),/有效 JSON（HTTP 503）/);
  const file=harness('file:');
  await assert.rejects(file.context.api('/api/knowledge/status'),/当前通过本地文件打开/);
  file.context.showFileAccessWarning();
  assert.equal(file.calls.length,0,'file mode must not probe ports or request local-file APIs');
  assert.equal(file.ids['library-summary'].children[1].href,'http://127.0.0.1:8774/knowledge.html');
  assert.match(file.ids['source-empty'].textContent,/本地文件模式/);
  assert.equal(file.ids['prev-page'].disabled,true);assert.equal(file.ids['next-page'].disabled,true);
  assert.ok(script.indexOf("if(window.location.protocol==='file:'){showFileAccessWarning();return;}")<script.indexOf('const initial=window.KnowledgeLibraries.initialSelection'),'file warning must precede external-module-dependent initialization');
  assert.ok(!helpers.includes('location.href='),'no automatic redirects');
  console.log('PASS: same-origin API failures, HTTP errors, unknown counts, review-group failure and manual file-mode recovery');
})().catch(error=>{console.error(error);process.exitCode=1;});
