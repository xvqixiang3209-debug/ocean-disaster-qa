/* Ordinary-user flow, including errors, safe sources and cancelling a query. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('C:/Users/1/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const live = process.env.CHAT_TEST_ORIGIN;
let failed = false;
const requests = [];
const send = (res, body, status=200) => {res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(body));};
const malicious = '<img src=x onerror="window.pwned=true">';
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (['/','/chat','/chat.js','/chat.css','/assistant_client.js'].includes(url.pathname)) {
    const file = ['/', '/chat'].includes(url.pathname) ? 'chat.html' : url.pathname.slice(1);
    res.writeHead(200, {'Content-Type':file.endsWith('.html')?'text/html; charset=utf-8':file.endsWith('.js')?'text/javascript; charset=utf-8':'text/css; charset=utf-8'});
    return res.end(fs.readFileSync(path.join(root,'frontend',file)));
  }
  if (url.pathname === '/api/assistant/session') return send(res, {token:'question-only-token'});
  if (url.pathname.startsWith('/api/assistant/conversations/') && req.method==='DELETE') return send(res,{cleared:true});
  if (url.pathname === '/api/knowledge/sources/SRC-1') return send(res, {source:{filename:malicious}, chunks:[{locator:'第3页',text:'原文：海门站3.12米。'}]});
  if (url.pathname === '/api/assistant/ask') {
    assert.equal(req.headers['x-assistant-token'],'question-only-token');
    const buffers=[]; for await (const data of req) buffers.push(data);
    const body=JSON.parse(Buffer.concat(buffers)); requests.push(body);
    if (body.question === '失败测试' && !failed) {failed=true; return send(res, {error:'暂时无法完成查询'},503);}
    if (body.question === '等待测试') await new Promise(resolve=>setTimeout(resolve,300));
    if (body.question === '缺源测试') return send(res, {answer:'没有足够的资料，无法确认当前情况。',mode:'no_evidence',sources:[]});
    if (body.question === '联网测试') return send(res, {answer:'台风是热带气旋的一种。[资料1]',mode:'llm',sources:[
      {citation_id:1,citation:'资料1',source_id:'web:official',source_type:'public_web',title:'台风的定义',url:'https://www.cma.gov.cn/definition.html',metadata:{retrieved_at:'2026-10-15T13:00:00Z'}},
      {citation_id:2,source_id:'web:invalid',source_type:'public_web',title:'非法链接',url:'javascript:alert(1)'}]});
    return send(res, {answer:'海门站最大增水3.12米。[资料1]',mode:'local_extractive',sources:[{
      source_id:'SRC-1',title:malicious+'历史报告',locator:'第3页',status:'indexed',citation:'资料1',excerpt:'海门站最大增水3.12米。'+malicious}]});
  }
  send(res, {error:'unknown'},404);
});

(async () => {
  if (!live) await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=live || 'http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[], prohibited=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
    const req=route.request();
    if (!req.url().startsWith(origin+'/')) return route.abort();
    if(req.method()!=='GET'&&!req.url().endsWith('/api/assistant/ask')&&
       !(req.method()==='DELETE'&&req.url().startsWith(origin+'/api/assistant/conversations/'))) {prohibited.push(req.url()); return route.abort();}
    return route.continue();
  });
  const output=path.join(__dirname,'artifacts'); fs.mkdirSync(output,{recursive:true});
  try {
    await page.goto(origin+(live?'/chat':'/'));
    await page.getByRole('heading',{name:'你想了解什么？'}).waitFor();
    assert.equal(await page.getByRole('link',{name:'资料管理工作台'}).isVisible(),false);
    assert.equal(await page.locator('#welcome').textContent().then(text=>/切片|验收台账|审核状态/.test(text)),false);
    await page.screenshot({path:path.join(output,live?'chat-live-home.png':'chat-home.png'),fullPage:true});
    const answerResponse = page.waitForResponse(response=>response.url().endsWith('/api/assistant/ask'),{timeout:90000});
    await page.locator('[data-question]').first().click();
    const reply = await (await answerResponse).json();
    if (live) {assert.equal(reply.mode,'llm'); assert.doesNotMatch(reply.answer,/检索到以下相关原文节选/);}
    await page.locator('.sources').waitFor({timeout:90000});
    assert.equal(await page.locator('.sources').getAttribute('open'),null);
    assert.match(await page.locator('.answer').textContent(),/3\.1[12]|312/);
    await page.locator('.sources > summary').click();
    await page.locator('.source-actions button').first().click();
    await page.waitForSelector('.source-full-text:not([hidden])');
    assert.ok(await page.locator('.source-full-text details').count()>0);
    assert.equal(await page.locator('#messages img').count(),0);
    assert.equal(await page.evaluate(()=>window.pwned),undefined);
    await page.screenshot({path:path.join(output,live?'chat-live-answer.png':'chat-answer.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.screenshot({path:path.join(output,live?'chat-live-mobile.png':'chat-mobile.png'),fullPage:true});
    await page.locator('#new-chat').click();
    assert.equal(await page.locator('.message').count(),0);
    assert.equal(await page.locator('#welcome').isVisible(),true);
    if (!live) {
      await page.locator('#question').fill('失败测试'); await page.locator('#send').click();
      await page.locator('.retry').waitFor(); await page.locator('.retry').click();
      await page.locator('.sources').waitFor();
      assert.equal(await page.locator('.user-message').count(),1);
      await page.locator('#question').fill('缺源测试'); await page.locator('#question').press('Enter');
      await page.waitForFunction(()=>document.getElementById('messages').textContent.includes('无法确认当前情况'));
      assert.equal(await page.locator('.assistant-message').last().locator('.sources').count(),0);
      await page.locator('#question').fill('联网测试'); await page.locator('#send').click();
      const webSources=page.locator('.assistant-message').last().locator('.sources');
      await webSources.waitFor(); await webSources.locator('summary').click();
      assert.equal(await webSources.getByRole('link',{name:'打开网页原文'}).count(),1);
      assert.equal(await webSources.getByRole('link',{name:'打开网页原文'}).getAttribute('href'),'https://www.cma.gov.cn/definition.html');
      assert.equal(await webSources.getByRole('link',{name:'下载原件'}).count(),0);
      assert.equal(await webSources.getByRole('button',{name:'查看保留原文'}).count(),0);
      await page.locator('#question').fill('等待测试'); await page.locator('#send').click();
      await page.locator('.pending').waitFor(); await page.locator('#new-chat').click();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('.message').count(),0);
      assert.ok(requests.every(body=>!('history' in body)&&!('model' in body)&&typeof body.question==='string'));
    }
    await page.locator('.more-menu > summary').click();
    assert.equal(await page.getByRole('link',{name:'历史台风地图'}).getAttribute('href'),'/map');
    assert.equal(await page.getByRole('link',{name:'资料管理工作台'}).isVisible(),true);
    assert.deepEqual(errors,[]); assert.deepEqual(prohibited,[]);
    console.log(JSON.stringify({result:'PASS',live:!!live,checks:['simple welcome','question and answer','sources collapsed by default','retained original text','safe source rendering','mobile layout','new question','management menu',...(!live?['error retry','missing evidence','abort stale response']:[])],screenshots:output},null,2));
  } finally {await browser.close(); if (!live) await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
