// Contract tests run real UI orchestration with a small DOM; no network or model calls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.listeners={};this.style={};this.scrollHeight=100;this.className='';}
  append(...nodes){for(const n of nodes){n.parentElement=this;this.children.push(n);if(this.tag==='select'&&!this.value)this.value=n.value;}}
  appendChild(node){this.append(node);return node;}
  prepend(...nodes){this.children.unshift(...nodes);}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  setAttribute(key,value){this[key]=value;}
  addEventListener(name,handler){this.listeners[name]=handler;}
  focus(){}
  querySelector(selector){return walk(this).find(n=>selector.startsWith('.')?n.className.split(' ').includes(selector.slice(1)):n.tag===selector);}
}
const walk=node=>[node,...node.children.flatMap(walk)],flush=()=>new Promise(setImmediate);
const a='2026-10-20T20:00:00+08:00',b='2026-10-20T21:00:00+08:00',l5='zjocean_L5_202610202000_01234567',ec='ecmwf_202610192000_01234567';
const rows=[{dataset_id:ec,batch:a,fields:{wave:{valid_times:[a,b]},wind:{valid_times:[a,b]},pressure:{valid_times:[a,b]}}},{dataset_id:l5,batch:a,source_family:'zjocean_l2_l5',level:'L5',fields:{wave:{valid_times:[a,b]},wind:{valid_times:[a,b]}}}];
const objectiveSource=fs.readFileSync('frontend/objective_panel.js','utf8');
function objectiveHarness(){
  const mapPanel=new Element('section'),chatPanel=new Element('section'),requests=[],modes=[];
  let removed=0,added=0,hold=null;
  const document={createElement:t=>new Element(t),querySelector:s=>s==='.map-panel'?mapPanel:chatPanel};
  const map={removeLayer:()=>removed++,closePopup:()=>{},fitBounds:()=>{},on:()=>{}};
  const L={Layer:{extend:()=>class{addTo(){added++;return this;}}}};
  const fetch=async url=>{
    requests.push(url);if(hold&&url.includes('/frame?')){const fn=hold;hold=null;return fn(url);}
    const p=new URL('http://local'+url).searchParams;
    return {ok:true,json:async()=>url.endsWith('/catalog')?{datasets:rows,default_dataset_id:l5}:{state:'ready',field:p.get('field'),dataset_id:p.get('dataset_id'),valid_time:p.get('valid_time'),bounds:[118,26,126,33],unit:'m/s',batch:a,stats:{maximum:{value:8},minimum:{value:1},valid_count:4}}};
  };
  const window={MapAssistant:{send:()=>assert.fail('map actions must not submit another model question'),renderAnswer:()=>{}}};
  const setMode=mode=>{modes.push(mode);window.ObjectivePanel.open();};
  vm.runInNewContext(objectiveSource,{document,window,map,L,fetch,Intl,Date,URL,URLSearchParams,setMode});
  return {api:window.ObjectivePanel,panel:mapPanel.children[0],requests,modes,hold:fn=>{hold=fn;},removed:()=>removed,added:()=>added};
}
const action=(field='wind',valid_time=b)=>({type:'objective_field',dataset_id:l5,field,valid_time,bounds:[120,27,124,32],source_family:'zjocean_l2_l5',available_fields:['wave','wind'],field_state:'ready',level:'L5',title:'本次问题的海面风'});
async function testObjective(){
  const h=objectiveHarness();await h.api.open();
  const selects=walk(h.panel).filter(n=>n.tag==='select'),[dataset,field,area]=selects;
  assert.equal(dataset.value,l5,'server recommended L5 batch should be initial selection');
  assert.deepEqual(field.children.map(n=>n.value),['wave','wind']);
  assert.ok(walk(h.panel).some(n=>n.textContent.includes('暂缺气压')));
  let result=await h.api.applyAction(action());assert.equal(result.state,'ready');
  assert.equal(dataset.value,l5);assert.equal(field.value,'wind');assert.equal(area.value,'120,27,124,32');
  assert.ok(h.requests.at(-1).includes('field=wind')&&h.requests.at(-1).includes(encodeURIComponent(b)));
  assert.ok(walk(h.panel).some(n=>n.textContent.includes('临时阵风口径')));
  assert.equal(h.modes.at(-1),'objective');
  const image=walk(h.panel).find(n=>n.tag==='a'&&n.textContent==='下载当前图件 PNG');
  const frameCount=()=>h.requests.filter(url=>url.includes('/frame?')).length,before=frameCount();
  result=await h.api.applyAction({...action('pressure'),field_state:'not_provided'});
  assert.equal(result.state,'no_data');assert.equal(frameCount(),before);assert.equal(image.hidden,true);
  assert.equal(dataset.value,l5,'unavailable L5 pressure must not switch to ECMWF');
  assert.deepEqual(field.children.map(n=>n.value),['wave','wind']);
  result=await h.api.applyAction(action('wave','2026-10-20T21:30:00+08:00'));
  assert.equal(result.state,'no_data');assert.equal(frameCount(),before,'no nearest-time substitution');
  const invalid=[{...action(),dataset_id:'../../etc/passwd'},{...action(),field:'script'},
    {...action(),url:'javascript:alert(1)'},{...action(),bounds:[120,32,124,27]},
    {...action(),bounds:[120,27,Infinity,32]},{...action(),valid_time:'2026-10-20T20:00:00'},
    {...action(),valid_time:'2026-02-30T20:00:00+08:00'},{...action(),valid_time:'2026-10-20T25:00:00+08:00'}];
  for(const bad of invalid)await assert.rejects(()=>h.api.applyAction(bad));
  assert.equal(frameCount(),before,'unsafe actions must never fetch a frame');
  let finish;
  h.hold(url=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({state:'ready',field:'wind',dataset_id:l5,valid_time:b,bounds:[118,26,126,33],stats:{}})});}));
  const old=h.api.applyAction(action());await flush();assert.equal(typeof finish,'function');
  await h.api.applyAction(action('wave',a));const added=h.added();finish();
  assert.equal((await old).state,'cancelled');assert.equal(h.added(),added,'out-of-order old request must not render');
  assert.ok(image.href.includes('field=wave')&&image.href.includes(encodeURIComponent(a)));
  h.hold(()=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({state:'ready',field:'wind',bounds:[118,26,126,33],stats:{}})});}));
  const pending=h.api.applyAction(action());await flush();h.api.cancelAction();finish();
  assert.equal((await pending).state,'cancelled');assert.equal(image.hidden,true,'new conversation cancels pending map and clears old layer');
  // A user hand-selection has the same precedence as a new conversation.
  h.hold(()=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({state:'ready',field:'wind',bounds:[118,26,126,33],stats:{}})});}));
  const background=h.api.applyAction(action());await flush();field.value='wave';await field.listeners.change();finish();
  assert.equal((await background).state,'cancelled');assert.ok(image.href.includes('field=wave'));
  const periodAction={...action('wave'),type:'objective_maximum',start:a,hours:48};delete periodAction.valid_time;
  result=await h.api.applyAction(periodAction);assert.equal(result.state,'ready');
  const maximumURL=h.requests.findLast(u=>u.includes('/maximum/frame?'));
  assert.ok(maximumURL);const periodParams=new URL('http://local'+maximumURL).searchParams;
  assert.equal(periodParams.get('start'),a);assert.equal(periodParams.get('hours'),'48');assert.equal(periodParams.get('valid_time'),null);
  assert.ok(image.href.includes('/maximum/image.png?'));
  for(const bad of [{...periodAction,hours:999},{...periodAction,valid_time:a},{...periodAction,start:'2026-02-30T20:00:00+08:00'}])await assert.rejects(()=>h.api.applyAction(bad));
  await h.api.applyAction(action('wave',a));assert.ok(image.href.includes('/image.png?')&&!image.href.includes('/maximum/'));
  console.log('PASS: L2/L5 field availability, safe actions, exact native times, automatic map selection and race cancellation');
}
function assistantHarness(apply){
  const ids=Object.fromEntries(['sendBtn','chatMessages','chatInput','conversationStatus','debugContent'].map(k=>[k,new Element('div')]));
  let answer={answer:'已经核对的完整回答',sources:[],map_action:action()},reset=0,asked=0;
  class Client{async ask(){asked++;return answer;}reset(){reset++;}}
  const document={getElementById:id=>ids[id],createElement:t=>new Element(t),createTextNode:t=>new Element('#text',t),querySelectorAll:()=>[]};
  const window={AssistantConversationClient:Client,ObjectivePanel:{applyAction:apply,cancelAction:()=>{reset++;}}};
  const article=()=>{const n=new Element('article');n.append(new Element('div'));n.children[0].className='msg-bubble';ids.chatMessages.append(n);return n;};
  vm.runInNewContext(fs.readFileSync('frontend/map_assistant.js','utf8'),{window,document,navigator:{clipboard:{writeText:async()=>{}}},appendMessage:article,appendStreamMessage:article,appendAssistantAnswer:(target,text)=>target.append(new Element('p',text)),URL,console});
  return {api:window.MapAssistant,ids,asked:()=>asked,resetCount:()=>reset,setAnswer:value=>{answer=value;}};
}
async function testAssistant(){
  const h=assistantHarness(async()=>{throw Error('模拟地图服务错误');});await h.api.send('查询海浪');
  assert.ok(walk(h.ids.chatMessages).some(n=>n.textContent==='已经核对的完整回答'));
  assert.ok(walk(h.ids.chatMessages).some(n=>n.textContent.includes('模拟地图服务错误')));
  assert.equal(h.asked(),1,'map update must not call the model again');
  let finish,guard;
  const race=assistantHarness(async(_,options)=>{guard=options.isCurrent;return new Promise(resolve=>{finish=()=>resolve({state:'ready'});});});
  const pending=race.api.send('查询海浪');await flush();assert.equal(guard(),true);race.api.reset();assert.equal(guard(),false);finish();await pending;
  assert.ok(!walk(race.ids.chatMessages).some(n=>n.textContent==='已经核对的完整回答'),'reset must not restore a superseded answer');
  assert.equal(race.ids.conversationStatus.textContent,'已开始新的对话，可连续追问。');
  console.log('PASS: map errors preserve full answers; reset cancels old map delivery without duplicated model calls');
}
(async()=>{await testObjective();await testAssistant();})().catch(error=>{console.error(error);process.exitCode=1;});
