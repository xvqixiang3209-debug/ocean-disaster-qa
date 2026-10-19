// DOM contracts only; no browser, remote service, or screenshots.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.listeners={};this.attrs={};}
  append(...items){items.forEach(item=>{item.parentElement=this;this.children.push(item);if(this.tag==='select'&&!this.value)this.value=item.value;});}
  replaceChildren(...items){this.children=[];this.append(...items);}
  addEventListener(name,handler){this.listeners[name]=handler;}
  setAttribute(name,value){this.attrs[name]=value;}
  click(){return this.listeners.click?.();}
}
const nodes={forecastToggle:new Element('button'),liveTyphoonBtn:new Element('button'),typhoonStatus:new Element('div'),typhoonResults:new Element('div'),typhoonJson:new Element('textarea')};
const container=new Element('div');
const document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),getElementById:id=>nodes[id],querySelector:()=>container};
let requested=[],answerQuestions=[],overlayFeatures;
const tide={trial:true,assumption_id:'trial',state:'ready',start:'2026-10-17T00:00:00+08:00',end:'2026-10-19T23:00:00+08:00',
  series:[{time:'2026-10-17T00:00:00+08:00',value_cm:123},{time:'2026-10-17T01:00:00+08:00',value_cm:145}],high_tides:[{time:'2026-10-17T00:30:00+08:00',kind:'high',value_cm:150}],low_tides:[],partial:true};
const coastal={...tide,requested_count:2,usable_count:1,missing_count:1,partial_count:0,results:[
  {name:'正常岸段',geometry:{type:'LineString',coordinates:[[121,28],[122,29]]},level:'red',state:'ready',maximum:{total_cm:400,time:tide.start}},
  {name:'<img onerror=bad>',geometry:{type:'LineString',coordinates:[[121,28],[122,29]]},level:null,state:'no_data',reason:'缺数据'}]};
const fetch=async url=>{requested.push(url);return{ok:true,json:async()=>url.endsWith('/status')?{import:{tide_hours:455520},grid_files:10,sync:{state:'ready'},assumptions:{notes:['测试假设']}}:
  url.endsWith('/sites')?{items:[{code:'KANMEN',name:'坎门',state:'ready'}]}:url.includes('/coastal?')?coastal:tide};};
const context=vm.createContext({document,fetch,Intl,Date,URLSearchParams,console,window:{MapAssistant:{send:q=>answerQuestions.push(q)}},map:{removeLayer:()=>{}},
  L:{geoJSON:(data,options)=>{overlayFeatures=data.features;overlayFeatures.forEach(f=>f.testColor=options.style(f).color);return{addTo:()=>({})};}}});
vm.runInContext(fs.readFileSync('frontend/forecast_panel.js','utf8'),context);
const panel=container.children[0];
function all(node,tag){return [...(node.tag===tag?[node]:[]),...node.children.flatMap(child=>all(child,tag))];}
(async()=>{
  nodes.forecastToggle.click();await new Promise(setImmediate);
  assert.equal(panel.hidden,false);
  const selects=all(panel,'select'),inputs=all(panel,'input'),buttons=all(panel,'button');
  const [mode,station,region,product,hours]=selects;
  assert.equal(station.value,'KANMEN');assert.equal(region.parentElement.hidden,true);
  hours.value='72';inputs.find(n=>n.type==='date').value='2026-10-17';
  const run=buttons.find(n=>n.textContent==='查询');await run.click();
  assert.ok(requested.some(u=>u.includes('hours=72')&&u.includes('station=KANMEN')));
  assert.equal(all(panel,'svg').length,1);assert.ok(all(panel,'td').some(n=>n.textContent==='150'));
  await buttons.find(n=>n.textContent==='在对话中解读').click();assert.ok(answerQuestions[0].includes('KANMEN'));
  mode.value='coastal';mode.listeners.change();assert.equal(region.parentElement.hidden,false);assert.equal(station.parentElement.hidden,true);
  await run.click();assert.equal(overlayFeatures.length,2);assert.equal(overlayFeatures[1].testColor,'#78828e');
  assert.equal(all(panel,'img').length,0);assert.ok(all(panel,'p').some(n=>n.textContent.includes('缺数据1')));
  assert.match(fs.readFileSync('frontend/forecast_panel.css','utf8'),/\.forecast-panel \[hidden\]/);
  console.log('PASS: forecast controls, full-duration query, tide curve, follow-up context, missing-data map color and safe text');
})().catch(error=>{console.error(error);process.exitCode=1;});
