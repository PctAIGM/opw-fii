// Read-only router checks; --fixture consumes every select in the browser.
// --candidate serves local assets against the real stopped-router snapshot.
// Set FII_ROUTER_URL, FII_ROUTER_PROXY, FII_ROUTER_PASSWORD.
const {chromium}=require('playwright'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const fixture=process.argv.includes('--fixture'),candidate=fixture||process.argv.includes('--candidate');
(async()=>{
 await fs.mkdir('build/performance',{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true,proxy:{server:process.env.FII_ROUTER_PROXY}});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],methods=[],blocked=[];
  let groupFailure=false,selectFailure=false,selects=0,groupReads=0;
  const names=Array.from({length:512},(_,i)=>'香港 · 高速线路 '+String(i+1).padStart(3,'0')+(i===0?' · <测试> & 安全显示':''));
  const data={running:true,source:'runtime',groups:[
   {name:'节点选择',type:'Selector',now:names[0],all:names},
   {name:'自动测速 · 日本与新加坡',type:'URLTest',now:names[1],fixed:'',all:names.slice(0,12)},
   {name:'故障转移',type:'Fallback',now:'',fixed:'',all:names.slice(0,5),dynamic:true},
   {name:'全球服务 · 一个很长的代理组名称用于验证手机显示',type:'LoadBalance',now:'',all:names.slice(0,6)}
  ],nodes:Object.fromEntries(names.map((name,i)=>[name,{type:i%2?'Vless':'Shadowsocks',alive:i===4?false:null,delay:i===4?0:i===5?null:35+i*3,testedAt:'2026-10-20T08:00:00+08:00'}]))};
  page.on('pageerror',e=>errors.push(e.message));
  if(candidate){
   await page.route(/\/resources\/view\/fii\/app\.js(?:\?|$)/,r=>r.fulfill({path:'luci-app-fii/htdocs/luci-static/resources/view/fii/app.js',contentType:'application/javascript'}));
   await page.route(/\/resources\/fii\/style\.css(?:\?|$)/,r=>r.fulfill({path:'luci-app-fii/htdocs/luci-static/resources/fii/style.css',contentType:'text/css'}));
  }
  await page.route(/\/ubus\/?/,async route=>{
   const request=route.request().postDataJSON();
   for(const call of Array.isArray(request)?request:[request]){
    if(call?.params?.[1]!=='luci.fii')continue;
    const method=call.params[2];methods.push(method);
    const respond=(result,ok=true)=>route.fulfill({contentType:'application/json',body:JSON.stringify({jsonrpc:'2.0',id:call.id,result:[0,ok?{ok:true,result}:{ok:false,error:result}]})});
    if(method==='groups')groupReads++;
    if(fixture&&method==='groups')return groupFailure?respond('模拟读取失败',false):respond(data);
    if(fixture&&method==='resources')return respond({running:true,source:'runtime',geo:[],rulesets:[],rulesError:''});
    if(fixture&&method==='select'){
     selects++;if(selectFailure)return respond('模拟切换失败',false);
     const payload=JSON.parse(call.params[3].payload),group=data.groups.find(g=>g.name===payload.group);
     if(payload.action==='unpin')group.fixed='';
     else{group.now=payload.name;if(['URLTest','Fallback'].includes(group.type))group.fixed=payload.name;}
     return respond({});
    }
    if(!['get','status','network','core_info','groups','resources','job','transfer_read','transfer_release'].includes(method)){blocked.push(method);return route.abort('blockedbyclient');}
    if(fixture&&method==='status'){
     const response=await route.fetch(),json=await response.json();Object.assign(json.result[1].result,{running:true,activeOutputId:'1',appliedRevision:33});
     return route.fulfill({response,body:JSON.stringify(json)});
    }
   }
   return route.continue();
  });
  await page.goto(new URL('/cgi-bin/luci/admin/services/fii',process.env.FII_ROUTER_URL).href);
  await page.locator('input[type=password]').fill(process.env.FII_ROUTER_PASSWORD);
  await page.locator('button[type=submit],input[type=submit]').first().click();
  const nav=page.locator('.fii-tabs');await nav.getByRole('button',{name:'控制面板',exact:true}).waitFor({timeout:60000});
  const styleHref=await page.locator('#fii-style').getAttribute('href');
  assert.match(styleHref,/\/fii\/style\.css\?v=[0-9a-f]{16}$/);
  assert.equal(await page.locator('#fii-style').count(),1);
  assert.equal(await page.locator('.fii-proxies-panel').count(),0);assert.equal(groupReads,0);
  assert.equal(await page.getByText('三步开始',{exact:true}).count(),0);
  await nav.getByRole('button',{name:'控制面板',exact:true}).click();assert.equal(new URL(page.url()).hash,'#proxies');
  const panel=page.locator('.fii-proxies-panel');await panel.waitFor();
  await page.waitForFunction(()=>document.querySelector('.fii-proxies-panel')?.getAttribute('aria-busy')===null,{},{timeout:60000});
  const first=panel.locator('[data-group="节点选择"]');
  if(fixture){
   assert.equal(await panel.locator('.fii-proxy-node').count(),0);
   await first.locator('.fii-proxy-group-toggle').click();await first.locator('.fii-proxy-node').first().waitFor();
   assert(await first.locator('.fii-proxy-node').count()<60);assert.equal(await first.locator('[aria-pressed="true"]').count(),1);
   await first.locator('.fii-proxy-viewport').evaluate(el=>el.scrollTop=el.scrollHeight);
   await first.locator('[data-node="'+names[511]+'"]').waitFor();assert(await first.locator('.fii-proxy-node').count()<60);
   const search=panel.getByRole('searchbox'),reads=groupReads;
   await search.fill('512');await first.locator('[data-node]').waitFor();assert.equal(await first.locator('.fii-proxy-node').count(),1);assert.equal(groupReads,reads);
   selectFailure=true;await first.locator('button[data-node]').click();await first.getByText('模拟切换失败',{exact:true}).waitFor();
   assert.equal(data.groups[0].now,names[0]);assert.equal(await first.locator('button[data-node]:disabled').count(),0);
   selectFailure=false;await first.locator('button[data-node]').click();await first.locator('[aria-pressed="true"]').waitFor();
   assert.equal(data.groups[0].now,names[511]);assert.equal(await search.inputValue(),'512');
   await page.waitForFunction(()=>document.activeElement?.dataset.node?.startsWith('香港 · 高速线路 512'));
   await search.fill('');await panel.getByRole('combobox',{name:'节点排序',exact:true}).selectOption('latency');
   await first.locator('button[data-node]').first().waitFor();assert.equal(await first.locator('button[data-node]').first().getAttribute('data-node'),names[0]);
   await first.locator('button[data-node]').first().focus();await page.keyboard.press('End');
   await page.waitForFunction(()=>document.activeElement?.dataset.node?.includes('006'));
   await panel.getByRole('combobox',{name:'节点排序',exact:true}).selectOption('config');
   const automatic=panel.locator('[data-group="自动测速 · 日本与新加坡"]');await automatic.locator('.fii-proxy-group-toggle').click();
   await automatic.locator('button[data-node]').first().waitFor();
   await automatic.locator('button[data-node='+JSON.stringify(names[1])+']').click();
   await automatic.locator('.is-pinned').waitFor();assert.equal(data.groups[1].fixed,names[1]);
   const unpin=automatic.getByRole('button',{name:'取消钉住 自动测速 · 日本与新加坡',exact:true});
   selectFailure=true;await unpin.click();await automatic.getByText('模拟切换失败',{exact:true}).waitFor();assert.equal(data.groups[1].fixed,names[1]);
   selectFailure=false;await unpin.click();await unpin.waitFor({state:'detached'});assert.equal(data.groups[1].fixed,'');
   await automatic.locator('button[data-node='+JSON.stringify(names[2])+']').click();await automatic.locator('.is-pinned').waitFor();
   data.groups[1].now=names[0];await panel.getByRole('button',{name:'刷新代理组',exact:true}).click();
   await automatic.locator('.is-current[data-node='+JSON.stringify(names[0])+']').waitFor();
   assert.equal(await automatic.locator('.is-pinned').getAttribute('data-node'),names[2]);
   assert((await automatic.locator('.fii-proxy-current').innerText()).includes('当前'));
   const balance=panel.locator('[data-group]').filter({has:page.getByRole('button',{name:'查看代理组 全球服务 · 一个很长的代理组名称用于验证手机显示',exact:true})});
   await balance.locator('.fii-proxy-group-toggle').click();await balance.locator('.fii-proxy-node').first().waitFor();
   assert.equal(await balance.locator('button[data-node]').count(),0);
   groupFailure=true;await panel.getByRole('button',{name:'刷新代理组',exact:true}).click();
   await panel.getByText('读取失败：模拟读取失败',{exact:true}).waitFor();assert.equal(await panel.locator('[data-group]').count(),4);
   assert.equal(await panel.locator('button[data-node]:enabled').count(),0);
   assert(await unpin.isDisabled());
   groupFailure=false;await panel.getByRole('button',{name:'刷新代理组',exact:true}).click();await panel.getByText('运行中 · 最近检测记录',{exact:true}).waitFor();
   await first.locator('button[data-node]:enabled').first().waitFor();
  }else{
   await panel.getByText('配置预览 · 内核已停止',{exact:true}).waitFor();assert(await panel.locator('[data-group]').count()>0);
   await panel.locator('[data-group]').first().locator('.fii-proxy-group-toggle').click();await panel.locator('.fii-proxy-node').first().waitFor();
   assert.equal(await panel.locator('button[data-node]').count(),0);assert.equal(await panel.locator('.is-current').count(),0);
  }
  await page.addStyleTag({content:'.main,.main-right{overflow:visible!important;height:auto!important}'});await page.mouse.move(0,0);
  const tag=fixture?'fixture':candidate?'candidate':'router';await panel.screenshot({path:`build/performance/proxies-${tag}-desktop.png`});
  await page.setViewportSize({width:805,height:900});
  const narrowDesktopLayout=await panel.evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth,groupsDisplay:getComputedStyle(el.querySelector('.fii-proxy-groups')).display}));
  assert(narrowDesktopLayout.scroll<=narrowDesktopLayout.client+1);assert.equal(narrowDesktopLayout.groupsDisplay,'grid');
  await panel.screenshot({path:`build/performance/proxies-${tag}-805.png`});
  await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();await page.waitForTimeout(100);
  assert.equal(await panel.locator('.fii-proxy-inline-body:visible').count(),0);
  const layout=await panel.evaluate(el=>({width:el.getBoundingClientRect().width,scroll:el.scrollWidth,client:el.clientWidth,height:el.getBoundingClientRect().height}));
  assert(layout.scroll<=layout.client+1,JSON.stringify(layout));await panel.screenshot({path:`build/performance/proxies-${tag}-mobile.png`});
  await panel.locator('[data-group]').first().locator('.fii-proxy-group-toggle').click();
  const modal=page.locator('.fii-proxy-modal');await modal.waitFor();await modal.locator('.fii-proxy-node').first().waitFor();
  const modalLayout=await modal.evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth,nodes:[...el.querySelectorAll('.fii-proxy-node')].map(n=>({width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height}))}));
  assert(modalLayout.scroll<=modalLayout.client+1,JSON.stringify(modalLayout));for(const box of modalLayout.nodes)assert(box.width>=44&&box.height>=44);
  await page.waitForTimeout(350);
  if(fixture){
   await modal.locator('[aria-pressed="true"]').waitFor();
   const previous=data.groups[0].now,target=await modal.evaluate(el=>{
    const box=el.querySelector('.fii-proxy-viewport').getBoundingClientRect();
    return [...el.querySelectorAll('button[data-node][aria-pressed="false"]')].find(node=>{const r=node.getBoundingClientRect();return r.top>=box.top&&r.bottom<=box.bottom}).dataset.node;
   });
   const targetButton=modal.locator('button[data-node='+JSON.stringify(target)+']');
   selectFailure=true;await targetButton.click();
   await modal.getByText('模拟切换失败',{exact:true}).waitFor();assert.equal(data.groups[0].now,previous);
   selectFailure=false;await targetButton.click();
   await modal.locator('button[data-node='+JSON.stringify(target)+'][aria-pressed="true"]').waitFor();assert.equal(data.groups[0].now,target);
  }
  await modal.screenshot({path:`build/performance/proxies-${tag}-mobile-nodes.png`});await modal.getByRole('button',{name:'关闭',exact:true}).click();
  await page.waitForFunction(()=>!document.body.classList.contains('modal-overlay-active'));
  await panel.locator('[data-group]').first().locator('.fii-proxy-group-toggle').click();await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.body.classList.contains('modal-overlay-active'));
  if(fixture){
   await panel.locator('[data-group="故障转移"]').locator('.fii-proxy-group-toggle').click();
   const fallback=page.locator('.fii-proxy-modal');await fallback.locator('button[data-node]').first().waitFor();
   await fallback.locator('button[data-node='+JSON.stringify(names[1])+']').click();
   await fallback.locator('.is-pinned').waitFor();assert.equal(data.groups[2].fixed,names[1]);
   await fallback.screenshot({path:'build/performance/proxies-fixture-pin-mobile.png'});
   await fallback.getByRole('button',{name:'取消钉住 故障转移',exact:true}).click();
   await fallback.locator('.is-pinned').waitFor({state:'detached'});assert.equal(data.groups[2].fixed,'');
   await fallback.getByRole('button',{name:'关闭',exact:true}).click();
  }
  await page.emulateMedia({colorScheme:'dark'});await panel.screenshot({path:`build/performance/proxies-${tag}-dark.png`});
  await nav.getByRole('button',{name:'概览',exact:true}).click();assert.equal(await panel.count(),0);
  const previousReads=groupReads;await page.waitForTimeout(100);assert.equal(groupReads,previousReads);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
  await page.goto(new URL('/cgi-bin/luci/admin/services/fii#proxies',process.env.FII_ROUTER_URL).href);await page.reload();
  await panel.waitFor({timeout:60000});await panel.locator('[data-group]').first().waitFor({timeout:60000});
  assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
  const result={passed:true,mode:tag,styleHref,overviewHasNoProxyPanel:true,overviewGroupReads:0,virtualized512Nodes:fixture,fixtureSelects:selects,groupReads,narrowDesktopLayout,layout,modalLayout,errors,blocked,methods:[...new Set(methods)]};
  await fs.writeFile(`build/performance/proxies-${tag}.json`,JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{for(const context of browser.contexts())for(const page of context.pages())await page.unrouteAll({behavior:'ignoreErrors'});await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
