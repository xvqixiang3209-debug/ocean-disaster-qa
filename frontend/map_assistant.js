/* The map and professional QA share one server-owned conversation. */
(() => {
  'use strict';
  const client = new window.AssistantConversationClient();
  const $ = id => document.getElementById(id);
  let revision=0, selectionKey='', busy=false;
  const make = (tag, text, cls) => {
    const node=document.createElement(tag);
    if (text!==undefined) node.textContent=String(text);
    if (cls) node.className=cls;
    return node;
  };
  function setBusy(value) {
    busy=value;
    $('sendBtn').disabled=value;
    $('sendBtn').textContent=value ? '回答中…' : '发送';
  }
  function note(text) {
    $('chatMessages').appendChild(make('p',text,'conversation-note'));
    $('chatMessages').scrollTop=$('chatMessages').scrollHeight;
  }
  function richText(tag,text) {
    const node=make(tag);
    String(text).split(/(\*\*[^*\n]+\*\*)/g).forEach(part => node.appendChild(
      /^\*\*[^*\n]+\*\*$/.test(part) ? make('strong',part.slice(2,-2)) : document.createTextNode(part)));
    return node;
  }
  function renderAnswer(target,text) {
    const answer=make('div',undefined,'assistant-answer');
    const lines=String(text).replace(/\r/g,'').split('\n');
    const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(x=>x.trim());
    for(let i=0;i<lines.length;) {
      const line=lines[i];
      if(!line.trim()) {i++;continue;}
      if(line.includes('|') && i+1<lines.length && /^\s*\|?\s*:?-{3,}.*\|/.test(lines[i+1])) {
        const wrap=make('div',undefined,'assistant-table-wrap'),table=make('table',undefined,'assistant-table');
        const head=make('thead'),row=make('tr');cells(line).forEach(x=>row.appendChild(richText('th',x)));
        head.appendChild(row);table.appendChild(head);i+=2;
        const body=make('tbody');
        while(i<lines.length && lines[i].trim() && lines[i].includes('|')) {
          const tr=make('tr');cells(lines[i++]).forEach(x=>tr.appendChild(richText('td',x)));body.appendChild(tr);
        }
        table.appendChild(body);wrap.appendChild(table);answer.appendChild(wrap);continue;
      }
      if(/^#{1,6}\s/.test(line)) {answer.appendChild(richText('h3',line.replace(/^#{1,6}\s+/,'')));i++;continue;}
      if(/^\s*\*\*[^*]{1,60}\*\*[:：]?\s*$/.test(line)) {
        answer.appendChild(make('h3',line.trim().replace(/^\*\*|\*\*[:：]?$/g,'')));i++;continue;
      }
      if(/^\s*[-*]\s+/.test(line)) {
        const list=make('ul');
        while(i<lines.length && /^\s*[-*]\s+/.test(lines[i])) list.appendChild(richText('li',lines[i++].replace(/^\s*[-*]\s+/,'')));
        answer.appendChild(list);continue;
      }
      if(/^\s*\d+[.)、]\s*/.test(line)) {
        const list=make('ol');list.start=Number(line.match(/^\s*(\d+)/)[1]);
        while(i<lines.length && /^\s*\d+[.)、]\s*/.test(lines[i])) list.appendChild(richText('li',lines[i++].replace(/^\s*\d+[.)、]\s*/,'')));
        answer.appendChild(list);continue;
      }
      answer.appendChild(richText('p',line));i++;
    }
    target.appendChild(answer);
  }
  function sourcesDrawer(sources,audit) {
    const drawer=make('details',undefined,'map-answer-sources');
    drawer.appendChild(make('summary','参考来源 · '+sources.length+'条'));
    if(audit) {
      const local=audit.local || {}, web=audit.web || {};
      drawer.appendChild(make('p',
        '资料库检索：'+(local.executed ? (local.state==='unavailable' ? '暂不可用' : '已完成') : '未执行')+
        '；联网检索：'+(web.executed ? (web.cache_hit ? '已检索（复用缓存）' : '已执行') : web.attempted ? '本次未能执行' : '未启用')+
        '。本回答采用本地资料'+(audit.used_local || 0)+'篇、网络正文'+(audit.used_web || 0)+'篇、业务数据'+(audit.used_tool || 0)+'组。'+
        (audit.corrective_rounds ? '已针对未覆盖的子问题补充检索。' : '')));
    }
    sources.forEach((source,index) => {
      const card=make('section',undefined,'map-answer-source');
      card.appendChild(make('strong',(source.citation || '资料'+(index+1))+' · '+(source.title || source.filename || '来源资料')));
      if (source.locator) card.appendChild(make('p',source.locator));
      if (source.excerpt) card.appendChild(make('p',source.excerpt));
      if (source.source_type==='business_forecast' && /^\/api\/forecast\/evidence\/[a-f0-9]{24}$/.test(source.url || '')) {
        const link=make('a','查看本次数据及假设');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';card.appendChild(link);
      } else if (source.source_type==='public_web' && source.url) {
        try {
          const url=new URL(source.url);
          if (['http:','https:'].includes(url.protocol) && !url.username && !url.password) {
            const link=make('a','打开官方原文');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';card.appendChild(link);
          }
        } catch (_) {}
      } else if (source.source_type==='historical_case_tool') {
        const sid=(source.metadata || {}).sid;
        if (typeof sid==='string' && /^[A-Za-z0-9_-]{1,64}$/.test(sid)) {
          const link=make('a','查看历史台风记录');link.href='/api/typhoon/'+encodeURIComponent(sid);
          link.target='_blank';link.rel='noopener noreferrer';card.appendChild(link);
        }
      } else if (source.source_id) {
        const link=make('a','查看资料原文');
        link.href='/knowledge.html?source='+encodeURIComponent(source.source_id);
        link.target='_blank';link.rel='noopener noreferrer';card.appendChild(link);
        if (!String(source.source_id).startsWith('legacy:')) {
          const original=make('a','下载原件');
          original.href='/api/knowledge/sources/'+encodeURIComponent(source.source_id)+'/file';
          original.setAttribute('download','');card.appendChild(original);
        }
      }
      drawer.appendChild(card);
    });
    return drawer;
  }
  async function send(question, retryArticle) {
    if (busy) return;
    question=String(question || $('chatInput').value || '').trim();
    if (!question) return;
    if (question.length>2000) {note('问题太长，请控制在2000字以内。');return;}
    const current=revision;
    setBusy(true);$('chatInput').value='';
    if (!retryArticle) appendMessage('user',question);
    const article=retryArticle || appendStreamMessage(null), bubble=article.querySelector('.msg-bubble');
    bubble.replaceChildren(make('p','正在查询资料库和官方来源，并组织回答…','map-answer-pending'));
    try {
      const data=await client.ask(question);
      if (current!==revision) return;
      bubble.replaceChildren();
      appendAssistantAnswer(bubble,data.answer || '本次没有取得可用回答。');
      if(data.data_url && /^\/api\/forecast\/evidence\/[a-f0-9]{24}$/.test(data.data_url)) {
        const link=make('a','查看本次数据与来源');link.href=data.data_url;link.target='_blank';link.rel='noopener noreferrer';link.className='map-answer-copy';bubble.appendChild(link);
      }
      const retrievalAudit=(data.evidence_diagnostics || {}).crag_audit;
      if ((data.sources || []).length || retrievalAudit) bubble.appendChild(sourcesDrawer(data.sources || [],retrievalAudit));
      const copy=make('button','复制回答','map-answer-copy');copy.type='button';
      copy.addEventListener('click',async () => {
        try {await navigator.clipboard.writeText(data.answer || '');copy.textContent='已复制';}
        catch (_) {copy.textContent='请选中回答文字复制';}
      });
      bubble.appendChild(copy);
      $('conversationStatus').textContent='可继续追问本次回答，例如“第二点具体怎么做？”';
    } catch (error) {
      if (current!==revision) return;
      bubble.replaceChildren(make('p',error.name==='AbortError' ? '查询时间较长，请稍后重试。' : error.message,'map-answer-error'));
      const retry=make('button','重新查询','map-answer-copy');retry.type='button';
      retry.addEventListener('click',() => send(question,article));bubble.appendChild(retry);
    } finally {
      if (current===revision) {
        setBusy(false);$('chatInput').focus();
        $('chatMessages').scrollTop=$('chatMessages').scrollHeight;
      }
    }
  }
  function reset(clear=false, selection={}) {
    revision++;client.reset(selection);setBusy(false);
    document.querySelectorAll('.map-answer-pending').forEach(node => {node.textContent='此次查询已取消。';});
    if (clear) {
      $('chatMessages').replaceChildren();
      note(selection.sid ? '已新建对话，地图所选台风保持不变。可以重新提问。' : '已新建对话。可以直接提问，也可以先在地图中选择历史台风。');
      $('debugContent').textContent='';
    }
    $('chatInput').value='';$('conversationStatus').textContent='已开始新的对话，可连续追问。';
  }
  function setSelection(selection={}) {
    const key=String(selection.sid || '')+'|'+String(selection.request_id || '');
    if (key===selectionKey) return;
    const wasSelected=Boolean(client.selectedSid);
    selectionKey=key;reset(false,selection);
    if (selection.sid) note('已切换到所选历史台风，后续问题会结合这个案例。');
    else if (wasSelected) note('已退出台风案例，开始通用专业问答。');
  }
  $('chatInput').addEventListener('keydown',event => {
    if (event.key==='Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault();send();}
  });
  window.MapAssistant={send,reset:() => reset(true,{sid:client.selectedSid,request_id:client.matchRequestId}),setSelection,renderAnswer};
})();
