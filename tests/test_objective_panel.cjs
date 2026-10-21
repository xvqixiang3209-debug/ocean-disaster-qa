// Public UI contract: one batch/time selection, safe downloads, no stale map.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.listeners={};this.style={};}
 append(...rows){rows.forEach(row=>{row.parentElement=this;this.children.push(row);if(this.tag==='select'&&!this.value)this.value=row.value;});}
 prepend(...rows){this.children.unshift(...rows);}
 replaceChildren(...rows){this.children=[];this.append(...rows);}
 setAttribute(name,value){this[name]=value;}
 addEventListener(name,handler){this.listeners[name]=handler;}
}
const mapPanel=new Element('section'),chatPanel=new Element('section'),document={createElement:tag=>new Element(tag),querySelector:s=>s==='.map-panel'?mapPanel:chatPanel};
const all=(node,tag)=>[...(node.tag===tag?[node]:[]),...node.children.flatMap(n=>all(n,tag))];
const a='2026-10-20T20:00:00+08:00',b='2026-10-21T02:00:00+08:00',id='ecmwf_202610192000_test';
let requests=[],removed=0,closed=0,deferred=null,failNext=false,latestLayer=null;
const map={removeLayer:()=>removed++,closePopup:()=>closed++,fitBounds:()=>{},on:()=>{}};
const L={Layer:{extend:()=>class {addTo(){latestLayer=this;return this;}}}};
const fetch=async url=>{
 requests.push(url);if(deferred&&url.includes('/frame?'))return deferred;
 if(failNext&&url.includes('/frame?')){failNext=false;throw Error('模拟不可读');}
 const parsed=new URL('http://localhost'+url),field=parsed.searchParams.get('field');
 let data=url.endsWith('/catalog')?{datasets:[{dataset_id:id,batch:a,fields:{wave:{valid_times:[a,b]},wind:{valid_times:[a,'2026-10-20T21:00:00+08:00',b]},pressure:{valid_times:[a,b]}}}],report_scopes:[{id:'zhujiajian',label:'朱家尖近海参考统计框',bounds:[122.33,29.82,122.53,30.02]}]}:
  url.includes('/availability?')?{batch:a,complete:true,fields:{wave:{spatial_complete:true,temporal_coverage:{available_count:73,expected_count:73,missing_times:[]}}}}:
  url.includes('/report?')?{state:'ready',cache_hit:true,summary:'真实样本报告',artifacts:{html:{filename:'report.html',url:'/api/objective/reports/'+'a'.repeat(32)+'/report.html'},bad:{filename:'bad',url:'javascript:bad()'}}}:
  {state:'ready',field,valid_time:parsed.searchParams.get('valid_time'),unit:field==='wave'?'m':'m/s',bounds:[118,26,126,33],batch:a,stats:{maximum:{value:3},minimum:{value:1},valid_count:25}};
 return {ok:true,json:async()=>data};
};
const window={MapAssistant:{send:()=>{},renderAnswer:(node,text)=>node.append(new Element('p',text))}};
vm.runInNewContext(fs.readFileSync('frontend/objective_panel.js','utf8'),{document,window,map,L,fetch,Intl,Date,URL,URLSearchParams,console,setMode:()=>{}});
const flush=()=>new Promise(setImmediate);
(async()=>{
 await window.ObjectivePanel.open();const panel=mapPanel.children[0];
 const [dataset,field]=all(panel,'select'),slider=all(panel,'input').find(n=>n.type==='range');
 slider.value='1';await slider.listeners.change();await flush();
 assert.ok(requests.at(-1).includes(encodeURIComponent(b)));
 field.value='wind';await field.listeners.change();await flush();
 assert.equal(slider.value,'2');assert.ok(requests.at(-1).includes('field=wind'));
 assert.ok(requests.at(-1).includes(encodeURIComponent(b)),'changing element must preserve matching valid time');
 assert.equal(dataset.value,id);assert.ok(latestLayer&&removed>=2&&closed>=3);
 await window.ObjectivePanel.buildReport();
 const links=all(panel,'a');assert.ok(links.some(n=>n.href?.endsWith('/report.html')));assert.ok(!links.some(n=>n.href?.startsWith('javascript:')));
 const reportLink=links.find(n=>n.href?.endsWith('/report.html'));assert.equal(reportLink.rel,'noopener noreferrer');
 const [, ,area,view,duration]=all(panel,'select');
 area.value='122.33,29.82,122.53,30.02';await area.listeners.change();await flush();
 const start=all(panel,'input').find(n=>n.type==='datetime-local');start.value='2026-10-21T20:00';duration.value='24';
 await window.ObjectivePanel.buildReport();
 const reportParams=new URL('http://local'+requests.findLast(u=>u.includes('/report?'))).searchParams;
 assert.equal(reportParams.get('scope'),'zhujiajian');assert.equal(reportParams.get('bounds'),area.value);assert.equal(reportParams.get('hours'),'24');
 assert.ok(all(panel,'p').some(n=>n.textContent.includes('已复用同批次')));
 view.value='maximum';await view.listeners.change();await flush();
 const maximumURL=requests.findLast(u=>u.includes('/maximum/frame?'));
 assert.ok(maximumURL);const maximumParams=new URL('http://local'+maximumURL).searchParams;
 assert.equal(maximumParams.get('start'),'2026-10-21T20:00:00+08:00');assert.equal(maximumParams.get('hours'),'24');assert.equal(maximumParams.get('bounds'),area.value);
 assert.equal(maximumParams.get('valid_time'),null,'period maxima must not borrow a snapshot timestamp');
 assert.ok(all(panel,'a').some(n=>n.href?.startsWith('/api/objective/maximum/image.png?')));
 assert.equal(slider.disabled,true);assert.ok(all(panel,'p').some(n=>n.textContent.includes('非单一时刻')));
 view.value='instant';await view.listeners.change();await flush();
 failNext=true;const oldRemoved=removed;await window.ObjectivePanel.load();
 assert.ok(removed>oldRemoved,'failed new query must remove old layer');
 assert.equal(links.find(n=>n.textContent==='下载当前图件 PNG').hidden,true);
 assert.ok(all(panel,'p').some(n=>n.textContent==='模拟不可读'));
 console.log('PASS: objective batch/time selection, field switch, safe report links, failed-load stale layer removal');
})().catch(error=>{console.error(error);process.exitCode=1;});
