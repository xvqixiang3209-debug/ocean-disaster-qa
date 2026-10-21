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
function objectiveHarness(catalogue=rows){
  const mapPanel=new Element('section'),chatPanel=new Element('section'),requests=[],modes=[];
  let removed=0,added=0,hold=null;
  const document={createElement:t=>new Element(t),querySelector:s=>s==='.map-panel'?mapPanel:chatPanel};
  const map={removeLayer:()=>removed++,closePopup:()=>{},fitBounds:()=>{},on:()=>{}};
  const L={Layer:{extend:()=>class{addTo(){added++;return this;}}}};
  const fetch=async url=>{
    requests.push(url);if(hold&&url.includes('/frame?')){const fn=hold;hold=null;return fn(url);}
    const p=new URL('http://local'+url).searchParams;
    return {ok:true,json:async()=>url.endsWith('/catalog')?{datasets:catalogue,default_dataset_id:l5}:{state:'ready',field:p.get('field'),dataset_id:p.get('dataset_id'),valid_time:p.get('valid_time'),bounds:[118,26,126,33],unit:'m/s',batch:a,stats:{maximum:{value:8},minimum:{value:1},valid_count:4}}};
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
function assistantHarness(apply,applyGroup){
  const ids=Object.fromEntries(['sendBtn','chatMessages','chatInput','conversationStatus','debugContent'].map(k=>[k,new Element('div')]));
  let answer={answer:'已经核对的完整回答',sources:[],map_action:action()},reset=0,asked=0;
  class Client{async ask(){asked++;return answer;}reset(){reset++;}}
  const document={getElementById:id=>ids[id],createElement:t=>new Element(t),createTextNode:t=>new Element('#text',t),querySelectorAll:()=>[]};
  const window={AssistantConversationClient:Client,ObjectivePanel:{applyAction:apply,applyActionGroup:applyGroup,cancelAction:()=>{reset++;}}};
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
  let grouped=0,legacy=0,deliveryTarget;
  const groups=assistantHarness(async()=>{legacy++;},async(actions,options)=>{grouped++;deliveryTarget=options.target;assert.equal(actions.length,2);return {state:'ready'};});
  groups.setAnswer({answer:'两个要素都有依据',sources:[],map_action:action('wave',a),map_actions:[action('wave',a),action('wind',b)]});
  await groups.api.send('风浪都显示');assert.equal(grouped,1);assert.equal(legacy,0,'the grouped contract takes precedence over compatibility primary action');
  assert.ok(deliveryTarget&&deliveryTarget.className==='msg-bubble','layer selector is attached to the same answer');
  assert.equal(groups.asked(),1,'field switching delivery does not submit another question');
  const retired=groups.resetCount();groups.setAnswer({answer:'普通知识答复',sources:[]});await groups.api.send('什么是海浪');
  assert.ok(groups.resetCount()>retired,'a non-map answer also retires prior answer-layer controls');
  console.log('PASS: map errors preserve full answers; reset cancels old map delivery without duplicated model calls');
}
function actionGroup(role='peak_of_this_field'){
  return [action('wave',a),action('wind',role==='shared'?a:b)].map((row,i)=>({...row,title:i?'海面风':'海浪',group_id:'oa_test_001',group_index:i,group_size:2,time_role:role,scope_note:'嵊泗未登记参考框；以下为舟山近海结果。'}));
}
async function testGroups(){
  const h=objectiveHarness();await h.api.open();
  const answer=new Element('div');await h.api.applyActionGroup(actionGroup(),{target:answer});
  const choices=walk(answer).filter(n=>n.tag==='button');assert.equal(choices.length,2);assert.equal(choices[0]['aria-pressed'],'true');
  let p=new URL('http://local'+h.requests.findLast(u=>u.includes('/frame?'))).searchParams;
  assert.equal(p.get('field'),'wave');assert.equal(p.get('valid_time'),a);assert.equal(p.get('sea_mask'),'wave_valid');
  assert.ok(walk(h.panel).some(n=>n.id==='objectiveScopeNotice'&&!n.hidden&&n.textContent.includes('嵊泗')));
  assert.ok(walk(h.panel).some(n=>n.textContent.includes('海浪 · 2026-10-20 20:00（本要素峰值时刻）')));
  await choices[1].onclick();p=new URL('http://local'+h.requests.findLast(u=>u.includes('/frame?'))).searchParams;
  assert.equal(p.get('field'),'wind');assert.equal(p.get('valid_time'),b,'wind switches to its own report peak, never the wave peak');
  assert.equal(choices[1]['aria-pressed'],'true');
  const field=walk(h.panel).filter(n=>n.tag==='select')[1];field.value='wave';await field.listeners.change();
  p=new URL('http://local'+h.requests.findLast(u=>u.includes('/frame?'))).searchParams;assert.equal(p.get('valid_time'),a,'panel dropdown follows grouped field time too');
  const before=h.requests.length;
  const invalid=[];
  for(const changed of [{bounds:[120,27,123,32]},{url:'https://untrusted.invalid'},{dataset_id:ec},{group_index:0},{group_size:3},{time_role:'bad'},{scope_note:'other'},{field:'wave'}]){const g=actionGroup();g[1]={...g[1],...changed};invalid.push(g);}
  const shared=actionGroup('shared');shared[1].valid_time=b;invalid.push(shared);
  for(const group of invalid)await assert.rejects(()=>h.api.applyActionGroup(group));
  assert.equal(h.requests.length,before,'invalid groups cannot trigger partial or substitute map requests');
  await h.api.applyActionGroup(actionGroup('shared'));const after=h.requests.length;
  assert.equal(choices[0].disabled,true);assert.equal((await choices[1].onclick()).state,'cancelled');assert.equal(h.requests.length,after,'old answer controls cannot restore superseded layers');
  const missing=actionGroup();missing[1]={...missing[1],field:'pressure',field_state:'not_provided'};const missingAnswer=new Element('div');
  await h.api.applyActionGroup(missing,{target:missingAnswer});const frameCount=h.requests.filter(u=>u.includes('/frame?')).length;
  const result=await walk(missingAnswer).filter(n=>n.tag==='button')[1].onclick();assert.equal(result.state,'no_data');
  assert.equal(h.requests.filter(u=>u.includes('/frame?')).length,frameCount);assert.ok(walk(h.panel).some(n=>n.textContent.includes('未提供可用气压')));
  const image=walk(h.panel).find(n=>n.tag==='a'&&n.textContent==='下载当前图件 PNG');assert.equal(image.hidden,true);
  const maximum=actionGroup('shared').map(row=>{const copy={...row,type:'objective_maximum',start:a,hours:72};delete copy.valid_time;return copy;});
  const maxAnswer=new Element('div');await h.api.applyActionGroup(maximum,{target:maxAnswer});await walk(maxAnswer).filter(n=>n.tag==='button')[1].onclick();
  p=new URL('http://local'+h.requests.findLast(u=>u.includes('/maximum/frame?'))).searchParams;assert.equal(p.get('field'),'wind');assert.equal(p.get('start'),a);assert.equal(p.get('hours'),'72');assert.equal(p.get('valid_time'),null);
  const beforeClear=h.requests.length;const clear=await h.api.applyAction({type:'objective_clear',reason:'厦门不在当前浙江计算范围内，请另选地点。'});
  assert.equal(clear.state,'cleared');assert.equal(h.requests.length,beforeClear,'scope clarification clears without catalog or frame requests');assert.equal(image.hidden,true);
  assert.ok(walk(h.panel).some(n=>n.textContent.includes('厦门不在当前浙江')));
  await assert.rejects(()=>h.api.applyAction({type:'objective_clear',reason:'clear',dataset_id:l5}));
  let finish;
  h.hold(()=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({state:'ready',field:'wave',dataset_id:l5,valid_time:a,bounds:[120,27,124,32],unit:'m',stats:{}})});}));
  const oldGroup=h.api.applyActionGroup(actionGroup(),{target:new Element('div')});await flush();assert.equal(typeof finish,'function');
  await h.api.applyActionGroup(actionGroup('shared'));const added=h.added();finish();
  assert.equal((await oldGroup).state,'cancelled');assert.equal(h.added(),added,'late response from a superseded group cannot repaint the map');
  console.log('PASS: grouped wave/wind selectors, per-field peak times, atomic validation, unavailable-field clear, stale controls, scope notices and explicit clear');
}
async function testPeakWindow(){
  const windowStart='2026-10-22T08:00:00+08:00',wavePeak='2026-10-24T05:00:00+08:00',windPeak='2026-10-23T17:00:00+08:00';
  const catalogue=[{...rows[1],fields:{wave:{valid_times:[windowStart,wavePeak]},wind:{valid_times:[windowStart,windPeak]}}}];
  const h=objectiveHarness(catalogue);await h.api.open();
  const group=actionGroup().map((row,i)=>({...row,valid_time:i?windPeak:wavePeak,window_start:windowStart,window_hours:72}));
  const answer=new Element('div');await h.api.applyActionGroup(group,{target:answer});
  const start=walk(h.panel).find(n=>n.type==='datetime-local'),selects=walk(h.panel).filter(n=>n.tag==='select'),duration=selects[4];
  const query=path=>new URL('http://local'+h.requests.findLast(u=>u.includes(path))).searchParams;
  assert.equal(start.value,'2026-10-22T08:00');assert.equal(duration.value,'72');
  assert.equal(query('/frame?').get('valid_time'),wavePeak,'wave map uses wave peak snapshot');
  assert.equal(query('/availability?').get('start'),windowStart,'coverage uses original requested window, not wave peak');
  assert.equal(query('/availability?').get('hours'),'72');
  await walk(answer).filter(n=>n.tag==='button')[1].onclick();
  assert.equal(query('/frame?').get('valid_time'),windPeak,'wind map uses its own peak snapshot');
  assert.equal(start.value,'2026-10-22T08:00');assert.equal(duration.value,'72');
  assert.equal(query('/availability?').get('start'),windowStart,'switching field cannot move the report window');
  assert.equal(query('/availability?').get('hours'),'72');
  await h.api.buildReport();assert.equal(query('/report?').get('start'),windowStart);assert.equal(query('/report?').get('hours'),'72');
  const before=h.requests.length;
  for(const damage of [{window_hours:48},{window_hours:169},{window_hours:0},{window_hours:1.5},{window_hours:'72'},
    {window_start:'2026-10-22T08:00:00'},{window_start:'2026-02-30T08:00:00+08:00'},
    {window_start:undefined},{window_hours:undefined},{valid_time:'2026-10-22T07:00:00+08:00'}]){
    const invalid=group.map(row=>({...row}));invalid[1]={...invalid[1],...damage};await assert.rejects(()=>h.api.applyActionGroup(invalid));
  }
  for(const damage of [{time_role:'shared'},{type:'objective_maximum',start:windowStart,hours:72,valid_time:undefined}])
    await assert.rejects(()=>h.api.applyAction({...group[0],...damage}));
  assert.equal(h.requests.length,before,'invalid or inconsistent report windows are rejected before fetching');
  const instant={...action('wave',windowStart)};await h.api.applyAction(instant);
  assert.equal(start.value,'2026-10-22T08:00');assert.equal(query('/frame?').get('valid_time'),windowStart,'ordinary snapshot without window metadata remains compatible');
  console.log('PASS: independent peak snapshots preserve the original 72-hour report/coverage window and reject malformed or mixed windows');
}
(async()=>{await testObjective();await testGroups();await testPeakWindow();await testAssistant();})().catch(error=>{console.error(error);process.exitCode=1;});
