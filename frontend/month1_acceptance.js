/* Human review of recorded local answers. No model or web requests. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = String(text);
    if (className) value.className = className;
    return value;
  };
  const verdicts = {passed: '完整正确（人工评定）', failed: '未通过', awaiting_data: '待资料／复核'};
  const criteria = {fact_correct: '事实和数字正确', complete: '完整覆盖约定子问题',
    citations_supported: '引文支持回答且可回查', time_scope_correct: '年份、对象、单位与时间口径正确'};
  let busy = false;

  async function request(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '请求失败');
    return result;
  }
  function message(text, error = false) {
    $('acceptance-message').textContent = text;
    $('acceptance-message').classList.toggle('error', error);
  }
  function render(data) {
    const summary = data.summary || {};
    $('acceptance-summary').textContent = '候选 '+(summary.case_count || 0)+' 题 · 已核定样本 '+(summary.sample_confirmed_count || 0)+
      ' · 已人工评定 '+(summary.reviewed_count || 0)+' · 完整正确 '+(summary.passed_count || 0)+
      '/20 · 目标 '+(summary.target_met ? '已达成（本候选集）' : '待完成：先核定20题，至少18题完整正确')+
      '。回答记录时间：'+(data.generated_at || '尚未生成')+'。';
    const body = $('acceptance-rows');
    body.replaceChildren();
    (data.items || []).forEach(item => {
      const row = node('tr');
      row.append(node('td', item.id+' · '+item.requirement_ids.join('、')), node('td', item.question));
      row.append(node('td', item.technical_checks_passed === true ? '通过（程序检查）' : item.technical_checks_passed === false ? '未通过' : '未执行'));
      row.append(node('td', item.review ? verdicts[item.review.verdict] : item.review_stale ? '回答已更新，需重评' : '未人工评定'));
      const cell = node('td');
      const button = node('button', '核验回答／记录', 'btn small');
      button.type = 'button';
      button.addEventListener('click', () => show(item, data.generated_at));
      cell.append(button);
      row.append(cell);
      body.append(row);
    });
  }
  async function load() {
    if (busy) return;
    busy = true;
    try {render(await request('/api/knowledge/month1-acceptance')); message('');}
    catch (error) {message('验收记录读取失败：'+error.message, true);}
    finally {busy = false;}
  }
  function field(form, label, id, value, maxLength, rows = 3) {
    const wrap = node('div', undefined, 'field');
    const title = node('label', label);
    title.htmlFor = id;
    const input = document.createElement(rows === 1 ? 'input' : 'textarea');
    input.id = id;
    input.value = value || '';
    input.maxLength = maxLength;
    input.required = true;
    if (rows !== 1) input.rows = rows;
    wrap.append(title, input);
    form.append(wrap);
    return input;
  }
  function checkbox(form, label, checked = false) {
    const wrap = node('label', undefined, 'check');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    wrap.append(input, node('span', label));
    form.append(wrap);
    return input;
  }
  function show(item, timestamp) {
    const target = $('acceptance-detail');
    target.hidden = false;
    target.replaceChildren(node('h3', item.id+' · '+item.question));
    target.append(node('p', '核验记录时间：'+(timestamp || '尚未生成')+'。'+item.review_focus, 'fine'));
    target.append(node('pre', item.answer || '尚无回答记录；请先运行本地验收工具生成记录。', 'product-excerpt'));
    (item.sources || []).forEach(source => {
      const card = node('p', undefined, 'fine');
      const link = node('a', (source.citation || '资料')+' · '+(source.title || source.filename)+' · '+source.locator);
      link.href = '/knowledge.html?source='+encodeURIComponent(source.source_id);
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      card.append(link);
      target.append(card);
    });
    const previous = item.review || {};
    const form = document.createElement('form');
    const reviewer = field(form, '验收人（真实填写）', 'acceptance-reviewer', previous.reviewer, 80, 1);
    const reference = field(form, '专家参考答案（含必要的缺项说明）', 'acceptance-reference', previous.reference_answer, 6000);
    const evidence = field(form, '核验出处：原件名称、页码／段落、适用版本和范围', 'acceptance-evidence', previous.evidence_note, 2000);
    const note = field(form, '验收意见与待补条件', 'acceptance-note', previous.note, 2000);
    const confirmed = checkbox(form, '我已核定此候选题的样本范围及来源就绪条件', previous.sample_confirmed);
    const checks = {};
    Object.entries(criteria).forEach(([key, label]) => {checks[key] = checkbox(form, label, previous.criteria && previous.criteria[key]);});
    const verdictLabel = node('label', '本题业务验收结果');
    verdictLabel.htmlFor = 'acceptance-verdict';
    const verdict = document.createElement('select'); verdict.id = 'acceptance-verdict';
    ['awaiting_data','failed','passed'].forEach(value => {
      const option = node('option', verdicts[value]); option.value = value; verdict.append(option);
    });
    verdict.value = previous.verdict || 'awaiting_data';
    const save = node('button', '保存人工验收记录', 'btn primary'); save.type = 'submit';
    const actions = node('div', undefined, 'actions-row'); actions.append(save);
    form.append(verdictLabel, verdict, actions);
    form.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true;
      try {
        const status = await request('/api/knowledge/status');
        if (!status.csrf_token) throw new Error('未获得本机管理令牌，请刷新页面');
        const result = await request('/api/knowledge/month1-acceptance/'+encodeURIComponent(item.id)+'/review', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-Knowledge-Token': status.csrf_token},
          body: JSON.stringify({reviewer: reviewer.value, reference_answer: reference.value, evidence_note: evidence.value,
            note: note.value, sample_confirmed: confirmed.checked, verdict: verdict.value,
            criteria: Object.fromEntries(Object.entries(checks).map(([key, input]) => [key, input.checked])),
            answer_sha256: item.answer_sha256, manifest_sha256: item.manifest_sha256})
        });
        render(result); show(result.items.find(row => row.id === item.id), result.generated_at);
        message('人工验收记录已保存并留痕；需求原题的完成状态需按完整需求另行核定。');
      } catch (error) {message(error.message, true); save.disabled = false;}
    });
    target.append(form);
    const history = node('details'); history.append(node('summary', '人工验收变更记录'));
    const historyBody = node('div', undefined, 'detail-body');
    (item.review_history || []).forEach(row => historyBody.append(node('p', row.created_at+' · '+row.reviewer+' · '+
      verdicts[row.verdict]+(row.current ? '' : '（对应旧回答）')+'：'+row.note, 'fine')));
    if (!(item.review_history || []).length) historyBody.append(node('p', '暂无人工验收记录。', 'fine'));
    history.append(historyBody); target.append(history);
  }
  $('tab-month1').addEventListener('click', load);
  $('month1-refresh').addEventListener('click', load);
  $('refresh-all').addEventListener('click', () => {if (!$('view-month1').hidden) load();});
})();
