// All writes are blocked. --fixture consumes log reads; --candidate serves local assets.
const {chromium}=require('playwright'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const fixture=process.argv.includes('--fixture'),candidate=fixture||process.argv.includes('--candidate');
(async()=>{
 await fs.mkdir('build/performance',{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true,proxy:{server:process.env.FII_ROUTER_PROXY}});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],blocked=[],methods=[];
  let reads=0,failure=false,delay=0,sequence=180;
  function snapshot(){return {text:Array.from({length:180},(_,i)=>{
   const id=sequence-179+i,level=['info','warning','error','debug'][i%4];
   return `Tue Oct 20 12:00:00 2026 daemon.${i%4===2?'err':'info'} fii[123]: time="2026-10-20T12:00:00+08:00" level=${level} msg=${JSON.stringify('记录 '+id+' · '+(i%4===2?'连接失败':'连接状态')+' <img src=x onerror=alert(1)> '+(id%11===0?'long-domain.example/'.repeat(22):''))}`;
  }).join('\n')+'\nTue Oct 20 12:00:01 2026 user.notice fii: Mihomo ready; configured traffic interception installed\n'+
  'Tue Oct 20 12:00:01 2026 daemon.info mihomo[456]: {"level":"error","msg":"JSON 格式错误记录","time":"2026-10-20T12:00:01+08:00"}',prepareText:'配置检查完成\n',limit:500,truncated:false};}
  page.on('pageerror',e=>errors.push(e.message));
  if(candidate){
   await page.route(/\/resources\/view\/fii\/app\.js(?:\?|$)/,r=>r.fulfill({path:'luci-app-fii/htdocs/luci-static/resources/view/fii/app.js',contentType:'application/javascript'}));
   await page.route(/\/resources\/fii\/style\.css(?:\?|$)/,r=>r.fulfill({path:'luci-app-fii/htdocs/luci-static/resources/fii/style.css',contentType:'text/css'}));
  }
  await page.route(/\/ubus\/?/,async route=>{
   const req=route.request().postDataJSON();
   for(const call of Array.isArray(req)?req:[req]){
    if(call?.params?.[1]!=='luci.fii')continue;
    const method=call.params[2];methods.push(method);
    if(!['get','status','network','core_info','logs','job','transfer_read','transfer_release'].includes(method)){blocked.push(method);return route.abort('blockedbyclient');}
    if(method==='logs'){
     reads++;
     if(fixture){const body=failure?{ok:false,error:'模拟日志读取失败'}:{ok:true,result:snapshot()};if(delay)await new Promise(r=>setTimeout(r,delay));
      return route.fulfill({contentType:'application/json',body:JSON.stringify({jsonrpc:'2.0',id:call.id,result:[0,body]})});}
    }
   }
   return route.continue();
  });
  await page.goto(new URL('/cgi-bin/luci/admin/services/fii',process.env.FII_ROUTER_URL).href);
  await page.locator('input[type=password]').fill(process.env.FII_ROUTER_PASSWORD);
  await page.locator('button[type=submit],input[type=submit]').first().click();
  const nav=page.locator('.fii-tabs');await nav.getByRole('button',{name:'日志',exact:true}).waitFor({timeout:60000});
  assert.equal(reads,0);await page.getByRole('button',{name:'运行日志',exact:true}).click();assert.equal(new URL(page.url()).hash,'#logs');
  const panel=page.locator('.fii-logs-panel'),viewport=panel.locator('.fii-log-viewport');
  async function refreshNow(){await panel.getByRole('button',{name:'刷新日志',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.fii-logs-panel button[aria-busy]'));}
  await panel.getByRole('status').filter({hasText:'每 3 秒刷新'}).waitFor();
  await panel.getByRole('button',{name:'暂停刷新',exact:true}).click();
  await panel.getByRole('status').filter({hasText:'已暂停'}).waitFor();
  if(fixture){
   assert.equal(await panel.locator('.fii-log-row').count(),183);assert.equal(await panel.locator('img').count(),0);
   assert.equal(await panel.locator('.fii-log-row.is-error').count(),46);
   assert(await viewport.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<24));
   await panel.getByRole('button',{name:'应用日志',exact:true}).click();assert.equal(await panel.locator('.fii-log-row').count(),2);
   await panel.getByRole('button',{name:'内核日志',exact:true}).click();assert.equal(await panel.locator('.fii-log-row').count(),181);
   await panel.getByRole('combobox',{name:'日志级别'}).selectOption('error');assert.equal(await panel.locator('.fii-log-row').count(),46);
   const search=panel.getByRole('searchbox'),before=reads;await search.fill('JSON 格式');assert.equal(await panel.locator('.fii-log-row').count(),1);assert.equal(reads,before);
   const pending=page.waitForEvent('download');await panel.getByRole('button',{name:'导出当前结果'}).click();
   const downloaded=await pending;const content=await fs.readFile(await downloaded.path(),'utf8');assert(content.includes('JSON 格式错误记录'));assert(!content.includes('配置检查完成'));
   await search.fill('无匹配条件');await panel.getByText('没有符合筛选条件的日志',{exact:true}).waitFor();assert(await panel.getByRole('button',{name:'导出当前结果'}).isDisabled());
   await search.fill('');await panel.getByRole('combobox',{name:'日志级别'}).selectOption('all');await panel.getByRole('button',{name:'全部日志',exact:true}).click();
   await panel.getByRole('checkbox',{name:'跟随最新',exact:true}).uncheck();await viewport.evaluate(el=>el.scrollTop=900);
   await page.waitForTimeout(100);const scrollBefore=await viewport.evaluate(el=>el.scrollTop);
   await refreshNow();assert.equal(await viewport.evaluate(el=>el.scrollTop),scrollBefore);
   sequence+=4;await refreshNow();
   const anchor=await viewport.evaluate(el=>{const row=[...el.querySelectorAll('.fii-log-row')].find(r=>r.offsetTop+r.offsetHeight>el.scrollTop);return {key:row.logKey,offset:el.scrollTop-row.offsetTop};});
   sequence+=4;await refreshNow();
   const anchorAfter=await viewport.evaluate(el=>{const row=[...el.querySelectorAll('.fii-log-row')].find(r=>r.offsetTop+r.offsetHeight>el.scrollTop);return {key:row.logKey,offset:el.scrollTop-row.offsetTop};});assert.deepEqual(anchorAfter,anchor);
   await panel.getByRole('button',{name:'跳到最新',exact:true}).click();assert(await panel.getByRole('checkbox',{name:'跟随最新'}).isChecked());
   failure=true;await refreshNow();await panel.getByRole('alert').filter({hasText:'模拟日志读取失败'}).waitFor();assert.equal(await panel.locator('.fii-log-row').count(),183);
   failure=false;await refreshNow();assert(await panel.getByRole('alert').isHidden());
   const paused=reads;await page.waitForTimeout(3300);assert.equal(reads,paused);
   await panel.getByRole('button',{name:'继续刷新',exact:true}).click();const started=reads;sequence+=4;
   await page.waitForFunction(()=>document.querySelector('.fii-log-message')?.textContent==='配置检查完成');
   await page.waitForTimeout(3300);assert(reads>started);
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
   await panel.getByRole('status').filter({hasText:'页面隐藏，已暂停'}).waitFor();
   const hiddenReads=reads;await page.waitForTimeout(3300);assert.equal(reads,hiddenReads);
   await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
   await panel.getByRole('status').filter({hasText:'每 3 秒刷新'}).waitFor();
   await panel.getByRole('button',{name:'暂停刷新',exact:true}).click();
   // Pause while an automatic read is in flight: its reply must not change the view.
   delay=700;const frozen=await panel.locator('.fii-log-lines').innerText();sequence+=4;
   await panel.getByRole('button',{name:'继续刷新',exact:true}).click();
   await panel.getByRole('button',{name:'暂停刷新',exact:true}).click();await page.waitForTimeout(900);
   assert.equal(await panel.locator('.fii-log-lines').innerText(),frozen);delay=0;
  }
  await page.addStyleTag({content:'.main,.main-right{overflow:visible!important;height:auto!important}'});await page.mouse.move(0,0);
  const tag=fixture?'fixture':candidate?'candidate':'router';
  if(!fixture)assert((await page.locator('.fii-header-actions').innerText()).includes('Mihomo 已停止'));
  await panel.screenshot({path:`build/performance/logs-${tag}-desktop.png`});
  for(const width of [805,390]){
   await page.setViewportSize({width,height:900});await panel.scrollIntoViewIfNeeded();
   const layout=await panel.evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));assert(layout.scroll<=layout.client+1);
   await panel.screenshot({path:`build/performance/logs-${tag}-${width}.png`});
  }
  await panel.getByRole('checkbox',{name:'自动换行',exact:true}).uncheck();assert(await viewport.evaluate(el=>el.classList.contains('nowrap')));
  await panel.getByRole('checkbox',{name:'自动换行',exact:true}).check();
  await page.emulateMedia({colorScheme:'dark'});await page.waitForTimeout(450);await panel.screenshot({path:`build/performance/logs-${tag}-dark.png`});
  if(fixture){delay=700;await panel.getByRole('button',{name:'继续刷新',exact:true}).click({noWaitAfter:true});}
  await nav.getByRole('button',{name:'概览',exact:true}).click();const exitReads=reads;await page.waitForTimeout(3500);assert.equal(reads,exitReads);
  assert.equal(blocked.length,0);assert.deepEqual(errors,[]);
  const report={fixture,candidate,reads,blocked,errors,methods,checks:fixture?['no overview log polling','source/level/literal filtering','escaped log text','filtered export','pause/resume including in-flight reply','background visibility suspension','unchanged scroll','history anchor during tail rollover','refresh error retention/retry','mobile/desktop/dark layouts','wrap toggle','leave-page cleanup']:['installed readonly logs','no overview log polling','pause','mobile/desktop/dark layouts','wrap toggle','leave-page cleanup'],coreStarted:false};
  await fs.writeFile(`build/performance/logs-${tag}.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  await page.unrouteAll({behavior:'ignoreErrors'});
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack||e);process.exit(1);});
