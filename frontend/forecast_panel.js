/* Forecast queries share the map and the same conversational assistant. */
(() => {
  'use strict';
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node;};
  const $=id=>document.getElementById(id);
  const panel=make('section');panel.className='forecast-panel';panel.hidden=true;panel.id='forecastPanel';
  const close=make('button','×');close.className='forecast-close';close.setAttribute('aria-label','关闭潮汐与预报');
  close.addEventListener('click',()=>{panel.hidden=true;});panel.append(close,make('h2','潮汐与预报'));
  const label=make('p','试算版 · 使用假设口径，不代表正式预警');label.className='trial-label';panel.append(label);
  function field(title,element){const wrapper=make('label',title);wrapper.append(element);panel.append(wrapper);return element;}
  function select(options){const node=make('select');options.forEach(([value,text])=>{const option=make('option',text);option.value=value;node.append(option);});return node;}
  const mode=field('查询内容',select([['tide','天文潮与高低潮时刻'],['grid','海域网格预报'],['coastal','岸段超警试算']]));
  const station=field('潮位站',select([]));
  const region=field('岸段范围',select([['','浙江全部198岸段'],['嘉兴','嘉兴'],['宁波','宁波'],['台州','台州'],['温州','温州'],['舟山','舟山']]));
  const product=field('预报要素',select([['wave','浪高'],['wind','10米风速（阵风试算）'],['surge','SWL水位（假设纯增水）'],['temperature','海温'],['current','流速']]));
  const date=make('input');date.type='date';date.value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date());field('日期（北京时间）',date);
  const hours=field('时段',select([['24','24小时'],['48','48小时'],['72','72小时'],['144','144小时']]));
  const pair=make('div');pair.className='forecast-pair';panel.append(pair);
  const lon=make('input');lon.type='number';lon.step='.01';lon.value='122.3';lon.setAttribute('aria-label','经度');
  const lat=make('input');lat.type='number';lat.step='.01';lat.value='30.3';lat.setAttribute('aria-label','纬度');
  const lonLabel=make('label','经度'),latLabel=make('label','纬度');lonLabel.append(lon);latLabel.append(lat);pair.append(lonLabel,latLabel);
  const controls=make('div'),run=make('button','查询'),explain=make('button','在对话中解读');explain.hidden=true;controls.append(run,explain);panel.append(controls);
  const status=make('p','正在读取数据状态…');status.className='forecast-status';panel.append(status);
  const result=make('div');panel.append(result);
  const assumptions=make('details');assumptions.append(make('summary','查看本次假设口径'));const notes=make('div');assumptions.append(notes);panel.append(assumptions);
  document.querySelector('.map-panel').append(panel);
  let overlay=null,lastQuestion='',revision=0;
  async function get(path){const response=await fetch(path,{cache:'no-store'}),data=await response.json();if(!response.ok)throw Error(data.error||'查询失败');return data;}
  function table(headers,rows){const node=make('table'),head=make('tr');headers.forEach(h=>head.append(make('th',h)));node.append(head);rows.forEach(row=>{const tr=make('tr');row.forEach(v=>tr.append(make('td',v??'缺测')));node.append(tr);});result.append(node);}
  const time=value=>value?value.slice(5,16).replace('T',' '):'缺测';
  function chart(series,key){
    const values=series.filter(row=>Number.isFinite(row[key]));if(values.length<2)return;
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 350 145');
    svg.setAttribute('role','img');svg.setAttribute('aria-label','查询时段数值变化曲线');
    const min=Math.min(...values.map(r=>r[key])),max=Math.max(...values.map(r=>r[key]));
    const line=document.createElementNS(ns,'polyline');line.setAttribute('fill','none');line.setAttribute('stroke','#6ed9e9');line.setAttribute('stroke-width','2');
    line.setAttribute('points',values.map((r,i)=>(35+i/(values.length-1)*300)+','+(120-(r[key]-min)/(max-min||1)*90)).join(' '));svg.append(line);
    [max,min].forEach((v,i)=>{const text=document.createElementNS(ns,'text');text.setAttribute('x','3');text.setAttribute('y',i?126:25);text.setAttribute('fill','#aac9da');text.setAttribute('font-size','10');text.textContent=v.toFixed(1);svg.append(text);});result.append(svg);
  }
  function visibility(){station.parentElement.hidden=mode.value!=='tide';region.parentElement.hidden=mode.value!=='coastal';product.parentElement.hidden=mode.value!=='grid';pair.hidden=mode.value!=='grid';}
  mode.addEventListener('change',visibility);visibility();
  async function initialize(){
    const [data,sites]=await Promise.all([get('/api/forecast/status'),get('/api/forecast/sites')]);
    station.replaceChildren();sites.items.forEach(site=>{const option=make('option',site.name+' · '+site.code+(site.state==='empty'?'（空文件）':''));option.value=site.code;station.append(option);});station.value='KANMEN';
    notes.replaceChildren();data.assumptions.notes.forEach(note=>notes.append(make('p',note)));
    status.textContent='已登记'+(data.import?.tide_hours||0)+'条逐时潮位，'+data.grid_files+'个网格文件；自动更新状态：'+(data.sync?.state||'等待同步')+'。';
  }
  run.addEventListener('click',async()=>{
    const version=++revision;run.disabled=true;explain.hidden=true;result.replaceChildren(make('p','查询中…'));
    const params=new URLSearchParams({date:date.value,hours:hours.value});let url;
    if(mode.value==='tide'){params.set('station',station.value);url='/api/forecast/tide?'+params;lastQuestion=station.value+' '+date.value+'未来'+hours.value+'小时的天文潮和高低潮时刻是什么？';}
    else if(mode.value==='grid'){params.set('product',product.value);params.set('lon',lon.value);params.set('lat',lat.value);url='/api/forecast/grid?'+params;lastQuestion=date.value+'未来'+hours.value+'小时，经度'+lon.value+'纬度'+lat.value+'的'+({'wave':'浪高','wind':'风速','surge':'增水','temperature':'海温','current':'流速'}[product.value])+'预报是多少？';}
    else{params.set('region',region.value);url='/api/forecast/coastal?'+params;lastQuestion=(region.value||'浙江')+date.value+'未来'+hours.value+'小时哪些岸段超过警戒潮位？请按试算口径解读。';}
    try{
      const data=await get(url);if(version!==revision)return;result.replaceChildren();
      result.append(make('p','北京时间 '+time(data.start)+' 至 '+time(data.end)));
      if(mode.value==='tide'){
        if(data.state!=='ready'){result.append(make('p','该站在此时段没有可用天文潮数据。'));return;}
        result.append(make('p','潮位：厘米；基准采用假设配置。逐时峰值与精确高潮时刻分别展示。'+(data.partial?' 本次仅覆盖部分时段。':'')));
        chart(data.series,'value_cm');table(['高低潮','时刻','潮位cm'],[...data.high_tides,...data.low_tides].sort((a,b)=>a.time.localeCompare(b.time)).map(r=>[r.kind==='high'?'高潮':'低潮',time(r.time),r.value_cm]));
      }else if(mode.value==='grid'){
        if(data.state!=='ready'){result.append(make('p',data.reason||'该位置或时段无有效网格数据。'));return;}
        result.append(make('p','批次 '+time(data.batch)+'；单位 '+data.unit+(data.stale?'；批次已过期':'')+(data.partial?'；仅覆盖部分时段':'')));
        chart(data.series,'value');table(['指标','数值','出现时间'],[['最大值',data.maximum?.value,time(data.maximum?.time)],['最小值',data.minimum?.value,time(data.minimum?.time)]]);
      }else{
        const colors={red:'#ef6767',orange:'#f6a94f',yellow:'#ead66b',blue:'#5faff0',below:'#72a398'};
        const labels={red:'红',orange:'橙',yellow:'黄',blue:'蓝',below:'未达阈值'};
        result.append(make('p','请求'+data.requested_count+'岸段，可计算'+data.usable_count+'，缺数据'+data.missing_count+'。'+(data.partial_count?data.partial_count+'个岸段仅覆盖部分时段。':'')+'所有颜色均为假设试算。'));
        if(overlay)map.removeLayer(overlay);
        const features=data.results.filter(r=>r.geometry).map(r=>({type:'Feature',geometry:r.geometry,properties:r}));
        overlay=L.geoJSON({type:'FeatureCollection',features},{style:f=>({color:colors[f.properties.level]||'#78828e',weight:5,opacity:.9}),onEachFeature:(f,layer)=>{
          const p=f.properties,content=make('div');content.append(make('strong',p.name+' · '+(labels[p.level]||'缺数据')+'（试算）'),make('p',p.maximum?'最大总潮位 '+p.maximum.total_cm+' cm，'+time(p.maximum.time):p.reason));
          const ask=make('button','询问这个岸段');ask.addEventListener('click',()=>window.MapAssistant.send(p.name+' '+date.value+'未来'+hours.value+'小时最高潮位和超警试算结果是多少？'));content.append(ask);layer.bindPopup(content);
        }}).addTo(map);
        const ready=data.results.filter(r=>r.state==='ready').sort((a,b)=>b.maximum.total_cm-a.maximum.total_cm);
        table(['岸段','最高cm','试算等级'],ready.slice(0,12).map(r=>[r.name,r.maximum.total_cm,labels[r.level]]));
      }
      explain.hidden=false;status.textContent='查询完成 · 假设配置 '+data.assumption_id;
    }catch(error){if(version===revision){result.replaceChildren(make('p',error.message));}}
    finally{if(version===revision)run.disabled=false;}
  });
  explain.addEventListener('click',()=>window.MapAssistant.send(lastQuestion));
  $('forecastToggle').addEventListener('click',()=>{panel.hidden=!panel.hidden;if(!panel.hidden)initialize().catch(error=>{status.textContent=error.message;});});
  window.ForecastPanel={open:()=>{$('forecastToggle').click();}};
  window.loadLiveTyphoon=async()=>{
    const button=$('liveTyphoonBtn');button.disabled=true;
    try{
      const [data,current]=await Promise.all([get('/api/forecast/typhoons'),get('/api/typhoon/current')]);
      if(!data.list?.length)throw Error('当前接口没有可用活动台风。');
      setMode('typhoon');selectedTyphoonSid=null;lastTyphoonMatchData=null;selectedTyphoonTrackIndexes.clear();updateSelectedContext();
      $('typhoonResults').replaceChildren();$('typhoonJson').value=JSON.stringify(current,null,2);
      if(typhoonLayer)map.removeLayer(typhoonLayer);typhoonLayer=L.layerGroup();
      const bounds=[],agencies=['#7dd8f5','#ffbf73','#d0a4f8','#89dfad'];
      data.list.forEach(storm=>{
        const points=storm.points||[],coords=points.map(p=>[p.lat,p.lon]);bounds.push(...coords);
        const name=storm.nameCN||storm.nameEN||String(storm.typhoonID);
        const title=make('span',name+' · 已有路径记录');
        L.polyline(coords,{color:'#ff7086',weight:3}).bindTooltip(title).addTo(typhoonLayer);
        const latest=[...points].sort((a,b)=>a.time.localeCompare(b.time)).at(-1);
        if(!latest)return;
        const marker=make('div');marker.append(make('strong',name),make('p','最新记录 '+latest.time+'（北京时间，假设）'),make('p','最大风速 '+latest.maxSpeed+' m/s（单位假设）'));
        L.circleMarker([latest.lat,latest.lon],{radius:6,color:'#ff7086',fillOpacity:1}).bindPopup(marker).addTo(typhoonLayer);
        Object.entries(latest.forecast||{}).forEach(([agency,forecast],i)=>{
          if(!Array.isArray(forecast))return;
          const path=[[latest.lat,latest.lon],...forecast.map(p=>[p.lat,p.lon])];bounds.push(...path);
          L.polyline(path,{color:agencies[i%agencies.length],weight:2,dashArray:'6 5'}).bindTooltip(make('span',name+' · '+agency+'预报路径')).addTo(typhoonLayer);
        });
      });
      typhoonLayer.addTo(map);if(bounds.length)map.fitBounds(bounds,{padding:[24,24],maxZoom:6});
      $('typhoonStatus').textContent='接口返回'+data.list.length+'个活动台风；实线为已有记录，虚线为各机构预报。时间/风速单位采用假设口径。'+(data.cache_stale?' 当前展示缓存，刷新失败。':'');
    }catch(error){$('typhoonStatus').textContent=error.message;}
    finally{button.disabled=false;}
  };
})();
