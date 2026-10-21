/* Display-only library identity. Management writes stay in the protected workbench. */
(() => {
  'use strict';
  const labels={marine:'专业资料库',legacy:'历史台风资料库'};
  const normalize=value=>['all','marine','legacy'].includes(value)?value:'all';
  const libraryOf=source=>String(source?.source_id||'').startsWith('legacy:')||source?.library==='legacy'?'legacy':'marine';
  const readOnly=source=>source?.read_only===true||libraryOf(source)==='legacy';
  const label=source=>labels[libraryOf(source)];
  const reviewGroups=[
    {id:'structured',label:'原值已恢复、业务口径待确认'},
    {id:'partial',label:'部分内容已审核'},
    {id:'fulltext',label:'全文待核对'},
    {id:'version',label:'版本待确认'}
  ];
  const normalizeReviewGroup=value=>reviewGroups.some(row=>row.id===value)?value:'';
  function reviewGroup(source){
    if(readOnly(source)||source?.status!=='pending_review')return null;
    const group=reviewGroups.find(row=>row.id===source?.review_group?.id);
    return group?{...group,reason:String(source.review_group.reason||'').slice(0,1000)}:null;
  }
  function renderReviewGroups(target,data,onSelect,selected='',document=window.document){
    target.replaceChildren();
    const rows=Array.isArray(data?.groups)?data.groups:[];
    for(const group of reviewGroups){
      const item=rows.find(row=>row?.id===group.id),button=make(document,'button');
      button.type='button';button.className='review-group';button.dataset.group=group.id;
      button.setAttribute('aria-pressed',String(selected===group.id));
      const known=typeof item?.count==='number'&&Number.isFinite(item.count)&&item.count>=0;
      const count=known?item.count.toLocaleString('zh-CN'):'—';
      button.appendChild(make(document,'span',group.label)).className='review-group-label';
      button.appendChild(make(document,'strong',count)).className='review-group-count';
      button.appendChild(make(document,'span',known?String(item.description||'点击查看这一组资料').slice(0,350):'数量尚未确认，请刷新状态')).className='review-group-description';
      button.setAttribute('aria-label',group.label+'，'+(known?count+'份原件，点击筛选':'数量未确认'));
      button.addEventListener('click',()=>onSelect(group.id));
      target.appendChild(button);
    }
  }
  function reviewProgress(source){
    const m=source?.metadata||source||{};
    if(readOnly(source))return '';
    if(typeof source?.review_progress==='string')return source.review_progress.slice(0,1000);
    if(m.source_type==='official_document_card')return '已核文档身份；不含正文条款';
    if(['official_standard_excerpt','reviewed_historical_excerpt'].includes(m.source_type))return '已核原页节选；原文件全文状态另列';
    if(m.structured_archive_state==='raw_values_recovered')return '原始数值已恢复；单位、完整日期和基准待核';
    if(m.structured_archive_state==='coast_versions_recovered')return '岸段双版本已对照；冲突阈值待核';
    const notes=[];
    if(m.formal_replacement_source_ids?.length)notes.push('正式替代正文已入库');
    if(m.approved_excerpt_source_ids?.length)notes.push('已核节选 '+m.approved_excerpt_source_ids.length+' 份');
    if(source?.status==='pending_review'&&m.edition_current_verified===true)notes.push('官方版本已查证，正文仍待核');
    return notes.join('；');
  }
  function initialSelection(search){
    const params=new URLSearchParams(search||''),source=params.get('source')||'';
    return {library:source?(source.startsWith('legacy:')?'legacy':'marine'):normalize(params.get('library')),source};
  }
  const make=(document,tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node;};
  function renderSummary(target,data,document=window.document){
    target.replaceChildren();
    const libraries=Array.isArray(data?.libraries)?data.libraries:[];
    for(const id of ['marine','legacy']){
      const item=libraries.find(row=>row&&row.id===id),row=make(document,'p');
      const count=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value.toLocaleString('zh-CN'):'未确认';
      row.textContent=!item||item.available===false?labels[id]+'：暂不可用，数量未确认。':
        labels[id]+'：'+count(item.record_count)+'条来源记录、'+count(item.chunk_count)+'个切片'+(id==='legacy'?'；历史文本与关联记录只读查看。':'；新资料继续在“导入与任务”中维护。');
      target.appendChild(row);
    }
    const mode=data?.query_mode==='joint_local_retrieval'?'主问答按问题需要联合检索两类本地资料；预报数值由数据模块查询。查看台账时选择哪一类资料，不改变主问答能力。':data?.query_mode==='marine_only'?'当前只有专业资料库可用于本地检索；历史台风资料库暂不可用。':'主问答的联合检索状态尚未确认，请刷新状态。';
    target.appendChild(make(document,'p',mode));
    target.appendChild(make(document,'p',String(data?.count_note||'来源记录跨库未去重，不是独立有效文档数。').slice(0,500)));
  }
  function renderReadOnlyLinks(target,data,document=window.document){
    const source=data.source||data,links=data.links||source.links||[];
    target.appendChild(make(document,'h3','历史台风 SID 关联'));
    if(!Array.isArray(links)||!links.length){
      target.appendChild(make(document,'p',Number(source.linked_sid_count)>0?'台账登记有关联台风；本次详情未返回关联明细。':'本次详情未返回 SID 关联记录。'));
      return;
    }
    const table=make(document,'table'),head=make(document,'thead'),row=make(document,'tr');
    for(const text of ['切片 / SID','置信度','关联状态'])row.appendChild(make(document,'th',text));
    head.appendChild(row);table.appendChild(head);const body=make(document,'tbody');
    for(const link of links){
      const line=make(document,'tr'),sid=String(link.sid||link.typhoon_sid||'未登记');
      line.appendChild(make(document,'td','切片 '+String(link.chunk_id??'未登记')+' → '+sid));
      line.appendChild(make(document,'td',typeof link.confidence==='number'&&Number.isFinite(link.confidence)?String(link.confidence):'未登记'));
      line.appendChild(make(document,'td',({auto_accepted:'自动匹配（规则）',needs_review:'待复核',rejected:'已拒绝',approved:'已接受'}[link.review_status]||String(link.review_status||'未登记'))));
      body.appendChild(line);
    }
    table.appendChild(body);const wrap=make(document,'div');wrap.className='table-wrap';wrap.appendChild(table);target.appendChild(wrap);
    target.appendChild(make(document,'p','此处查看历史关联记录；不会修改资料审核、外发授权或台风关联。'));
  }
  const api={normalize,libraryOf,readOnly,label,reviewProgress,reviewGroup,reviewGroups,normalizeReviewGroup,renderReviewGroups,initialSelection,renderSummary,renderReadOnlyLinks};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.KnowledgeLibraries=api;
})();
