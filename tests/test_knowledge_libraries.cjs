// Federation UI contract: real rendering/query code, stub DOM, no browser/model/network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const libraries=require('../frontend/knowledge_libraries.js');
class Node{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.className='';this.listeners={};this.dataset={};}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.append(node);return node;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(key,value){this[key]=value;}
  querySelector(selector){return walk(this).find(n=>selector.startsWith('.')?n.className.split(' ').includes(selector.slice(1)):n.tag===selector);}
  before(){}
}
const walk=n=>[n,...n.children.flatMap(walk)],texts=n=>walk(n).map(v=>v.textContent).join('\n');
const document={createElement:tag=>new Node(tag)};
const html=fs.readFileSync('frontend/knowledge.html','utf8');
const script=Array.from(html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)).map(m=>m[1]).find(s=>s.includes('async function loadSources'));
assert.ok(html.includes('<h1>知识库管理</h1>'));
assert.ok(html.includes('source-library')&&html.includes('所属资料库'));
assert.ok(html.includes('/knowledge_libraries.js'));
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))if(match[1].trim())new vm.Script(match[1]);
assert.deepEqual(libraries.initialSelection(''),{library:'all',source:''});
assert.deepEqual(libraries.initialSelection('?library=legacy'),{library:'legacy',source:''});
assert.deepEqual(libraries.initialSelection('?library=marine&source=legacy%3Aabc'),{library:'legacy',source:'legacy:abc'});
assert.deepEqual(libraries.initialSelection('?library=legacy&source=SOURCE-1'),{library:'marine',source:'SOURCE-1'});
assert.deepEqual(libraries.initialSelection('?library=https%3A%2F%2Fevil.test'),{library:'all',source:''});
assert.equal(libraries.libraryOf({source_id:'legacy:abc',library:'marine'}),'legacy');
assert.equal(libraries.readOnly({source_id:'legacy:abc',read_only:false}),true);
assert.equal(libraries.readOnly({source_id:'SOURCE-1'}),false);
assert.match(libraries.reviewProgress({metadata:{source_type:'official_document_card'}}),/不含正文条款/);
assert.match(libraries.reviewProgress({status:'pending_review',metadata:{structured_archive_state:'raw_values_recovered'}}),/完整日期和基准待核/);
assert.match(libraries.reviewProgress({metadata:{formal_replacement_source_ids:['new']}}),/正式替代正文已入库/);
assert.equal(libraries.reviewProgress({source_id:'legacy:old',metadata:{structured_archive_state:'raw_values_recovered'}}),'');
assert.equal(libraries.reviewProgress({review_progress:'原值已恢复；口径待确认'}),'原值已恢复；口径待确认');
assert.deepEqual(libraries.reviewGroup({status:'pending_review',review_group:{id:'partial',label:'untrusted label',reason:'原件全文待核'}}),{id:'partial',label:'部分内容已审核',reason:'原件全文待核'});
assert.equal(libraries.reviewGroup({status:'approved',review_group:{id:'partial'}}),null);
assert.equal(libraries.reviewGroup({source_id:'legacy:x',status:'pending_review',review_group:{id:'partial'}}),null);
const groups=new Node('div'),selectedGroups=[];
libraries.renderReviewGroups(groups,{groups:[{id:'structured',count:881,description:'<img src=x onerror=bad()>'},{id:'partial',count:9},{id:'fulltext',count:28},{id:'version',count:11}]},id=>selectedGroups.push(id),'partial',document);
assert.equal(groups.children.length,4);
assert.match(texts(groups),/881/);assert.match(texts(groups),/部分内容已审核/);
assert.equal(groups.children[1]['aria-pressed'],'true');
groups.children[0].listeners.click();assert.deepEqual(selectedGroups,['structured']);
assert.equal(walk(groups).filter(n=>n.tag==='img').length,0);
libraries.renderReviewGroups(groups,null,()=>{},'',document);
assert.match(texts(groups),/数量尚未确认/);assert.ok(!texts(groups).includes('0'));
assert.equal(libraries.normalizeReviewGroup('unknown'), '');
const summary=new Node('section');
libraries.renderSummary(summary,{libraries:[{id:'marine',record_count:2794,chunk_count:25095,available:true},{id:'legacy',record_count:452,chunk_count:45052,available:true}],query_mode:'joint_local_retrieval',count_note:'<img src=x onerror=bad()>跨库未去重'},document);
assert.match(texts(summary),/452条来源记录/);assert.match(texts(summary),/45,052个切片/);
assert.match(texts(summary),/主问答按问题需要联合检索/);assert.equal(walk(summary).filter(n=>n.tag==='img').length,0);
assert.ok(texts(summary).includes('<img src=x onerror=bad()>'));
libraries.renderSummary(summary,{libraries:[{id:'marine',record_count:2794,chunk_count:25095,available:true},{id:'legacy',available:false}],query_mode:'marine_only'},document);
assert.match(texts(summary),/历史台风资料库：暂不可用，数量未确认/);assert.match(texts(summary),/当前只有专业资料库/);
assert.ok(!texts(summary).includes('历史台风资料库：0'));
console.log('PASS: library labels/count scopes, deep links, unavailable libraries and inert untrusted text');

