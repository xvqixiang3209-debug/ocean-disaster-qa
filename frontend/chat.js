(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let token = '', active = null, generation = 0;
  const conversation = new window.AssistantConversationClient();
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  async function json(path, options = {}) {
    const response = await fetch(path, {credentials:'same-origin', cache:'no-store', ...options});
    let result;
    try {result = await response.json();} catch (_) {throw new Error('服务连接异常，请稍后重试。');}
    if (!response.ok) {const error = new Error(result.error || '暂时无法完成查询。'); error.status = response.status; throw error;}
    return result;
  }
  async function connect(signal) {
    const session = await json('/api/assistant/session', {signal});
    if (!session.token) throw new Error('未能连接问答服务，请刷新页面。');
    token = session.token;
  }
  function setBusy(value) {
    $('send').disabled = value;
    $('send').textContent = value ? '查询中…' : '发送 ↑';
    document.querySelectorAll('.suggestions button').forEach(button => {button.disabled = value;});
  }
  function addMessage(kind, question) {
    const article = make('article', undefined, 'message '+kind+'-message');
    if (kind === 'user') article.append(make('div', question, 'user-text'));
    else {
      const heading = make('div', undefined, 'assistant-heading');
      heading.append(make('span', '≈', 'assistant-dot'), make('span', '海洋助手'));
      article.append(heading);
    }
    $('messages').append(article);
    return article;
  }
  // Render only text nodes and tables. Retrieved documents and model output
  // cannot introduce HTML, scripts or untrusted hyperlinks into this page.
  function richText(tag, text) {
    const node = make(tag);
    String(text).split(/(\*\*[^*\n]+\*\*)/g).forEach(part => {
      node.append(/^\*\*[^*\n]+\*\*$/.test(part) ? make('strong', part.slice(2, -2)) : document.createTextNode(part));
    });
    return node;
  }
  function renderAnswer(target, text) {
    const lines = String(text).replace(/\r/g, '').split('\n');
    const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(value => value.trim());
    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      if (!line.trim()) {index++; continue;}
      if (/^\s*\*\*[^*]{1,60}\*\*[:：]?\s*$/.test(line)) {
        target.append(make('h3', line.trim().replace(/^\*\*|\*\*[:：]?$/g, ''))); index++; continue;
      }
      if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}.*\|/.test(lines[index+1])) {
        const wrap = make('div', undefined, 'table-wrap'), table = make('table');
        const head = make('thead'), heading = make('tr');
        cells(line).forEach(value => heading.append(richText('th', value)));
        head.append(heading); table.append(head);
        const body = make('tbody'); index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
          const row = make('tr'); cells(lines[index++]).forEach(value => row.append(richText('td', value))); body.append(row);
        }
        table.append(body); wrap.append(table); target.append(wrap); continue;
      }
      if (/^#{1,6}\s/.test(line)) {target.append(make('h3', line.replace(/^#{1,6}\s+/, ''))); index++; continue;}
      if (/^\s*[-*]\s+/.test(line)) {
        const list = make('ul');
        while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) list.append(richText('li', lines[index++].replace(/^\s*[-*]\s+/, '')));
        target.append(list); continue;
      }
      if (/^\s*\d+[.)、]\s*/.test(line)) {
        const list = make('ol');
        list.start = Number(line.match(/^\s*(\d+)/)[1]);
        while (index < lines.length && /^\s*\d+[.)、]\s*/.test(lines[index])) {
          list.append(richText('li', lines[index++].replace(/^\s*\d+[.)、]\s*/, '')));
        }
        target.append(list); continue;
      }
      target.append(richText('p', line)); index++;
    }
  }
  function sourceCards(sources) {
    const detail = make('details', undefined, 'sources');
    detail.append(make('summary', '参考来源（可选） · '+sources.length+' 条资料'));
    sources.forEach((source, index) => {
      const card = make('article', undefined, 'source-card');
      card.append(make('h3', (source.citation || '资料'+(index+1))+' · '+(source.title || source.filename || '来源资料'), 'source-title'));
      const status = source.review_label || (source.status === 'approved' ? '已审核资料' : '历史参考资料');
      card.append(make('p', (source.locator || '定位未注明')+' · '+status));
      if (source.excerpt) card.append(make('p', source.excerpt));
      if (source.quality_warning) card.append(make('p', source.quality_warning));
      if (source.source_type === 'business_forecast' && /^\/api\/forecast\/evidence\/[a-f0-9]{24}$/.test(source.url || '')) {
        const link=make('a','查看本次数据及假设');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);
      } else if (source.source_type === 'public_web' && source.url) {
        try {
          const url = new URL(source.url);
          if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
            const link = make('a', '打开网页原文');
            link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
            card.append(link);
          }
        } catch (_) { /* Invalid citation links are not rendered. */ }
        const retrieved = source.metadata && source.metadata.retrieved_at;
        if (retrieved) card.append(make('p', '网页读取时间：'+retrieved));
      } else if (source.source_id) {
        const actions = make('div', undefined, 'source-actions');
        const button = make('button', '查看保留原文'); button.type = 'button';
        const full = make('div', undefined, 'source-full-text'); full.hidden = true;
        button.addEventListener('click', async () => {
          if (full.dataset.loaded) {full.hidden = !full.hidden; return;}
          button.disabled = true;
          try {
            const result = await json('/api/knowledge/sources/'+encodeURIComponent(source.source_id));
            full.replaceChildren();
            if (result.source && result.source.original_available === false) full.append(make('p', '该历史库仅保留提取文本，原件需向资料提供方核验。'));
            (result.chunks || []).forEach(chunk => {const section = make('details'); section.append(make('summary', chunk.locator || '正文片段'), make('p', chunk.text || '')); full.append(section);});
            if (result.chunks_truncated) full.append(make('p', '这里只展示部分正文，完整内容请核对原件。'));
            full.dataset.loaded = 'true'; full.hidden = false;
          } catch (error) {full.textContent = error.message; full.hidden = false;}
          finally {button.disabled = false;}
        });
        actions.append(button);
        if (!String(source.source_id).startsWith('legacy:')) {
          const original = make('a', '下载原件');
          original.href = '/api/knowledge/sources/'+encodeURIComponent(source.source_id)+'/file';
          original.setAttribute('download', ''); actions.append(original);
        }
        card.append(actions, full);
      }
      detail.append(card);
    });
    return detail;
  }
  function displayResult(article, result) {
    const sources = result.sources || [], answer = make('div', undefined, 'answer');
    let text = result.answer || '暂时没有可展示的回答。';
    if (result.mode === 'local_extractive' && sources.length) {
      // Preserve the same first-four excerpts as the existing service, while
      // moving repeated filenames and review metadata into the source drawer.
      text = '已有历史资料记载如下：\n\n'+sources.slice(0,4).map((source, index) =>
        '- '+(source.identity_anchor ? '案例上下文：'+source.identity_anchor+'。 ' : '')+
        (source.excerpt || '')+' ['+(source.citation || '资料'+(index+1))+']').join('\n\n');
    }
    renderAnswer(answer, text); article.append(answer);
    if (result.mode === 'local_extractive' && sources.length) {
      article.append(make('p', '回答方式：资料原文摘录。仅反映对应年份和统计口径。', 'answer-state'));
      const warnings = [...new Set(sources.map(source => source.quality_warning).filter(Boolean))];
      warnings.forEach(warning => article.append(make('p', warning, 'answer-state')));
    }
    if (sources.length) article.append(sourceCards(sources));
    const copy = make('button', '复制回答', 'copy-answer'); copy.type = 'button';
    copy.addEventListener('click', async () => {
      try {await navigator.clipboard.writeText(text); copy.textContent = '已复制';}
      catch (_) {copy.textContent = '请选中回答文字复制';}
    });
    article.append(copy);
  }
  async function ask(question, retryArticle) {
    question = String(question || '').trim();
    if (!question || active) return;
    if (question.length > 2000) {$('connection-message').textContent = '问题太长，请控制在2000字以内。'; return;}
    const id = ++generation, controller = new AbortController(); active = controller;
    setBusy(true); $('connection-message').textContent = ''; $('welcome').hidden = true;
    if (!retryArticle) addMessage('user', question);
    const article = retryArticle || addMessage('assistant');
    if (retryArticle) [...article.children].slice(1).forEach(child => child.remove());
    const pending = make('p', '正在查询资料库和官方网络来源，并综合回答…', 'pending'); article.append(pending);
    $('question').value = ''; $('question').style.height = '';
    article.scrollIntoView({block:'start', behavior:'instant'});
    const timer = setTimeout(() => controller.abort(), 180000);
    try {
      const result = await conversation.ask(question);
      if (id !== generation) return;
      pending.remove(); displayResult(article, result);
    } catch (error) {
      if (id !== generation) return;
      pending.remove();
      article.append(make('p', error.name === 'AbortError' ? '查询时间较长，请稍后重试。' : error.message, 'error-message'));
      const retry = make('button', '重新查询', 'retry'); retry.type = 'button';
      retry.addEventListener('click', () => ask(question, article)); article.append(retry);
    } finally {
      clearTimeout(timer);
      if (id === generation) {active = null; setBusy(false); $('question').focus({preventScroll:true});}
    }
  }
  $('question-form').addEventListener('submit', event => {event.preventDefault(); ask($('question').value);});
  $('question').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault(); ask($('question').value);}
  });
  $('question').addEventListener('input', () => {$('question').style.height = ''; $('question').style.height = Math.min($('question').scrollHeight,180)+'px';});
  document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => ask(button.dataset.question)));
  $('new-chat').addEventListener('click', () => {
    conversation.reset();
    generation++; if (active) active.abort(); active = null; setBusy(false);
    $('messages').replaceChildren(); $('welcome').hidden = false; $('question').value = '';
    $('question').style.height = ''; $('connection-message').textContent = ''; $('question').focus();
    window.scrollTo({top:0, behavior:'instant'});
  });
})();
