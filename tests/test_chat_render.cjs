// DOM-contract regression for formatting and escaping; no browser/network.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
class Node {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
}
const document = {createElement: tag => new Node(tag), createTextNode: text => new Node('#text', text)};
const source = fs.readFileSync(path.join(__dirname, '../frontend/chat.js'), 'utf8');
const makeCode = source.slice(source.indexOf('  const make ='), source.indexOf('  async function json'));
const renderCode = source.slice(source.indexOf('  function richText'), source.indexOf('  function sourceCards'));
const context = vm.createContext({document});
vm.runInContext(makeCode + renderCode, context);
function render(text) {const node = new Node('div'); context.renderAnswer(node, text); return node;}
function find(node, tag) {return [...(node.tag === tag ? [node] : []), ...node.children.flatMap(child => find(child, tag))];}
const result = render('**办理程序**\n\n1. **提出建议**：办公室研判。[资料1]\n2. 批准后通知有关单位。[资料1]\n\n<img src=x onerror=alert(1)>\n[恶意链接](javascript:alert(1))');
assert.equal(find(result, 'h3')[0].textContent, '办理程序');
assert.equal(find(result, 'ol').length, 1);
assert.equal(find(result, 'li').length, 2);
assert.equal(find(result, 'strong')[0].textContent, '提出建议');
assert.equal(find(result, 'img').length, 0);
assert.equal(find(result, 'a').length, 0);
assert.ok(find(result, '#text').some(node => node.textContent.includes('<img')));
const table = render('| 条件 | 内容 |\n|---|---|\n| **终止** | 经评估后决定。[资料1] |');
assert.equal(find(table, 'table').length, 1);
assert.equal(find(table, 'strong')[0].textContent, '终止');
console.log('PASS: headings, ordered steps, bold text, table formatting, literal HTML and unsafe links');
const advanced = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
for (const script of advanced.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) {
  if (script[1].trim()) new vm.Script(script[1]);
}
console.log('PASS: advanced workbench inline JavaScript syntax');
const mapSource=fs.readFileSync(path.join(__dirname,'../frontend/map_assistant.js'),'utf8');
const mapMake=mapSource.slice(mapSource.indexOf('  const make ='),mapSource.indexOf('  function setBusy'));
const mapRender=mapSource.slice(mapSource.indexOf('  function richText'),mapSource.indexOf('  function sourcesDrawer'));
const mapContext=vm.createContext({document});
vm.runInContext(mapMake+mapRender,mapContext);
const mapTarget=new Node('div');
mapContext.renderAnswer(mapTarget,'## 办理程序\n1. **提出建议**。[资料1]\n2. 批准后通知。\n\n| 指标 | 值 |\n|---|---|\n| 增水 | 3.12米 |\n\n<img src=x onerror=alert(1)>');
assert.equal(find(mapTarget,'h3').length,1);
assert.equal(find(mapTarget,'ol').length,1);
assert.equal(find(mapTarget,'strong').length,1);
assert.equal(find(mapTarget,'table').length,1);
assert.equal(find(mapTarget,'img').length,0);
console.log('PASS: integrated map answer headings, steps, bold, tables and literal untrusted HTML');