function fragment(first,last){return script.slice(script.indexOf(first),script.indexOf(last));}
function harness(){
  const ids=Object.fromEntries(['source-library','source-query','source-status','source-review-group','source-category','source-rows','source-empty','source-summary','page-label','prev-page','next-page','source-message','source-detail'].map(k=>[k,new Node('div')]));
  ids['source-library'].value='legacy';ids['source-query'].value='台风';
  let requests=[],opened=[],deferred=null,failed=false,errors=[];
  const state={page:6,pageSize:40,sourceRequest:0,total:0};
  const el=(tag,text,cls)=>{const n=new Node(tag,text===undefined||text===null?'':String(text));n.className=cls||'';return n;};
  const module={...libraries,renderReadOnlyLinks:(target,data)=>libraries.renderReadOnlyLinks(target,data,document)};
  const context={window:{KnowledgeLibraries:module},document,state,URLSearchParams,
    $:id=>ids[id],el,first:(obj,keys,fallback)=>{for(const k of keys)if(obj?.[k]!==undefined)return obj[k];return fallback;},
    numeric:n=>Number(n).toLocaleString('zh-CN'),categories:{historical_report:'历史报告'},statusNames:{},
    pill:value=>el('span',value,'pill'),message:(id,text)=>{ids[id].textContent=text;},addCategory:()=>{},loadDetail:id=>opened.push(id),
    appendFact:(node,label,value)=>{if(value!==undefined&&value!==null)node.append(el('dt',label),el('dd',value));},warningLines:source=>source.warnings||[],
    api:async path=>{
      requests.push(path);if(deferred){const p=deferred;deferred=null;return p;}if(failed)throw Error('模拟旧库离线');
      return {items:[{source_id:'legacy:DOC-241',filename:'<img src=x>旧台风资料',category:'historical_report',status:'indexed',chunk_count:4}],total:452,page:6,page_size:40,library_errors:errors};
    }};
  vm.createContext(context);vm.runInContext(fragment('  async function loadSources','  function appendFact'),context);
  return {ids,state,context,requests,opened,hold:p=>{deferred=p;},fail:()=>{failed=true;},libraryError:()=>{errors=[{library:'legacy',error_type:'Offline'}];}};
}
async function tableTests(){
  const h=harness();await h.context.loadSources();
  const search=new URL('http://local'+h.requests[0]).searchParams;
  assert.equal(search.get('library'),'legacy');assert.equal(search.get('page'),'6');assert.equal(search.get('q'),'台风');
  assert.match(h.ids['source-summary'].textContent,/452 条来源记录/);assert.equal(h.ids['next-page'].disabled,false,'more than 200 historical records must remain pageable');
  assert.match(texts(h.ids['source-rows']),/历史台风资料库/);assert.match(texts(h.ids['source-rows']),/历史文本 \/ 只读/);
  const button=walk(h.ids['source-rows']).find(n=>n.tag==='button');button.listeners.click();assert.deepEqual(h.opened,['legacy:DOC-241']);
  assert.equal(walk(h.ids['source-rows']).filter(n=>n.tag==='img').length,0);
  // The previous library's slower response must not replace a newer selection.
  let complete;h.hold(new Promise(resolve=>{complete=resolve;}));const old=h.context.loadSources();h.ids['source-library'].value='marine';await h.context.loadSources();
  complete({items:[],total:0,page:1,page_size:40});await old;
  assert.equal(walk(h.ids['source-rows']).filter(n=>n.tag==='tr').length,1);
  h.ids['source-library'].value='all';h.libraryError();await h.context.loadSources();
  assert.match(h.ids['source-message'].textContent,/历史台风资料库本次读取失败/);
  assert.match(h.ids['source-message'].textContent,/未将不可用资料库计为0/);
  h.ids['source-library'].value='marine';h.ids['source-status'].value='pending_review';h.ids['source-review-group'].value='structured';
  await h.context.loadSources();
  const groupedSearch=new URL('http://local'+h.requests.at(-1)).searchParams;
  assert.equal(groupedSearch.get('review_group'),'structured');
  assert.match(h.ids['source-summary'].textContent,/原值已恢复、业务口径待确认/);
  h.fail();await h.context.loadSources();assert.equal(h.ids['source-rows'].children.length,0);assert.equal(h.ids['next-page'].disabled,true);
  assert.match(h.ids['source-empty'].textContent,/读取失败/);
  console.log('PASS: library query/paging, historical source badges, row navigation and stale-response rejection');
}
function detailTests(){
  const h=harness();vm.runInContext(fragment('  function renderDetail','  async function submitReview'),h.context);
  h.context.renderDetail({source:{source_id:'legacy:doc',filename:'<svg onload=bad()>旧库',status:'indexed',read_only:false,original_available:true},chunks:[{text:'保留文本',locator:'切片1'}],links:[{chunk_id:7,sid:'2019214N15134',confidence:.92,review_status:'auto_accepted'}]});
  const target=h.ids['source-detail'];assert.match(texts(target),/历史台风 SID 关联/);assert.match(texts(target),/2019214N15134/);
  assert.equal(walk(target).filter(n=>['button','input','select','a','svg'].includes(n.tag)).length,0,'legacy details must have no review/export/write/original controls');
  vm.runInContext(fragment('  function attachOriginalFile','  async function loadDetail'),h.context);
  h.context.attachOriginalFile({source_id:'legacy:doc',original_available:true});assert.equal(walk(target).filter(n=>n.tag==='a').length,0);
  vm.runInContext(fragment('  function attachExternalConsent','  function renderDetail'),h.context);
  h.context.attachExternalConsent({source_id:'legacy:doc',external_allowed:true});
  assert.equal(walk(target).filter(n=>n.tag==='input').length,0);
  assert.match(script,/allow_web:false/,'workbench QA acceptance stays local-only');
  assert.ok(!html.includes('/api/typhoon/knowledge/import'));
  console.log('PASS: legacy details remain read-only despite contradictory flags; no original/export approval or old writes; local QA preserved');
}
(async()=>{await tableTests();detailTests();})().catch(error=>{console.error(error);process.exitCode=1;});
