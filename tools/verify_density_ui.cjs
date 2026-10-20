// Render every page against isolated data, compare with saved pre-change assets,
// and check narrow screens, touch targets, editors and proxy virtual scrolling.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),out=path.join(root,'build/ui-density');

(async()=>{
    await fs.mkdir(out,{recursive:true});
    // Snapshot old assets under build/ui-density before editing for a comparison;
    // a fresh checkout without that snapshot still validates the current UI.
    const hasBaseline=await fs.access(path.join(out,'app-before.js')).then(()=>true,()=>false);
    const data=JSON.parse(execFileSync(process.env.FII_PYTHON||'python',['-c',"import sys,json;sys.path.insert(0,'fii/files/usr/lib');from fii.model import defaults;print(json.dumps(defaults()))"],{cwd:root,encoding:'utf8'}));
    data.sources=Array.from({length:9},(_,i)=>({id:'s'+i,name:'订阅源 '+(i+1)+' · 香港日本新加坡',enabled:true,url:'https://example.com/sub',nodeCount:128,lastRefreshAt:1792440000,totalBytes:107374182400,downloadBytes:10737418240,expireAtSeconds:1795040000,cachedYaml:'proxies: []'}));
    data.overrides=Array.from({length:12},(_,i)=>({id:'o'+i,name:'覆写 '+(i+1)+' · 分流规则',enabled:true,global:i%2===0,type:'YAML',body:'rules: []',snippet:'rules: []'}));
    data.outputs=Array.from({length:8},(_,i)=>({id:'p'+i,name:'输出 '+(i+1)+' · 日常代理',enabled:true,sourceIds:['s0','s1'],overrideIds:['o1']}));
    let before=false;const errors=[],mutations=[];
    const names=Array.from({length:512},(_,i)=>'节点 '+String(i+1).padStart(3,'0')+' · 香港高速线路');
    const server=http.createServer(async(req,res)=>{
        try{
            const url=new URL(req.url,'http://127.0.0.1');
            res.setHeader('Cache-Control','no-store');
            if(url.pathname!=='/api'){
                const files={
                    '/tools/preview.html':['tools/preview.html','text/html; charset=utf-8'],
                    '/luci-app-fii/htdocs/luci-static/resources/view/fii/app.js':[before?'build/ui-density/app-before.js':'luci-app-fii/htdocs/luci-static/resources/view/fii/app.js','application/javascript'],
                    '/luci-app-fii/htdocs/luci-static/resources/fii/style.css':[before?'build/ui-density/style-before.css':'luci-app-fii/htdocs/luci-static/resources/fii/style.css','text/css']
                };
                const file=files[url.pathname];if(!file){res.writeHead(404);res.end();return;}
                res.setHeader('Content-Type',file[1]);res.end(await fs.readFile(path.join(root,file[0])));return;
            }
            let raw='';for await(const chunk of req)raw+=chunk;
            const {method,payload}=JSON.parse(raw);let result;
            if(method==='get'){
                result=payload.kind?{item:data[payload.kind].find(item=>item.id===payload.id),revision:data.revision}:{data};
                if(payload.sync)result.data=data;
            }
            else if(method==='status')result={running:true,coreInstalled:true,coreVersion:'v1.19.32',coreSelectedChannel:'release',activeOutputId:'p0',activeName:'日常代理',appliedRevision:data.revision,mode:'core'};
            else if(method==='network')result={devices:[{name:'br-lan',up:true,networks:['lan']}],hosts:[],users:[{id:'0',name:'root'}],groups:[],cgroups:[],cgroupV2:true};
            else if(method==='core_info')result={installed:true,channel:'release',selectedChannel:'release',version:'v1.19.32',versionText:'Mihomo v1.19.32 linux amd64',architecture:'x86_64',supported:true,updatedAt:1792440000};
            else if(method==='groups')result={running:true,source:'runtime',groups:Array.from({length:6},(_,i)=>({name:'代理组 '+(i+1),type:'Selector',now:names[0],all:names})),nodes:Object.fromEntries(names.map((name,i)=>[name,{type:'Shadowsocks',delay:40+i}]))};
            else if(method==='resources')result={running:true,source:'runtime',geodataMode:true,geoAutoUpdate:false,geoUpdateInterval:24,
                geo:['geosite','geoip','asn','mmdb'].map(id=>({id,name:id.toUpperCase(),file:id+'.dat',exists:true,size:5242880,modifiedAt:1792440000,url:'https://example.com/geodata/'+id})),
                rulesError:'',rulesets:Array.from({length:12},(_,i)=>({name:'规则集 '+(i+1)+' · 常用域名分流',vehicleType:'HTTP',behavior:'Domain',format:'mrs',ruleCount:1000+i*10,updatedAt:'2026-10-20T00:00:00Z',canUpdate:true}))};
            else if(method==='logs')result={text:Array.from({length:150},(_,i)=>'Tue Oct 20 12:00:00 2026 daemon.info mihomo[123]: level=info msg="连接记录 '+i+'"').join('\n'),prepareText:'',limit:500,truncated:false};
            else{mutations.push(method);throw Error('Writes disabled: '+method);}
            res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,result}));
        }catch(error){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:false,error:error.message}));}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    let browser;
    const base='http://127.0.0.1:'+server.address().port+'/tools/preview.html';
    const tabs=[['overview','概览'],['sources','订阅源'],['mihomo','Mihomo'],['overrides','覆写'],['outputs','输出配置'],['settings','路由器设置'],['proxies','控制面板'],['logs','日志']];
    const measurements={before:{},after:{}};
    async function settled(page,key){
        if(key==='mihomo')await page.getByText('Mihomo v1.19.32 linux amd64',{exact:true}).waitFor();
        if(key==='proxies')await page.getByText('规则集 12 · 常用域名分流',{exact:true}).waitFor();
        if(key==='logs')await page.locator('.fii-log-row').first().waitFor();
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    }
    async function layout(page){return page.evaluate(()=>{
        const body=document.querySelector('.fii-body'),first=body.querySelector('.fii-card,.fii-resource-row,.fii-log-row');
        const entries=[...body.querySelectorAll('.fii-grid>.fii-card,.fii-override-row,.fii-resource-row,.fii-log-row')];
        return {pageHeight:Math.round(document.querySelector('.fii').getBoundingClientRect().height),bodyTop:Math.round(body.getBoundingClientRect().top),
            font:getComputedStyle(document.querySelector('.fii')).fontSize,firstCardHeight:first?Math.round(first.getBoundingClientRect().height):null,
            visibleEntries:entries.filter(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}).length,
            overflow:document.documentElement.scrollWidth>innerWidth+1};
    });}
    try{
        browser=await chromium.launch({channel:'chrome',headless:true});
        for(const phase of hasBaseline?['before','after']:['after']){
            before=phase==='before';const page=await browser.newPage({viewport:{width:1440,height:900}});
            page.on('pageerror',error=>errors.push({phase,message:error.message}));await page.goto(base);
            await page.locator('.fii-tabs').waitFor();
            for(const [key,label]of tabs){
                await page.locator('.fii-tabs').getByRole('button',{name:label,exact:true}).click();await settled(page,key);
                await page.evaluate(()=>window.scrollTo(0,0));measurements[phase][key]=await layout(page);
                assert.equal(measurements[phase][key].overflow,false,phase+' '+key+' overflow');
                await page.screenshot({path:path.join(out,phase+'-'+key+'.png'),fullPage:true});
            }
            await page.close();
        }
        before=false;
        const page=await browser.newPage({viewport:{width:1440,height:900}});page.on('pageerror',error=>errors.push({phase:'responsive',message:error.message}));await page.goto(base);
        const nav=page.locator('.fii-tabs');await nav.waitFor();
        const responsive=[];
        for(const width of [1024,768,390]){
            await page.setViewportSize({width,height:900});
            for(const [key,label]of tabs){
                await nav.getByRole('button',{name:label,exact:true}).click();await settled(page,key);
                const result=await layout(page);assert.equal(result.overflow,false,width+' '+key+' overflow');
                responsive.push({width,page:key,...result});
            }
            await nav.getByRole('button',{name:'路由器设置',exact:true}).click();
            for(const button of await page.locator('.fii-settings-tabs button').all()){
                await button.click();assert.equal((await layout(page)).overflow,false,width+' settings subsection overflow');
            }
            await nav.getByRole('button',{name:'订阅源',exact:true}).click();await page.getByRole('button',{name:'添加订阅源',exact:true}).click();
            await page.locator('.fii-modal input').first().waitFor();
            assert(await page.locator('.fii-modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
            await page.locator('.fii-modal').getByRole('button',{name:'取消',exact:true}).click();
            await nav.getByRole('button',{name:'输出配置',exact:true}).click();await page.getByRole('button',{name:'创建输出',exact:true}).click();
            await page.locator('.fii-modal .fii-choices').first().waitFor();
            assert(await page.locator('.fii-modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
            await page.locator('.fii-modal').getByRole('button',{name:'取消',exact:true}).click();
            await nav.getByRole('button',{name:'覆写',exact:true}).click();
            await page.locator('.fii-override-row summary').first().click();await page.locator('.fii-override-row details[open] pre').first().waitFor();
            assert.equal((await layout(page)).overflow,false,width+' expanded override overflow');
        }
        await page.setViewportSize({width:1440,height:900});await nav.getByRole('button',{name:'订阅源',exact:true}).click();
        await page.locator('.fii').evaluate(el=>el.style.maxWidth='900px');
        assert.equal(await page.locator('.fii-sources-list').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),2);
        assert.equal((await layout(page)).overflow,false,'narrow LuCI content area overflow');
        await page.locator('.fii').evaluate(el=>el.style.maxWidth='');
        await page.setViewportSize({width:1440,height:900});await nav.getByRole('button',{name:'控制面板',exact:true}).click();await settled(page,'proxies');
        await page.getByRole('button',{name:'查看代理组 代理组 1',exact:true}).click();
        const viewport=page.locator('[data-group="代理组 1"] .fii-proxy-viewport');
        await viewport.locator('button[data-node]').first().waitFor();
        const geometry=await viewport.evaluate(el=>{const first=el.querySelector('button[data-node]'),nodes=[...el.querySelectorAll('button[data-node]')];return{tileHeight:first.getBoundingClientRect().height,nodeCount:nodes.length,overlap:nodes.some((n,i)=>i>0&&n.offsetTop>nodes[i-1].offsetTop&&n.offsetTop<nodes[i-1].offsetTop+nodes[i-1].offsetHeight)}});
        assert.equal(geometry.tileHeight,56);assert.equal(geometry.overlap,false);assert(geometry.nodeCount<100);
        await viewport.evaluate(el=>el.scrollTop=el.scrollHeight);await viewport.locator('[data-node="'+names[511]+'"]').waitFor();
        await viewport.locator('button[data-node]').last().focus();await page.keyboard.press('Home');await viewport.locator('[data-node="'+names[0]+'"]').waitFor();
        await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});
        await page.screenshot({path:path.join(out,'after-proxies-mobile-dark.png'),fullPage:true});
        const touch=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});await touch.goto(base);
        await touch.locator('.fii-tabs').getByRole('button',{name:'订阅源',exact:true}).click();
        const touchGeometry=await touch.locator('.fii-actions button').first().evaluate(el=>({height:el.getBoundingClientRect().height,width:el.getBoundingClientRect().width}));
        assert(touchGeometry.height>=44&&touchGeometry.width>=44);
        await touch.getByRole('button',{name:'添加订阅源',exact:true}).click();
        assert.equal(await touch.locator('.fii-modal input').first().evaluate(el=>getComputedStyle(el).fontSize),'16px');
        await touch.locator('.fii-modal').getByRole('button',{name:'取消',exact:true}).click();
        await touch.locator('.fii-tabs').getByRole('button',{name:'控制面板',exact:true}).click();await settled(touch,'proxies');
        await touch.getByRole('button',{name:'查看代理组 代理组 1',exact:true}).click();
        await touch.locator('.fii-proxy-modal button[data-node]').first().waitFor();
        assert.equal(await touch.locator('.fii-proxy-modal button[data-node]').first().evaluate(el=>el.getBoundingClientRect().height),56);
        assert(await touch.locator('.fii-proxy-modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
        assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
        const savings=hasBaseline?Object.fromEntries(tabs.map(([key])=>[key,Math.round((1-measurements.after[key].pageHeight/measurements.before[key].pageHeight)*100)])):null;
        const report={verified:true,viewport:'1440x900',measurements,heightReductionPercent:savings,responsive,proxyGeometry:geometry,touchGeometry,browserErrors:errors,mutations};
        await fs.writeFile(path.join(out,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({verified:true,measurements,heightReductionPercent:savings,proxyGeometry:geometry,touchGeometry,browserErrors:errors},null,2));
    }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
