/* All objective fields and report downloads share the existing map/chat page. */
(() => {
  'use strict';
  const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const panel=make('section');panel.id='objectivePanel';panel.className='objective-panel';panel.hidden=true;
  const title=make('div');title.className='objective-title';title.append(make('h2','客观预报 · 图与报告'));
  const close=make('button','×');close.setAttribute('aria-label','关闭客观预报面板');close.onclick=()=>window.ObjectivePanel.close();title.append(close);panel.append(title);
  const sourceLabel=make('p','原始预报网格 · 临时解释 · 时间统一为北京时间');panel.append(sourceLabel);
  const availability=make('p');availability.id='objectiveAvailability';panel.append(availability);
  function choose(label,entries){const wrap=make('label',label),node=make('select');entries.forEach(([value,text])=>{const option=make('option',text);option.value=value;node.append(option);});wrap.append(node);panel.append(wrap);return node;}
  const dataset=choose('预报批次',[]),field=choose('地图要素',[['wave','有效波高 · m'],['wind','10米风 · m/s'],['pressure','海平面气压 · hPa']]);
  const area=choose('统计与显示范围',[['119.5,26.5,123.5,31.5','浙江近海参考统计框'],['115,15,145,36','台风与东海大范围（框选统计）']]);
  const view=choose('图件类型',[['instant','指定时刻分布图'],['maximum','时段逐格最大值图']]);
  const slider=make('input');slider.type='range';slider.min='0';slider.max='0';slider.value='0';slider.setAttribute('aria-label','预报有效时刻');panel.append(slider);
  const timeLabel=make('p','正在读取批次…');timeLabel.id='objectiveValidTime';panel.append(timeLabel);
  const nav=make('div');nav.className='objective-actions';const prev=make('button','上一时次'),next=make('button','下一时次'),show=make('button','显示预报图');nav.append(prev,next,show);panel.append(nav);
  const stats=make('div');stats.className='objective-stats';stats.id='objectiveStats';panel.append(stats);
  const legend=make('div');legend.className='objective-gradient';panel.append(legend);
  const legendText=make('p');legendText.className='objective-legend-label';panel.append(legendText);
  const image=make('a','下载当前图件 PNG');image.target='_blank';image.rel='noopener noreferrer';image.hidden=true;panel.append(image);
  const startWrap=make('label','报告起始时刻（北京时间）'),start=make('input');start.type='datetime-local';start.setAttribute('aria-label','报告起始时刻');startWrap.append(start);panel.append(startWrap);
  const duration=choose('统计时长',[['72','72小时'],['24','24小时'],['48','48小时']]);
  const screening=make('details');screening.append(make('summary','可选：自定风浪筛查值'));
  const thresholdInputs={};for(const [name,label] of [['wave','浪高筛查值（m）'],['wind','风速筛查值（m/s，临时口径）']]){const wrap=make('label',label),input=make('input');input.type='number';input.min='0';input.step='0.1';input.setAttribute('aria-label',label);thresholdInputs[name]=input;wrap.append(input);screening.append(wrap);}screening.append(make('p','留空不设置；此处为用户指定筛查值，不是正式预警等级。'));panel.append(screening);
  const coverageCard=make('div');coverageCard.className='objective-stats';coverageCard.id='objectiveCoverage';panel.append(coverageCard);
  const actions=make('div');actions.className='objective-actions';const report=make('button','生成所选范围报告'),ask=make('button','在对话中解读');actions.append(report,ask);panel.append(actions);
  const reportResult=make('div');reportResult.id='objectiveReportResult';panel.append(reportResult);
  const status=make('p');status.id='objectiveStatus';status.setAttribute('role','status');panel.append(status);
  document.querySelector('.map-panel').append(panel);
  const shortcuts=make('div');shortcuts.className='objective-quick-actions';
  const knowledge=make('button','预警知识'),similar=make('button','相似台风'),products=make('button','预报产品');
  knowledge.onclick=()=>window.MapAssistant.send('海浪预警和风暴潮预警分别是什么意思，预警等级与应急响应有什么区别？');
  similar.onclick=async()=>{setMode('typhoon');await window.loadLiveTyphoon();await matchConfiguredTyphoon();};
  products.onclick=()=>setMode('objective');shortcuts.append(knowledge,similar,products);
  document.querySelector('.chat-panel').prepend(shortcuts);
  let datasets=[],times=[],frame=null,layer=null,sequence=0,reportSequence=0,actionEpoch=0,catalogRequest=null,defaultDatasetId='',suppressOpenLoad=false,coverageSequence=0;
  let scopes=[{id:'zhejiang',label:'浙江近海参考统计框',bounds:[119.5,26.5,123.5,31.5]}];
  const palettes={wave:{min:0,max:8,label:'有效波高 0—8 m'},wind:{min:0,max:40,label:'10米风速 0—40 m/s · 箭头表示风吹向'},pressure:{min:960,max:1040,label:'海平面气压 960—1040 hPa · 白线间隔4 hPa'}};
  const stops=[[35,54,145],[35,149,200],[43,201,171],[246,216,71],[235,92,51],[156,33,64]];
  function color(value,scale,colors=stops){const x=Math.max(0,Math.min(1,(value-scale.min)/(scale.max-scale.min)))*(colors.length-1),i=Math.min(colors.length-2,Math.floor(x)),f=x-i;return colors[i].map((c,j)=>Math.round(c+(colors[i+1][j]-c)*f));}
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const stamp=value=>value?String(value).slice(0,16).replace('T',' '):'未提供';
  const localInput=value=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(value||new Date()).replace(' ','T');
  async function get(path){const r=await fetch(path,{cache:'no-store'}),data=await r.json();if(!r.ok)throw Error(data.error||'查询失败');return data;}
  function params(){const p=new URLSearchParams({dataset_id:dataset.value,field:field.value,valid_time:times[Number(slider.value)]||'',bounds:area.value,maxsize:'180'});if(field.value==='wind'&&datasets.find(d=>d.dataset_id===dataset.value)?.fields?.wave)p.set('sea_mask','wave_valid');return p;}
  const currentDataset=()=>datasets.find(d=>d.dataset_id===dataset.value);
  const currentScope=()=>scopes.find(s=>s.bounds.join(',')===area.value);
  function periodParams(){const p=new URLSearchParams({dataset_id:dataset.value,field:field.value,start:start.value+':00+08:00',hours:duration.value||'72',bounds:area.value,maxsize:'180'});if(currentDataset()?.fields?.wave)p.set('sea_mask','wave_valid');return p;}
  async function readCoverage(){
    const ticket=++coverageSequence;if(!dataset.value||!start.value)return;
    coverageCard.replaceChildren(make('p','正在核对所选范围和原生时次…'));
    try{const p=periodParams();p.set('fields',Object.keys(currentDataset()?.fields||{}).filter(k=>['wave','wind'].includes(k)).join(',')||field.value);const data=await get('/api/objective/availability?'+p);if(ticket!==coverageSequence)return;
      coverageCard.replaceChildren(make('p','批次 '+stamp(data.batch)+' · '+(currentScope()?.label||'地图选定参考框')));
      for(const [name,item] of Object.entries(data.fields||{})){const t=item.temporal_coverage||{};coverageCard.append(make('p',({wave:'海浪',wind:'海面风',pressure:'气压'}[name]||name)+'：原生时次 '+(t.available_count??'—')+'/'+(t.expected_count??'—')+'；空间'+(item.spatial_complete?'覆盖完整':'覆盖不足')+(t.missing_times?.length?'；缺 '+t.missing_times.length+' 时次':'')));}
      coverageCard.append(make('p',data.complete?'所问时空覆盖完整；数值缺测以计算结果为准。':'所问覆盖不足或批次尚未起报，仍可查看实际已取得部分。'));
    }catch(error){if(ticket===coverageSequence)coverageCard.replaceChildren(make('p','覆盖核查未完成：'+error.message));}
  }
  const l2l5=d=>d?.source_family==='zjocean_l2_l5'||d?.profile?.id==='zjocean-l2-l5-provisional-v1'||/^zjocean_/.test(d?.dataset_id||'');
  function fieldLabel(value,d=currentDataset()){
    return {wave:l2l5(d)?'WAV波高 · m（临时解释）':'有效波高 · m',wind:l2l5(d)?'u10/v10合成 · m/s（临时阵风口径）':'10米风 · m/s',pressure:'海平面气压 · hPa'}[value];
  }
  function syncFields(preferred=field.value){
    const current=currentDataset(),keys=['wave','wind','pressure'].filter(k=>Object.hasOwn(current?.fields||{},k));field.replaceChildren();
    keys.forEach(k=>{const option=make('option',fieldLabel(k,current));option.value=k;field.append(option);});
    field.value=keys.includes(preferred)?preferred:(keys[0]||'');
    sourceLabel.textContent=(l2l5(current)?'浙江海洋'+(current.level||'L2/L5')+'网格 · u10/v10暂按阵风口径':'ECMWF原始网格')+' · 临时解释 · 北京时间';
    availability.textContent=current&&!keys.includes('pressure')?'该批次提供'+keys.map(k=>({wave:'海浪',wind:'海面风'}[k])).join('、')+'；暂缺气压网格。':'';
  }
  function availableTimes(preferred){const previous=preferred||times[Number(slider.value)],value=currentDataset()?.fields?.[field.value];times=value?.valid_times||[];slider.max=String(Math.max(0,times.length-1));let nearest=previous?times.findIndex(t=>Date.parse(t)===Date.parse(previous)):-1;if(nearest<0){const target=previous?Date.parse(previous):Date.now();nearest=times.findIndex(t=>Date.parse(t)>=target);}if(nearest<0)nearest=times.length-1;slider.value=String(Math.max(0,nearest));timeLabel.textContent='有效时刻：'+stamp(times[Number(slider.value)])+'（北京时间）';}
  async function readCatalogue(refresh=false){
    if(!catalogRequest||refresh)catalogRequest=get('/api/objective/catalog').finally(()=>{catalogRequest=null;});
    const data=await catalogRequest;defaultDatasetId=typeof data.default_dataset_id==='string'?data.default_dataset_id:'';
    if(Array.isArray(data.report_scopes)){scopes=data.report_scopes.filter(s=>typeof s.id==='string'&&typeof s.label==='string'&&Array.isArray(s.bounds)&&s.bounds.length===4&&s.bounds.every(finite));for(const s of scopes){if(!Array.from(area.children).some(n=>n.value===s.bounds.join(','))){const option=make('option',s.label);option.value=s.bounds.join(',');area.append(option);}}}
    return Array.isArray(data.datasets)?data.datasets:[];
  }
  function setCatalogue(rows,preferred=dataset.value){
    datasets=rows;dataset.replaceChildren();
    datasets.forEach(d=>{const n=make('option',(l2l5(d)?(d.level||'L2/L5')+' ':'ECMWF ')+stamp(d.batch)+' · '+Object.keys(d.fields||{}).map(k=>({wave:'浪',wind:'风',pressure:'压'}[k]||k)).join('/'));n.value=d.dataset_id;dataset.append(n);});
    dataset.value=datasets.some(d=>d.dataset_id===preferred)?preferred:(datasets[0]?.dataset_id||'');syncFields();availableTimes();
  }
  async function initialize(){
    const epoch=actionEpoch,rows=await readCatalogue();if(epoch!==actionEpoch||panel.hidden)return;
    const l5=rows.filter(d=>l2l5(d)&&(d.level==='L5'||/_L5(?:_|$)/i.test(d.dataset_id))&&d.fields?.wave&&d.fields?.wind).sort((a,b)=>Date.parse(b.batch)-Date.parse(a.batch))[0];
    setCatalogue(rows,defaultDatasetId||l5?.dataset_id);if(!datasets.length){clearFrame();status.textContent='尚未导入客观预报NC。';return;}
    if(!start.value)start.value=localInput();await readCoverage();await load();
  }
  function clearFrame(){
    if(layer){map.removeLayer(layer);layer=null;}map.closePopup();frame=null;image.hidden=true;stats.replaceChildren();legend.style.background='';legendText.textContent='';
  }
  function cancelAction(clear=true){actionEpoch++;sequence++;show.disabled=false;if(clear)clearFrame();}
  function contours(ctx,data,project){
    const z=data.values,xs=data.lon,ys=data.lat;
    ctx.strokeStyle='rgba(255,255,255,.78)';ctx.lineWidth=1;ctx.font='11px sans-serif';ctx.fillStyle='#fff';
    for(let level=960;level<=1044;level+=4){let labelled=false;
      for(let y=0;y<ys.length-1;y++)for(let x=0;x<xs.length-1;x++){
        const values=[z[y][x],z[y][x+1],z[y+1][x+1],z[y+1][x]],xy=[[xs[x],ys[y]],[xs[x+1],ys[y]],[xs[x+1],ys[y+1]],[xs[x],ys[y+1]]];
        if(!values.every(finite))continue;const points=[];
        for(let edge=0;edge<4;edge++){const end=(edge+1)%4,a=values[edge],b=values[end];if((a<level&&b>=level)||(b<level&&a>=level)){const f=(level-a)/(b-a);points.push(project(xy[edge][1]+f*(xy[end][1]-xy[edge][1]),xy[edge][0]+f*(xy[end][0]-xy[edge][0])));}}
        for(let k=0;k+1<points.length;k+=2){ctx.beginPath();ctx.moveTo(points[k].x,points[k].y);ctx.lineTo(points[k+1].x,points[k+1].y);ctx.stroke();if(!labelled&&points[k].x>30&&points[k].y>30){ctx.fillText(String(level),points[k].x+2,points[k].y-2);labelled=true;}}
      }
    }
  }
  function draw(canvas,data){
    const size=map.getSize(),ctx=canvas.getContext('2d'),ratio=window.devicePixelRatio||1;
    canvas.width=size.x*ratio;canvas.height=size.y*ratio;canvas.style.width=size.x+'px';canvas.style.height=size.y+'px';canvas.style.opacity='.78';ctx.scale(ratio,ratio);
    const project=(lat,lon)=>map.latLngToContainerPoint([lat,lon]),xs=data.lon,ys=data.lat,z=data.values,scale={min:data.color_scale?.minimum??palettes[data.field].min,max:data.color_scale?.maximum??palettes[data.field].max},colors=data.color_scale?.colors||stops;
    const dx=xs.length>1?Math.abs(xs[1]-xs[0])/2:.05,dy=ys.length>1?Math.abs(ys[1]-ys[0])/2:.05;
    ctx.globalAlpha=1;
    for(let y=0;y<ys.length;y++)for(let x=0;x<xs.length;x++){
      if(!finite(z[y][x]))continue;const a=project(ys[y]+dy,xs[x]-dx),b=project(ys[y]-dy,xs[x]+dx);
      if(b.x<0||a.x>size.x||b.y<0||a.y>size.y)continue;
      ctx.fillStyle='rgb('+color(z[y][x],scale,colors).join(',')+')';ctx.fillRect(a.x,a.y,Math.max(1,b.x-a.x+.5),Math.max(1,b.y-a.y+.5));
    }
    ctx.globalAlpha=1;
    if(data.field==='pressure')contours(ctx,data,project);
    if(data.field==='wind'&&data.u&&data.v){
      const step=Math.max(1,Math.ceil(Math.max(xs.length,ys.length)/17));ctx.strokeStyle='rgba(255,255,255,.95)';ctx.lineWidth=1.3;
      for(let y=0;y<ys.length;y+=step)for(let x=0;x<xs.length;x+=step){
        const u=data.u[y][x],v=data.v[y][x];if(!finite(u)||!finite(v)||Math.hypot(u,v)<.2)continue;
        const p=project(ys[y],xs[x]),angle=Math.atan2(-v,u),length=13+Math.min(10,Math.hypot(u,v)/2),ex=p.x+Math.cos(angle)*length/2,ey=p.y+Math.sin(angle)*length/2;
        ctx.beginPath();ctx.moveTo(p.x-Math.cos(angle)*length/2,p.y-Math.sin(angle)*length/2);ctx.lineTo(ex,ey);ctx.lineTo(ex-5*Math.cos(angle-.55),ey-5*Math.sin(angle-.55));ctx.moveTo(ex,ey);ctx.lineTo(ex-5*Math.cos(angle+.55),ey-5*Math.sin(angle+.55));ctx.stroke();
      }
    }
  }
  function renderLayer(data){
    if(layer)map.removeLayer(layer);
    const FieldLayer=L.Layer.extend({onAdd(){this.canvas=make('canvas');this.canvas.className='objective-map-canvas';map.getContainer().append(this.canvas);map.on('moveend zoomend resize',this.paint,this);this.paint();},paint(){draw(this.canvas,data);},onRemove(){map.off('moveend zoomend resize',this.paint,this);this.canvas.remove();}});
    layer=new FieldLayer();layer.addTo(map);
  }
  async function load(options={}){
    const ticket=++sequence,allowed=()=>ticket===sequence&&!panel.hidden&&(!options.isCurrent||options.isCurrent());clearFrame();
    if(!dataset.value||!times.length){status.textContent='所选要素没有可用预报时次。';return {state:'no_data'};}
    show.disabled=true;status.textContent='正在读取区域网格…';
    const isMaximum=view.value==='maximum';slider.disabled=isMaximum;prev.disabled=isMaximum;next.disabled=isMaximum;
    timeLabel.textContent=isMaximum?'时段逐格最大值：从'+stamp(start.value)+'起 '+duration.value+'小时，各格点峰值时刻可能不同。':'有效时刻：'+stamp(times[Number(slider.value)])+'（北京时间）';
    try{const data=await get(isMaximum?'/api/objective/maximum/frame?'+periodParams():'/api/objective/frame?'+params());if(!allowed())return {state:'cancelled'};frame=data;
      if(data.state!=='ready'){frame=null;status.textContent=data.reason||'所选时刻无有效网格。';return {state:'no_data',message:status.textContent};}
      renderLayer(data);const b=data.bounds;map.fitBounds([[b[1],b[0]],[b[3],b[2]]],{padding:[12,12]});
      stats.replaceChildren();const s=data.stats||{},value=v=>finite(v)?v.toFixed(2):'缺测';
      const maximum=typeof s.maximum==='object'?s.maximum?.value:s.maximum,minimum=typeof s.minimum==='object'?s.minimum?.value:s.minimum;
      stats.append(make('p','区域范围 '+b.join('，')),make('p','最大 '+value(maximum)+' '+data.unit+'；最小 '+value(minimum)+' '+data.unit),make('p','有效格点 '+(s.valid_count??'—')+' · 批次 '+stamp(data.batch)));
      const scale=palettes[field.value];legend.style.background='linear-gradient(90deg,'+(data.color_scale?.colors||stops).map(c=>'rgb('+c.join(',')+')').join(',')+')';legendText.textContent=isMaximum?(fieldLabel(field.value)+' · 时段逐格最大值；非单一时刻，不叠加风向箭头'):field.value==='wind'&&l2l5(currentDataset())?'u10/v10合成风速 0—40 m/s · 临时阵风口径 · 箭头表示风吹向':scale.label;
      image.href=isMaximum?'/api/objective/maximum/image.png?'+periodParams():'/api/objective/image.png?'+params();image.hidden=false;status.textContent=isMaximum?'时段最大值图就绪；切回指定时刻可查询原生格点。':'图层就绪，点击地图可查询该格点。';return {state:'ready',dataset_id:dataset.value,field:field.value,valid_time:data.valid_time,product_type:isMaximum?'temporal_maximum':'instant'};
    }catch(e){if(allowed())status.textContent=e.message;return {state:allowed()?'error':'cancelled',message:e.message};}finally{if(ticket===sequence)show.disabled=false;}
  }
  async function buildReport(){
    const ticket=++reportSequence;report.disabled=true;reportResult.replaceChildren(make('p','正在统计所选范围风浪过程、绘图并检查可复用报告…'));
    try{
      if(!dataset.value)throw Error('请先选择已导入批次。');
      const p=periodParams();const scope=currentScope();if(scope)p.set('scope',scope.id);p.set('fields',Object.keys(currentDataset()?.fields||{}).filter(k=>['wave','wind'].includes(k)).join(',')||field.value);
      for(const name of ['wave','wind'])if(thresholdInputs[name].value!=='')p.set(name+'_threshold',thresholdInputs[name].value);
      const data=await get('/api/objective/report?'+p);
      if(ticket!==reportSequence)return;reportResult.replaceChildren();
      if(data.state!=='ready')reportResult.append(make('p','本次覆盖不完整，请查看报告中的具体缺口。'));
      if(data.summary)window.MapAssistant.renderAnswer(reportResult,data.summary);
      let artifacts=data.artifacts||[];if(!Array.isArray(artifacts))artifacts=Object.values(artifacts);
      artifacts.forEach(a=>{if(!/^\/api\/objective\/reports\/[a-f0-9]+\/[A-Za-z0-9_.-]+$/.test(a.url||''))return;const link=make('a',({'report.html':'打开完整报告','report.json':'下载数据JSON','series.csv':'下载时序CSV'}[a.filename]||a.filename));link.href=a.url;link.target='_blank';link.rel='noopener noreferrer';reportResult.append(link);});
      status.textContent=(data.cache_hit?'已复用同批次、同范围和时段的报告':'报告已生成')+' · 图、表、文字均来自所选批次。';
    }catch(e){if(ticket===reportSequence)reportResult.replaceChildren(make('p',e.message));}finally{if(ticket===reportSequence)report.disabled=false;}
  }
  function validAction(action){
    const keys=['type','dataset_id','field','valid_time','start','hours','bounds','title','source_family','available_fields','field_state','state','level'];
    if(!action||typeof action!=='object'||Array.isArray(action)||Object.keys(action).some(k=>!keys.includes(k)))throw Error('地图动作包含不支持的字段。');
    if(!['objective_field','objective_maximum'].includes(action.type)||typeof action.dataset_id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(action.dataset_id)||!Object.hasOwn(palettes,action.field))throw Error('地图动作的数据集或要素无效。');
    const period=action.type==='objective_maximum',when=period?action.start:action.valid_time;
    if(period&&(!Number.isInteger(action.hours)||action.hours<1||action.hours>168||action.valid_time!==undefined))throw Error('时段最大值地图需要1至168小时，不能带单时刻参数。');
    if(!period&&(action.start!==undefined||action.hours!==undefined))throw Error('单时刻地图不能带时段最大值参数。');
    const iso=typeof when==='string'&&when.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(Z|[+-](\d{2}):(\d{2}))$/);
    if(!iso||!Number.isFinite(Date.parse(when)))throw Error('地图动作缺少明确时区的有效时刻。');
    const [year,month,day,hour,minute,second]=iso.slice(1,7).map(v=>Number(v||0)),date=new Date(Date.UTC(year,month-1,day));
    if(year<1900||year>2200||month<1||month>12||date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day||hour>23||minute>59||second>59||Number(iso[8]||0)>14||Number(iso[9]||0)>59||(Number(iso[8]||0)===14&&Number(iso[9]||0)!==0))throw Error('地图动作的日期时刻无效。');
    if(action.bounds!==undefined){const b=action.bounds;if(!Array.isArray(b)||b.length!==4||!b.every(finite)||b[0]<-180||b[2]>180||b[1]<-90||b[3]>90||b[0]>=b[2]||b[1]>=b[3])throw Error('地图动作的经纬度范围无效。');}
    for(const key of ['title','source_family','field_state','state','level'])if(action[key]!==undefined&&(typeof action[key]!=='string'||action[key].length>160))throw Error('地图动作的说明字段无效。');
    if(action.available_fields!==undefined&&(!Array.isArray(action.available_fields)||action.available_fields.length>3||action.available_fields.some(k=>!Object.hasOwn(palettes,k))))throw Error('地图动作的要素列表无效。');
    return action;
  }
  async function applyAction(raw,options={}){
    const action=validAction(raw),epoch=++actionEpoch,current=()=>epoch===actionEpoch&&(!options.isCurrent||options.isCurrent());
    const period=action.type==='objective_maximum';view.value=period?'maximum':'instant';
    sequence++;clearFrame();show.disabled=false;
    if(!current())return {state:'cancelled'};
    suppressOpenLoad=true;try{setMode('objective');}finally{suppressOpenLoad=false;}
    panel.hidden=false;status.textContent='正在按本次回答更新地图…';
    try{
      const rows=await readCatalogue();if(!current()||panel.hidden)return {state:'cancelled'};
      const selected=rows.find(d=>d.dataset_id===action.dataset_id);setCatalogue(rows,action.dataset_id);
      if(!selected){clearFrame();status.textContent='本次回答的数据批次未登记，地图未显示其他批次。';return {state:'no_data',message:status.textContent};}
      syncFields(action.field);
      if(action.bounds){const value=action.bounds.join(',');let option=Array.from(area.children).find(n=>n.value===value);if(!option){option=make('option','本次问题指定范围');option.value=value;area.append(option);}area.value=value;}
      const state=action.field_state||action.state;
      if(!Object.hasOwn(selected.fields||{},action.field)||['not_provided','no_data','unavailable','outside_coverage'].includes(state)){
        field.value='';times=[];slider.max='0';slider.value='0';timeLabel.textContent='所问起始时刻：'+stamp(action.start||action.valid_time)+'（北京时间）';clearFrame();
        status.textContent=action.field==='pressure'?'该批次未提供可用气压网格，旧图已清除；没有拼接其他批次。':'本次问题所需的'+({wave:'海浪',wind:'海面风'}[action.field]||'要素')+'网格不可用，旧图已清除。';return {state:'no_data',message:status.textContent};
      }
      const when=period?action.start:action.valid_time;
      const index=(selected.fields[action.field].valid_times||[]).findIndex(t=>Date.parse(t)===Date.parse(when));
      if(!period&&index<0){times=[];slider.max='0';slider.value='0';clearFrame();timeLabel.textContent='所问时刻：'+stamp(when)+'（北京时间）';status.textContent='该批次没有所问时刻的原生网格，地图未替换为其他时刻。';return {state:'no_data',message:status.textContent};}
      availableTimes(when);start.value=localInput(new Date(when));if(period){if(!Array.from(duration.children).some(n=>n.value===String(action.hours))){const option=make('option',action.hours+'小时');option.value=String(action.hours);duration.append(option);}duration.value=String(action.hours);}readCoverage();
      const result=await load({isCurrent:current});if(current()&&result?.state==='ready'&&action.title)status.textContent=action.title+(period?' · 逐格时段最大值，各格点峰值时刻可不同。':' · 图层就绪，点击地图可查询格点。');return result;
    }catch(error){if(current()){clearFrame();status.textContent=error.message;}return {state:current()?'error':'cancelled',message:error.message};}
  }
  const manualLoad=()=>{cancelAction();return load();};
  dataset.addEventListener('change',()=>{cancelAction();syncFields();availableTimes();readCoverage();return load();});field.addEventListener('change',()=>{cancelAction();availableTimes();return load();});area.addEventListener('change',()=>{readCoverage();return manualLoad();});
  view.addEventListener('change',manualLoad);start.addEventListener('change',()=>{readCoverage();if(view.value==='maximum')return manualLoad();});duration.addEventListener('change',()=>{readCoverage();if(view.value==='maximum')return manualLoad();});
  slider.addEventListener('change',manualLoad);slider.addEventListener('input',()=>{cancelAction();timeLabel.textContent='有效时刻：'+stamp(times[Number(slider.value)])+'（北京时间）';});
  prev.onclick=()=>{slider.value=String(Math.max(0,Number(slider.value)-1));return manualLoad();};next.onclick=()=>{slider.value=String(Math.min(times.length-1,Number(slider.value)+1));return manualLoad();};show.onclick=manualLoad;report.onclick=buildReport;
  ask.onclick=()=>window.MapAssistant.send('从'+start.value.replace('T',' ')+'起，生成'+(currentScope()?.label||'浙江地图选定范围')+'未来'+duration.value+'小时'+Object.keys(currentDataset()?.fields||{}).map(k=>({wave:'海浪',wind:'海面风',pressure:'气压'}[k])).filter(Boolean).join('、')+'影响报告，使用数据集ID '+dataset.value+'，地图范围：'+area.value+'，说明重点海域及变化时段。');
  let clickInstalled=false;
  window.ObjectivePanel={open:async()=>{panel.hidden=false;if(!clickInstalled){map.on('click',async e=>{if(panel.hidden||!frame||view.value==='maximum')return;const ticket=sequence,p=params();p.set('lon',e.latlng.lng);p.set('lat',e.latlng.lat);try{const data=await get('/api/objective/point?'+p);if(ticket!==sequence||panel.hidden||!frame)return;const content=make('div');content.append(make('strong','客观预报格点'),make('p','北京时间 '+stamp(data.valid_time)),make('p','数值 '+(data.value??'缺测')+' '+(data.unit||'')),make('p','原生网格中心 '+data.grid_lon+'°E，'+data.grid_lat+'°N'));L.popup().setLatLng(e.latlng).setContent(content).openOn(map);}catch(e){if(ticket===sequence&&!panel.hidden)status.textContent=e.message;}});clickInstalled=true;}if(suppressOpenLoad)return;if(!datasets.length)await initialize();else if(frame)renderLayer(frame);else await load();},close:()=>{panel.hidden=true;cancelAction();},load,buildReport,applyAction,cancelAction};
})();
