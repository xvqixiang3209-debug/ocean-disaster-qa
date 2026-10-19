/* Real-service integration: reads plus local-only ask; never imports or reviews. */
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'C:/Users/1/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = process.env.PHASE1_TEST_ORIGIN || 'http://127.0.0.1:8767';
(async()=>{
  const browser = await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page = await browser.newPage({viewport:{width:1512,height:1120}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const prohibited=[];
  await page.route('**/*', route=>{
    const req=route.request();
    if(!req.url().startsWith(origin+'/')) return route.abort();
    if(req.method()!=='GET'&&!req.url().endsWith('/api/knowledge/ask')){prohibited.push(req.url());return route.abort();}
    return route.continue();
  });
  try{
    await page.goto(origin+'/knowledge.html');
    await page.waitForFunction(()=>/^\d/.test(document.getElementById('stat-sources').textContent));
    const status=await page.request.get(origin+'/api/knowledge/status');
    const statusBody=await status.json();
    assert.ok(statusBody.source_count>0);
    await page.locator('#source-category').selectOption('预报警报');
    await page.locator('#source-status').selectOption('indexed');
    await page.locator('#source-search button').click();
    await page.waitForFunction(()=>document.getElementById('source-message').textContent==='');
    assert.ok(await page.locator('#source-rows tr').count()>0);
    await page.locator('#source-rows button').first().click();
    await page.waitForSelector('#review-submit');
    assert.ok(await page.locator('.chunk-list details').count()>0);
    const detailTitle=await page.locator('.detail-title').textContent();
    const output=path.join(__dirname,'artifacts');fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'knowledge-live-sources.png'),fullPage:true});
    if(!(await page.locator('#next-page').isDisabled())){
      await page.locator('#next-page').click();
      await page.waitForFunction(()=>document.getElementById('page-label').textContent.startsWith('第 2 页'));
      assert.ok(await page.locator('#source-rows tr').count()>0);
    }
    await page.getByRole('tab',{name:'历史预报产品'}).click();
    await page.waitForFunction(()=>document.getElementById('forecast-message').textContent==='');
    assert.ok(await page.locator('#forecast-product-rows tr').count()>0);
    assert.match(await page.locator('#view-forecast').textContent(),/不是实时业务接口/);
    await page.locator('#forecast-product-rows button').first().click();
    assert.match(await page.locator('#forecast-detail').textContent(),/本地历史归档/);
    await page.locator('#forecast-query').fill('wj2018100516');
    await page.locator('#forecast-date').fill('2018-10-05');
    await page.locator('#forecast-region').fill('杭州湾');
    await page.locator('#forecast-search button').click();
    await page.waitForFunction(()=>document.getElementById('forecast-message').textContent==='');
    assert.ok(await page.locator('#forecast-product-rows tr').count()>0);
    assert.match(await page.locator('#forecast-product-rows').textContent(),/2018-10-05/);
    await page.locator('#forecast-product-rows button').first().click();
    assert.match(await page.locator('#forecast-detail .detail-title').textContent(),/wj2018100516/);
    await page.screenshot({path:path.join(output,'month1-live-products.png'),fullPage:true});
    await page.getByRole('tab',{name:'第1个月验收'}).click();
    await page.waitForFunction(()=>document.getElementById('month1-question-count').textContent==='160');
    await page.waitForSelector('#acceptance-rows tr');
    assert.equal(await page.locator('#acceptance-rows tr').count(),20);
    assert.match(await page.locator('#acceptance-summary').textContent(),/完整正确 0\/20/);
    await page.locator('#month1-scope').selectOption('public_later');
    await page.locator('#month1-search button[type="submit"]').click();
    await page.waitForFunction(()=>document.getElementById('month1-page-label').textContent.includes('共 36 条'));
    await page.locator('#acceptance-rows button').first().click();
    await page.waitForSelector('#acceptance-reference');
    assert.equal(await page.locator('#acceptance-reference').inputValue(),'');
    const legacySource=await page.locator('#acceptance-detail a[href*="legacy"]').first().getAttribute('href');
    const legacyId=new URL(origin+legacySource).searchParams.get('source');
    const legacyResponse=await page.request.get(origin+'/api/knowledge/sources/'+encodeURIComponent(legacyId));
    assert.equal(legacyResponse.status(),200);
    assert.equal((await legacyResponse.json()).source.original_available,false);
    await page.screenshot({path:path.join(output,'month1-live-acceptance.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.screenshot({path:path.join(output,'month1-live-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1512,height:1120});
    await page.getByRole('tab',{name:'导入与任务'}).click();
    await page.waitForFunction(()=>!document.getElementById('jobs-list').textContent.includes('正在读取'));
    await page.screenshot({path:path.join(output,'knowledge-live-jobs.png'),fullPage:true});
    await page.getByRole('tab',{name:'专业问答验收'}).click();
    assert.match(await page.locator('#model-state').textContent(),/最近一次无资料连接测试成功/);
    await page.locator('#ask-question').fill('2019年利奇马最大增水是多少');
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/knowledge/ask'),{timeout:90000});
    await page.locator('#ask-submit').click();
    const response=await responsePromise;assert.equal(response.status(),200);
    const answer=await response.json();assert.equal(answer.web_sources.length,0);
    await page.waitForFunction(()=>document.getElementById('ask-message').textContent.includes('完成'));
    await page.screenshot({path:path.join(output,'knowledge-live-answer.png'),fullPage:true});
    // The main-map SSE rendering is tested with a synthetic response, not a model call.
    await page.route(origin+'/api/chat',route=>route.fulfill({status:200,contentType:'text/event-stream',body:[
      {type:'meta',intent:'marine_knowledge_qa',model:'local-test'},
      {type:'content',content:'合成验收回答。'},
      {type:'rag_sources',items:[{source_id:'SRC /?<1>',filename:'<img src=x onerror=alert(1)>资料',locator:'第 3 页',status:'indexed',excerpt:'<script>alert(1)</script>合成节选'}]},
      {type:'done'}
    ].map(event=>'data: '+JSON.stringify(event)+'\n\n').join('')}));
    await page.goto(origin+'/map');
    await page.locator('#chatInput').fill('合成前端渲染测试');
    await page.locator('#sendBtn').click();
    await page.waitForSelector('.web-sources a[href^="/knowledge.html?source="]');
    const citation=page.locator('.web-sources a[href^="/knowledge.html?source="]').last();
    assert.ok((await citation.getAttribute('href')).includes(encodeURIComponent('SRC /?<1>')));
    assert.equal(await page.locator('.web-sources img').count(),0);
    assert.equal(await page.locator('.web-sources script').count(),0);
    assert.deepEqual(prohibited,[]);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:'PASS',source_count:statusBody.source_count,chunk_count:statusBody.chunk_count,opened:detailTitle,answer_meta:answer.answer_meta,rag_sources:answer.rag_sources.length,answer:answer.answer,screenshots:output},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
