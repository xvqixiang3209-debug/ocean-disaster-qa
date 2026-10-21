/* Offline browser contract test. No real documents, external requests or model calls. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'C:/Users/1/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appRoot = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(appRoot, 'frontend', 'knowledge.html'), 'utf8');
const token = 'synthetic-test-token';
const malicious = '<img src=x onerror="window.pwned=true">报告';
let uploads = [], reviewBody, linkBody, askBody, searches = [], readOnly = false;
const source = {source_id:'SRC-1',filename:malicious,category:'历史报告',status:'indexed',quality_status:'ok',chunk_count:2,created_at:'2026-10-06',warnings:['合成测试资料，请勿用于业务'],external_allowed:false};
const product = {source_id:'PRODUCT-1',filename:'海浪预报2018100610.doc',category:'预报警报',review_status:'indexed',issue_time:{known:true,value:'2018-10-06T10:00:00',timezone:'unknown',locator:'paragraph:3'},valid_time:{known:false,text:'今天夜间到明天白天',locator:'paragraph:8'},publisher:{known:true,value:'浙江省海洋监测预报中心'},signatory:{known:false},regions:[{name:'杭州湾'}],data_role:'forecast+warning',freshness:'historical',live_source:false,original_url:'/api/knowledge/sources/PRODUCT-1/file',excerpt:'预计今天夜间到明天白天，杭州湾有大浪。'};
const json = (res, data, status=200) => {res.writeHead(status, {'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(data));};
const server = http.createServer(async(req,res) => {
  const url = new URL(req.url,'http://localhost');
  if (url.pathname === '/knowledge.html') {res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);return;}
  if (url.pathname === '/month1_acceptance.js') {res.writeHead(200,{'Content-Type':'text/javascript; charset=utf-8'});res.end(fs.readFileSync(path.join(appRoot,'frontend','month1_acceptance.js'),'utf8'));return;}
  if (url.pathname === '/knowledge_libraries.js') {res.writeHead(200,{'Content-Type':'text/javascript; charset=utf-8'});res.end(fs.readFileSync(path.join(appRoot,'frontend','knowledge_libraries.js'),'utf8'));return;}
  if (url.pathname === '/api/knowledge/libraries') return json(res,{libraries:[{id:'marine',record_count:1,chunk_count:2,available:true},{id:'legacy',record_count:0,chunk_count:0,available:true,read_only:true,originals_available:false}],query_mode:'joint_local_retrieval',count_note:'来源记录跨库未去重，不是独立有效文档数。'});
  if (url.pathname === '/api/knowledge/month1-acceptance') return json(res,{items:[],summary:{case_count:20,passed_count:0,reviewed_count:0,sample_confirmed_count:0}});
  if (url.pathname === '/api/knowledge/status') return json(res,{csrf_token:readOnly?'':token,source_count:1,chunk_count:2,status_counts:{indexed:1,approved:0,pending_review:0},category_counts:{'历史报告':1},models:['test-model'],external_model:{state:'disabled',enabled:false,configured:false,tested:false,note:'合成测试中关闭'}});
  if (url.pathname === '/api/forecast-products' && req.method === 'GET') return json(res,{items:[product],total:1,page:1,page_size:30,live_source:false,availability:'local_archive_only'});
  if (url.pathname === '/api/knowledge/sources' && req.method === 'GET') {searches.push(Object.fromEntries(url.searchParams));return json(res,{items:[source],total:1,page:1,page_size:20});}
  if (url.pathname === '/api/knowledge/sources/SRC-1' && req.method === 'GET') return json(res,{source,chunks:[{chunk_id:7,locator:'第 3 页',text:'2019 年利奇马：海门站最大增水 3.12 米。'},{chunk_id:8,locator:'第 4 页',text:malicious}],links:[{chunk_id:7,sid:'2019214N15134',confidence:0.9,review_status:'pending_review'}]});
  if (url.pathname === '/api/knowledge/sources/SRC-1/file' && req.method === 'GET') {res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="synthetic-original.txt"'});res.end('Synthetic original for browser verification only.');return;}
  if (url.pathname === '/api/knowledge/jobs') return json(res,{items:uploads.map((_,i)=>({job_id:'JOB-'+(i+1),status:'completed',total_files:i?1:100,processed_files:i?1:100}))});
  if (url.pathname.startsWith('/api/knowledge/jobs/')) return json(res,{job_id:url.pathname.split('/').pop(),status:'completed',total:100,processed:100,errors:[{filename:'fixture',reason:'人工构造的任务提示'}]});
  if (req.method === 'POST') {
    if (req.headers['x-knowledge-token'] !== token) return json(res,{error:'令牌错误'},403);
    const buffers=[];for await (const part of req) buffers.push(part);const body=Buffer.concat(buffers).toString('utf8');
    if (url.pathname === '/api/knowledge/import') {uploads.push(body);return json(res,{job_id:'JOB-'+uploads.length},202);}
    if (url.pathname === '/api/knowledge/links/review') {linkBody=JSON.parse(body);return json(res,{...linkBody,review_status:linkBody.status});}
    if (url.pathname.endsWith('/review')) {reviewBody=JSON.parse(body);return json(res,{ok:true});}
    if (url.pathname === '/api/knowledge/ask') {askBody=JSON.parse(body);return json(res,{answer:'### 历史参考\n\n| 指标 | 数值 |\n| --- | --- |\n| 最大增水 | 3.12 m |\n\n'+malicious,answer_meta:{used_mode:'extractive'},rag_sources:[{source_id:'SRC-1',filename:malicious,locator:'第 3 页',excerpt:'合成测试引用 <script>window.pwned=true</script>',url:'javascript:alert(1)',status:'indexed'}]});}
  }
  json(res,{error:'unknown route '+req.url},404);
});

(async() => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:'+server.address().port;
  const browser = await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
  try {
    await page.goto(origin+'/knowledge.html');
    await page.waitForFunction(()=>document.getElementById('stat-sources').textContent==='1');
    await page.getByRole('button',{name:malicious,exact:true}).click();
    await page.waitForSelector('#review-submit');
    assert.equal(await page.locator('#source-detail img').count(),0,'untrusted filename must be text');
    assert.equal(await page.locator('#review-external').isChecked(),false,'external model consent defaults false');
    assert.equal(await page.locator('#source-original-file').getAttribute('href'),'/api/knowledge/sources/SRC-1/file','original-file URL must use same-origin source ID only');
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#source-original-file').click();
    const download=await downloadPromise;
    assert.equal(download.suggestedFilename(),'synthetic-original.txt');
    await page.locator('#source-status').selectOption('indexed');
    await page.getByRole('button',{name:'检索',exact:true}).click();
    await page.waitForTimeout(100);
    assert.equal(searches.at(-1).status,'indexed');
    await page.locator('#review-status').selectOption('approved');
    await page.locator('#review-note').fill('已核对范围与版本');
    await page.locator('#review-submit').click();
    assert.match(await page.locator('#review-message').textContent(),/请勾选/);
    assert.equal(reviewBody,undefined,'approval cannot bypass acknowledgement');
    await page.locator('#review-confirm').check();
    await page.locator('#review-submit').click();
    await page.waitForFunction(()=>document.getElementById('global-message').textContent.includes('审核已保存'));
    assert.deepEqual(reviewBody,{status:'approved',note:'已核对范围与版本',external_allowed:false});
    page.on('dialog',dialog=>dialog.type()==='prompt'?dialog.accept('已核对台风名称年份和正文'):dialog.accept());
    await page.locator('.sid-review-table button[data-link-status="approved"]').click();
    await page.waitForFunction(()=>document.getElementById('global-message').textContent.includes('SID 关联已接受'));
    assert.deepEqual(linkBody,{chunk_id:7,sid:'2019214N15134',status:'approved',note:'已核对台风名称年份和正文'});
    source.quality_status='partial';source.status='pending_review';source.metadata={requires_human_review:true,external_allowed:true};delete source.external_allowed;
    await page.getByRole('button',{name:malicious,exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.review-box').dataset.requiresHumanReview==='true');
    assert.equal(await page.locator('#review-external').isChecked(),false,'even existing external permission must not pre-check new approval');
    assert.match(await page.locator('.review-box').textContent(),/当前外发授权：允许/);
    assert.match(await page.locator('#source-detail .detail-top').textContent(),/待审核/);
    await page.locator('#review-status').selectOption('approved');
    await page.locator('#review-confirm').check();
    await page.locator('#review-note').fill('已核对范围与版本');reviewBody=undefined;
    await page.locator('#review-submit').click();
    assert.match(await page.locator('#review-message').textContent(),/已逐页核对/);
    assert.equal(reviewBody,undefined,'partial/OCR review requires evidence prefix');
    await page.locator('#review-note').fill('已逐页核对第1至4页，文字与原件一致，核验范围为全文');
    await page.locator('#review-submit').click();
    await page.waitForFunction(()=>document.getElementById('global-message').textContent.includes('审核已保存'));
    assert.equal(reviewBody.external_allowed,false);
    source.metadata.is_draft=true;
    await page.getByRole('button',{name:malicious,exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.review-box').dataset.isDraft==='true');
    await page.locator('#review-status').selectOption('approved');
    await page.locator('#review-confirm').check();
    await page.locator('#review-note').fill('已逐页核对第1至4页');reviewBody=undefined;
    await page.locator('#review-submit').click();
    assert.match(await page.locator('#review-message').textContent(),/草案不能直接批准/);
    assert.equal(reviewBody,undefined);
    source.status='indexed';source.quality_status='parsed';source.metadata={};source.external_allowed=false;
    await page.getByRole('tab',{name:'导入与任务'}).click();
    const files=Array.from({length:101},(_,i)=>({name:'fixture-'+i+'.txt',mimeType:'text/plain',buffer:Buffer.from('offline test '+i)}));
    await page.locator('#upload-files').setInputFiles(files);
    await page.locator('#upload-submit').click();
    await page.waitForFunction(()=>document.getElementById('upload-progress-label').textContent.includes('全部 101'));
    assert.equal(uploads.length,2,'101 files should upload in 2 batches');
    assert.equal((uploads[0].match(/name="files";/g)||[]).length,100);
    assert.equal((uploads[1].match(/name="files";/g)||[]).length,1);
    assert.ok(uploads.every(body=>body.includes('name="relative_paths"')));
    await page.getByRole('tab',{name:'历史预报产品'}).click();
    await page.waitForFunction(()=>document.getElementById('forecast-summary').textContent.includes('1 份'));
    assert.match(await page.locator('#view-forecast').textContent(),/不是实时业务接口/);
    assert.match(await page.locator('#forecast-product-rows').textContent(),/2018-10-06T10:00:00/);
    await page.locator('#forecast-product-rows button').click();
    assert.match(await page.locator('#forecast-detail').textContent(),/杭州湾/);
    assert.match(await page.locator('#forecast-detail').textContent(),/预报＋警报/);
    await page.getByRole('tab',{name:'专业问答验收'}).click();
    await page.locator('#ask-question').fill('利奇马最大增水是多少？');
    await page.locator('#ask-submit').click();
    await page.waitForSelector('#answer table');
    assert.equal(askBody.allow_web,false);
    assert.equal(await page.locator('#answer img').count(),0);
    assert.equal(await page.locator('#answer-sources a').count(),0,'javascript URL cannot become link');
    assert.equal(await page.evaluate(()=>window.pwned),undefined);
    const output=path.join(appRoot,'tests','artifacts');fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'knowledge-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'knowledge-mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile page must not horizontally overflow');
    readOnly=true;
    await page.reload();
    await page.waitForFunction(()=>document.getElementById('global-message').textContent.includes('只读'));
    assert.equal(await page.locator('#ask-submit').isDisabled(),true);
    assert.equal(await page.locator('#upload-submit').isDisabled(),true);
    assert.deepEqual(errors,[],'browser should have no JavaScript errors');
    console.log(JSON.stringify({result:'PASS',checks:['status','source filter','source detail','safe text rendering','explicit review consent','default no external model consent','separate SID review with note and confirmation','batched upload 100+1','historical forecast product query and labels','local-only ask','Markdown table','safe citations','mobile layout','read-only mode','same-origin original-file download','OCR/partial review evidence and pending state','draft approval blocked'],screenshots:output},null,2));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
