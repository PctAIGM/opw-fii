// Exercise the real stylesheet loader against a warm browser HTTP cache.
// Local-only HTTP fixtures; no router requests, credentials or mutations.
const http=require('node:http'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const source=await fs.readFile('luci-app-fii/htdocs/luci-static/resources/view/fii/app.js','utf8');
 const loader=source.slice(source.indexOf('var fiiStyleHash ='),source.indexOf('var methods ='));
 const css=await fs.readFile('luci-app-fii/htdocs/luci-static/resources/fii/style.css','utf8');
 const version=source.match(/var fiiStyleHash = '([0-9a-f]{16})'/)[1];
 const oldCss=css.slice(0,css.indexOf('/* Proxy page:'));
 let releaseCss=false,recover=false,pending=[],counts={old:0,current:0,failed:0};
 function sendCss(res,body){res.writeHead(200,{'content-type':'text/css','cache-control':'public, max-age=3600, immutable'});res.end(body);}
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/assets/fii/style.css'){
   if(!url.search){counts.old++;return sendCss(res,oldCss);}
   counts.current++;if(!releaseCss){pending.push(res);return;}return sendCss(res,css);
  }
  if(url.pathname==='/fail/fii/style.css'){
   counts.failed++;if(recover)return sendCss(res,css);
   res.writeHead(404,{'cache-control':'no-store'});return res.end('Missing fixture stylesheet');
  }
  res.writeHead(200,{'content-type':'text/html','cache-control':'no-store'});
  const fail=url.pathname==='/failure',warm=url.pathname==='/warm';
  res.end('<!doctype html><html><head><meta charset="utf-8"><link id="fii-style" rel="stylesheet" href="/assets/fii/style.css"></head><body>'+
   '<main id="result" class="fii"><section class="fii-proxies-panel"><div class="fii-proxy-groups"><div class="fii-proxy-node">fixture</div></div></section></main>'+
   (warm?'':'<script>document.getElementById("result").hidden=true;var L={resource:function(path){return "'+(fail?'/fail/':'/assets/')+'"+path}};'+loader+
   'window.assetResult="pending";window.applyStyle=function(){return loadStyle().then(function(){document.getElementById("result").hidden=false;window.assetResult="ready"}).catch(function(error){window.assetResult="error";window.assetError=error.message})};window.applyStyle();</script>')+'</body></html>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/warm');assert.equal(counts.old,1);
  assert.equal(await page.locator('.fii-proxy-groups').evaluate(el=>getComputedStyle(el).display),'block');
  await page.goto(base+'/upgrade',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.assetResult==='pending');
  assert.equal(await page.locator('#result').evaluate(el=>el.hidden),true);
  assert.equal(counts.old,1,'Unversioned old CSS must actually be served from browser cache');
  releaseCss=true;for(const res of pending.splice(0))sendCss(res,css);
  await page.waitForFunction(()=>window.assetResult==='ready');
  assert.equal(await page.locator('#result').evaluate(el=>el.hidden),false);
  assert.equal(await page.locator('#fii-style').getAttribute('href'),'/assets/fii/style.css?v='+version);
  assert.equal(await page.locator('link[rel=stylesheet]').count(),1);
  assert.equal(await page.locator('.fii-proxy-groups').evaluate(el=>getComputedStyle(el).display),'grid');
  assert.equal(counts.current,1);
  await page.evaluate(()=>window.applyStyle());assert.equal(counts.current,1);
  await page.goto(base+'/warm');await page.goto(base+'/upgrade');await page.waitForFunction(()=>window.assetResult==='ready');
  assert.equal(counts.old,1);assert.equal(counts.current,1,'Unchanged versioned CSS should remain cacheable');
  await page.goto(base+'/failure');await page.waitForFunction(()=>window.assetResult==='error');
  assert.equal(await page.locator('#result').evaluate(el=>el.hidden),true);
  assert((await page.evaluate(()=>window.assetError)).includes('界面样式加载失败'));
  recover=true;await page.evaluate(()=>window.applyStyle());await page.waitForFunction(()=>window.assetResult==='ready');
  assert.equal(counts.failed,2);assert.equal(await page.locator('#result').evaluate(el=>el.hidden),false);
  assert.deepEqual(errors,[]);
  const result={passed:true,styleHash:version,warmOldCssReproduced:true,oldCacheBypassed:true,renderWaitsForCss:true,unchangedCssRemainsCached:true,failedLoadBlocksBrokenView:true,failedLoadRetryWorks:true,counts,errors};
  await fs.mkdir('build/performance',{recursive:true});await fs.writeFile('build/performance/ui-cache.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();for(const res of pending)res.destroy();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1});
