// Isolated loopback browser fixture. No router access or real resource updates.
const {chromium} = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'build/performance');

(async () => {
    await fs.mkdir(output, {recursive:true});
    let running = true, readFailure = false, ruleFailure = false, updateFailure = false, updates = [], reads = 0, polls = 0, current = 'HK';
    const methods = [], errors = [];
    const status = () => ({running, coreInstalled:true, coreVersion:'fixture', activeOutputId:'output', appliedRevision:1});
    const resourceView = () => ({running, source:running ? 'runtime' : 'snapshot', geodataMode:true,
        geoAutoUpdate:true, geoUpdateInterval:24,
        geo:['geosite','geoip','asn','mmdb'].map((id, i) => ({id, name:id.toUpperCase(), file:id + '.dat',
            exists:i < 2, size:i < 2 ? 2048 : null, modifiedAt:i < 2 ? 1792440000 : null,
            url:i === 0 ? 'https://example.com/' + 'long-path/'.repeat(10) + 'geosite.dat' : ''})),
        rulesError:ruleFailure ? '模拟控制器错误' : '',
        rulesets:ruleFailure ? [] : [
            {name:'Live / 规则 <img src=x onerror=alert(1)>',vehicleType:'HTTP',behavior:'Domain',format:'mrs',ruleCount:42,updatedAt:'2026-10-20T00:00:00Z',canUpdate:running},
            {name:'local',vehicleType:'File',behavior:'Classical',format:'yaml',ruleCount:7,updatedAt:'0001-01-01T00:00:00Z',canUpdate:running},
            {name:'inline',vehicleType:'Inline',behavior:'Domain',format:'text',ruleCount:1,canUpdate:false}
        ]});
    const assets = new Map([
        ['/tools/preview.html', ['tools/preview.html','text/html; charset=utf-8']],
        ['/luci-app-fii/htdocs/luci-static/resources/view/fii/app.js', ['luci-app-fii/htdocs/luci-static/resources/view/fii/app.js','application/javascript']],
        ['/luci-app-fii/htdocs/luci-static/resources/fii/style.css', ['luci-app-fii/htdocs/luci-static/resources/fii/style.css','text/css']]
    ]);
    const server = http.createServer(async (req, res) => {
        try {
            const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
            if (pathname !== '/api') {
                const asset = assets.get(pathname);
                if (!asset) {res.writeHead(404);res.end();return;}
                res.writeHead(200, {'Content-Type':asset[1]});res.end(await fs.readFile(path.join(root, asset[0])));return;
            }
            let raw = '';for await (const chunk of req) raw += chunk;
            const {method, payload} = JSON.parse(raw);methods.push(method);
            let result;
            if (method === 'get') result = {data:{revision:1,settings:{},sources:[],overrides:[],outputs:[]}};
            else if (method === 'status') result = status();
            else if (method === 'network') result = {};
            else if (method === 'groups') result = {running,source:running ? 'runtime' : 'snapshot',
                groups:[{name:'PROXY',type:'Selector',now:running ? current : '',all:['HK','JP']}],nodes:{HK:{type:'ss',delay:42},JP:{type:'ss',delay:80}}};
            else if (method === 'select') {current=payload.name;result={message:'已切换'};}
            else if (method === 'resources') {reads++;if (readFailure) throw Error('模拟资源读取失败');result=resourceView();}
            else if (method === 'resource_update') {updates.push(payload);polls=0;result={jobId:'a'.repeat(32)};}
            else if (method === 'job') {
                await new Promise(resolve => setTimeout(resolve, 250));
                result = ++polls < 3 ? {done:false,progress:{message:'正在由内核更新资源…'},pollAfterMs:100} :
                    updateFailure ? {done:true,ok:false,error:'HTTP 503'} : {done:true,ok:true,result:{message:'资源更新完成'}};
            } else throw Error('Unexpected fixture RPC: ' + method);
            res.writeHead(200, {'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,result}));
        } catch (error) {res.writeHead(200, {'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:error.message}));}
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({channel:'chrome',headless:true});
        const page = await browser.newPage({viewport:{width:1440,height:1100}});
        page.on('pageerror', error => errors.push(error.message));
        await page.goto('http://127.0.0.1:' + server.address().port + '/tools/preview.html');
        const nav = page.locator('.fii-tabs');
        await nav.getByRole('button', {name:'控制面板',exact:true}).waitFor();
        assert.equal(reads, 0);
        await nav.getByRole('button', {name:'控制面板',exact:true}).click();
        const panel = page.locator('.fii-resources');
        await panel.getByText('GEOSITE', {exact:true}).waitFor();
        assert.equal(await panel.locator('.fii-resource-card').count(), 4);
        assert.equal(await panel.locator('.fii-resource-row').count(), 3);
        assert.equal(await panel.locator('img').count(), 0);
        assert(await panel.locator('.fii-resource-row').nth(2).getByRole('button').isDisabled());
        assert.equal(new URL(page.url()).hash, '#proxies');
        await page.getByRole('button',{name:'查看代理组 PROXY'}).click();
        await page.locator('button[data-node="JP"]').click();
        await page.waitForFunction(() => document.querySelector('button[data-node="JP"]')?.classList.contains('is-current'));
        assert.equal(current, 'JP');
        await panel.locator('.fii-resource-row').first().getByRole('button').click();
        await panel.getByText('正在由内核更新资源…',{exact:true}).waitFor();
        assert(await panel.getByRole('button',{name:'更新 GEO 数据',exact:true}).isDisabled());
        assert(await panel.locator('.fii-resource-row').nth(1).getByRole('button').isDisabled());
        await nav.getByRole('button',{name:'概览',exact:true}).click();
        await nav.getByRole('button',{name:'控制面板',exact:true}).click();
        await panel.getByText('资源更新完成',{exact:true}).waitFor();
        assert.equal(updates.length, 1);
        assert.equal(updates[0].name, 'Live / 规则 <img src=x onerror=alert(1)>');
        await panel.getByRole('button',{name:'更新 GEO 数据',exact:true}).click();
        await panel.getByText('正在由内核更新资源…',{exact:true}).waitFor();
        running=false;
        await panel.getByText('资源更新完成',{exact:true}).waitFor();
        assert(await panel.getByRole('button',{name:'更新 GEO 数据',exact:true}).isDisabled());
        assert((await panel.innerText()).includes('配置快照 · 内核已停止'));
        assert.equal(updates[1].kind, 'geo');
        await page.waitForFunction(() => [...document.querySelectorAll('.fii-resource-row button')].every(button => button.disabled));
        running=true;
        await panel.getByRole('button',{name:'刷新资源状态'}).click();
        await page.waitForFunction(() => !document.querySelector('.fii-resource-row button').disabled);
        updateFailure=true;
        await panel.getByRole('button',{name:'重新加载规则资源 local'}).click();
        await panel.getByText(/更新失败：HTTP 503/).waitFor();
        assert.equal(updates.length, 3);
        assert.equal(updates[2].name, 'local');
        readFailure=true;
        await panel.getByRole('button',{name:'刷新资源状态'}).click();
        await panel.getByText(/资源状态读取失败：模拟资源读取失败/).waitFor();
        assert(await panel.getByRole('button',{name:'更新 GEO 数据',exact:true}).isDisabled());
        readFailure=false;ruleFailure=true;
        await panel.getByRole('button',{name:'刷新资源状态'}).click();
        await panel.getByRole('alert').filter({hasText:'模拟控制器错误'}).waitFor();
        assert.equal(await panel.locator('.fii-resource-card').count(), 4);
        assert.equal(await panel.locator('.fii-resource-row').count(), 0);
        ruleFailure=false;
        await panel.getByRole('button',{name:'刷新资源状态'}).click();
        await panel.locator('.fii-resource-row').first().waitFor();
        await page.screenshot({path:path.join(output,'control-panel-desktop.png'),fullPage:true});
        await page.setViewportSize({width:390,height:844});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        await page.screenshot({path:path.join(output,'control-panel-390.png'),fullPage:true});
        await page.emulateMedia({colorScheme:'dark'});
        await page.screenshot({path:path.join(output,'control-panel-dark.png'),fullPage:true});
        assert.equal(errors.length, 0, errors.join('\n'));
        await nav.getByRole('button',{name:'概览',exact:true}).click();
        const before = reads;
        await page.evaluate(() => window.fiiApp.drawStatus());
        assert.equal(reads, before);
        assert(await page.evaluate(() => window.fiiApp.refreshResources === null));
        const result = {verified:true,updates,resourceReads:reads,methods:[...new Set(methods)],browserErrors:errors,
            checks:['tab rename','proxy selection','GEO metadata','runtime rulesets','HTML escaping','inline readonly','background progress',
                'duplicate update prevention','tab exit during update','stopped update disabled','error retry','independent GEO/rules errors','390px and dark layout','listener cleanup']};
        await fs.writeFile(path.join(output,'control-panel-ui.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify(result,null,2));
    } finally {if (browser) await browser.close();await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error);process.exitCode=1;});
