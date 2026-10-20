// Read-only management benchmarks. Never invokes save, apply or service controls.
// Set FII_ROUTER_URL, FII_ROUTER_PASSWORD and optionally FII_ROUTER_PROXY.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const label=process.argv[2]||'current';
if (!/^[a-z0-9-]+$/i.test(label)) throw Error('Invalid result label');
if (!process.env.FII_ROUTER_URL || !process.env.FII_ROUTER_PASSWORD) throw Error('FII_ROUTER_URL and FII_ROUTER_PASSWORD are required');
(async()=>{
const browser=await chromium.launch({channel:'chrome',headless:true,
 ...(process.env.FII_ROUTER_PROXY?{proxy:{server:process.env.FII_ROUTER_PROXY}}:{})});
try {
const page=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[],rows=[];let frames=[],rejectedDownloads=0;
page.on('pageerror',e=>errors.push(e.message));
if (process.env.FII_FORCE_CHUNK_DOWNLOAD === '1') {
 await page.route(url=>url.pathname.endsWith('/cgi-download'),route=>{
  rejectedDownloads++;return route.fulfill({status:403,body:'Download disabled by benchmark'});
 });
}
await page.route(/\/ubus\/?/,async route=>{
 const body=route.request().postDataJSON();
 const calls=Array.isArray(body)?body:[body];
 for(const call of calls){
  const method=call?.params?.[2];
  const allowed=['get','status','network','core_info','groups','preview','job','transfer_read','transfer_release'];
  if(call?.params?.[1]==='luci.fii'&&!allowed.includes(method)) {
   await route.abort('blockedbyclient');throw Error('Unapproved operation blocked: '+method);
  }
 }
 await route.continue();
});
page.on('request',r=>{if(r.method()==='POST'){let method;try{method=r.postDataJSON()?.params?.[2]}catch{}frames.push(method||(r.url().includes('cgi-download')?'download':'other'));}});
await page.goto(new URL('/cgi-bin/luci/admin/services/fii#outputs',process.env.FII_ROUTER_URL).href);
await page.locator('input[type=password]').fill(process.env.FII_ROUTER_PASSWORD);
await page.locator('button[type=submit],input[type=submit]').first().click();
await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor({timeout:60000});
async function tab(name){await page.getByRole('button',{name,exact:true}).click();}
async function measure(name,button,done){
 frames=[];
 await page.evaluate(({button,done})=>{
  const target=Array.from(document.querySelectorAll('.fii-body button')).find(x=>x.textContent===button);
  window.probeStart=performance.now();target.click();
  window.probeDone=null;
  function check(){if(new Function('return '+done)()){window.probeDone=performance.now();return;}requestAnimationFrame(check);}check();
 },{button,done});
 await page.waitForFunction(()=>window.probeDone!==null,null,{timeout:90000});
 const ms=await page.evaluate(()=>Math.round(window.probeDone-window.probeStart));
 rows.push({name,ms,requests:[...frames]});
 await page.getByRole('button',{name:name==='preview'?'关闭':'取消',exact:true}).click();
}
await tab('输出配置');
await measure('output-editor','编辑',`!!document.querySelector('.fii-editor input')`);
await tab('订阅源');
await measure('source-editor','编辑',`!!document.querySelector('.fii-editor input')`);
await tab('覆写');
await measure('override-editor','编辑',`!!document.querySelector('.fii-editor textarea')`);
await tab('输出配置');
for(let i=0;i<2;i++)await measure('preview','预览',`!!Array.from(document.querySelectorAll('textarea')).find(x=>x.readOnly&&x.value.includes('proxies:'))`);
if (process.env.FII_FORCE_CHUNK_DOWNLOAD === '1' && (!rejectedDownloads || !rows.some(row=>row.requests.includes('transfer_read')))) throw Error('Chunk fallback was not exercised');
const result={label,rows,errors,rejectedDownloads};await fs.mkdir('build/performance',{recursive:true});
await fs.writeFile('build/performance/management-'+label+'.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
} finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
