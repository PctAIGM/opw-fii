// Real LuCI integration checks. Run only against the disposable QEMU VM.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
    const output = path.resolve(__dirname, '../build/vm');
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const results = [];
    let original, initialStatus;
    async function screenshot(name) {
        await page.locator('.fii').screenshot({path:path.join(output,name), animations:'disabled',
            style:'.main { height:auto!important; max-height:none!important; overflow:visible!important; } .main-right { height:auto!important; overflow:visible!important; }'});
    }
    async function rpc(method, payload = {}) {
        const response = await page.evaluate(async ({method, payload}) => {
            const r = await L.require('rpc');
            const result = await r.declare({object:'luci.fii',method,params:['payload'],expect:{'':{}}})(JSON.stringify(payload));
            if (!result.ok) throw new Error(result.error);
            return result.result;
        }, {method, payload});
        if (response.jobId) {
            for (let attempt=0; attempt<180; attempt++) {
                await new Promise(resolve => setTimeout(resolve, 250));
                const job = await rpc('job', {id:response.jobId});
                if (job.done) {
                    if (!job.ok) throw new Error(job.error);
                    return job.result;
                }
            }
            throw new Error('Timed out waiting for ' + method);
        }
        return response;
    }
    async function result(name, fn) {
        await fn(); results.push({name, passed:true}); console.log(name);
    }
    async function waitApplied(before) {
        await page.waitForFunction(() => !Array.from(document.querySelectorAll('.fii-settings-save button')).some(x => x.disabled), null, {timeout:45000});
        const data = (await rpc('get')).data;
        const status = await rpc('status');
        assert.ok(data.revision > before);
        assert.equal(status.appliedRevision, data.revision);
        assert.equal(status.running, true);
        return {data, status};
    }
    try {
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii?ui-check=7#settings');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD || 'fii-test');
        await page.getByRole('button', {name:'登录', exact:true}).click();
        await page.getByRole('button', {name:'路由器设置', exact:true}).waitFor();
        await page.getByRole('button', {name:'路由器设置', exact:true}).click();
        await page.getByRole('button', {name:'代理设置', exact:true}).waitFor();
        original = (await rpc('get')).data;
        initialStatus = await rpc('status');
        await result('checkboxes reflect persisted values', async () => {
            assert.equal(await page.getByRole('checkbox', {name:'代理 IPv6 流量', exact:true}).isChecked(), original.settings.ipv6);
            assert.equal(await page.getByRole('checkbox', {name:'代理 IPv4 流量', exact:true}).isChecked(), original.settings.ipv4Proxy);
            await page.getByRole('button', {name:'本机代理', exact:true}).click();
            assert.equal(await page.getByRole('checkbox', {name:'代理路由器本机', exact:true}).isChecked(), original.settings.routerProxy);
        });
        await result('save and apply router proxy from LuCI', async () => {
            await page.getByRole('checkbox', {name:'代理路由器本机', exact:true}).check();
            await page.getByRole('button', {name:'保存并应用', exact:true}).click();
            const saved = await waitApplied(original.revision);
            assert.equal(saved.data.settings.routerProxy, true);
            assert.equal(saved.status.routerProxy, true);
        });
        await result('independent TCP REDIRECT and UDP TUN apply from LuCI', async () => {
            await page.getByRole('button', {name:'代理设置', exact:true}).click();
            await page.getByRole('combobox', {name:'TCP 代理方式', exact:true}).selectOption('redirect');
            await page.getByRole('combobox', {name:'UDP 代理方式', exact:true}).selectOption('tun');
            const before = (await rpc('get')).data.revision;
            await page.getByRole('button', {name:'保存并应用', exact:true}).click();
            const saved = await waitApplied(before);
            assert.deepEqual(saved.status.protocols, {tcp:'redirect',udp:'tun'});
        });
        await result('ordered router rule edits and reorder persist independently for DNS', async () => {
            await page.getByRole('button', {name:'本机代理', exact:true}).click();
            const beforeRules = await page.locator('.fii-acl-rule:visible').count();
            await page.getByRole('button', {name:'添加规则', exact:true}).click();
            await page.getByRole('textbox', {name:'规则名称', exact:true}).fill('系统用户直连');
            await page.getByRole('checkbox', {name:'代理流量', exact:true}).uncheck();
            await page.getByRole('checkbox', {name:'root · UID 0', exact:true}).check();
            await page.getByRole('button', {name:'确定', exact:true}).click();
            for (let index=beforeRules+1; index>1; index--) await page.getByRole('button', {name:'上移规则 '+index, exact:true}).click();
            const before = (await rpc('get')).data.revision;
            await page.getByRole('button', {name:'保存并应用', exact:true}).click();
            const saved = await waitApplied(before);
            assert.equal(saved.data.settings.routerRules[0].proxy, false);
            assert.equal(saved.data.settings.routerRules[0].dns, true);
            assert.deepEqual(saved.data.settings.routerRules[0].users, ['0']);
            await screenshot('router-settings-router-acl.png');
        });
        await result('DNS mode keeps values across setting tabs', async () => {
            await page.getByRole('button', {name:'DNS', exact:true}).click();
            await page.getByRole('combobox', {name:'DNS 模式', exact:true}).selectOption('redir-host');
            assert.equal(await page.getByRole('group', {name:'Fake-IP 过滤域名', exact:true}).isVisible(), false);
            await page.getByRole('textbox', {name:'添加节点域名 DNS', exact:true}).fill('1.1.1.1');
            await page.getByRole('button', {name:'运行设置', exact:true}).click();
            await page.getByRole('button', {name:'DNS', exact:true}).click();
            assert.equal(await page.getByRole('textbox', {name:'添加节点域名 DNS', exact:true}).inputValue(), '1.1.1.1');
            const before = (await rpc('get')).data.revision;
            await page.getByRole('button', {name:'保存并应用', exact:true}).click();
            const saved = await waitApplied(before);
            assert.equal(saved.data.settings.dnsMode, 'redir-host');
            assert.deepEqual(saved.data.settings.proxyNameservers, ['1.1.1.1']);
        });
        await result('backend validation preserves state on invalid device address', async () => {
            await page.getByRole('button', {name:'局域网代理', exact:true}).click();
            await page.getByRole('button', {name:'添加规则', exact:true}).click();
            await page.getByRole('textbox', {name:'添加IP / IPv6 / 网段', exact:true}).fill('invalid-address');
            await page.getByRole('button', {name:'确定', exact:true}).click();
            const before = (await rpc('get')).data.revision;
            await page.getByRole('button', {name:'保存设置', exact:true}).click();
            await page.getByText('设备地址无效: invalid-address', {exact:true}).waitFor();
            assert.equal((await rpc('get')).data.revision, before);
            await page.locator('.fii-acl-rule:visible').last().getByRole('button', {name:'删除', exact:true}).click();
        });
        await result('bypass port presets DSCP marks and mainland switches persist', async () => {
            await page.getByRole('button', {name:'绕过设置', exact:true}).click();
            await page.getByRole('checkbox', {name:'绕过中国大陆 IPv4', exact:true}).check();
            await page.getByRole('checkbox', {name:'绕过中国大陆 IPv6', exact:true}).check();
            await page.getByRole('textbox', {name:'TCP 代理目标端口', exact:true}).fill('80 443 18080-18090');
            await page.getByRole('textbox', {name:'添加绕过 DSCP', exact:true}).fill('4');
            await page.getByRole('textbox', {name:'添加绕过 FWMark', exact:true}).fill('0x100/0xff00');
            const before = (await rpc('get')).data.revision;
            await page.getByRole('button', {name:'保存并应用', exact:true}).click();
            const saved = await waitApplied(before);
            assert.equal(saved.data.settings.bypassChina4, true);
            assert.deepEqual(saved.data.settings.bypassDscp, ['4']);
            assert.equal(saved.data.settings.tcpPorts, '80 443 18080-18090');
            await screenshot('router-settings-bypass.png');
        });
        await result('master switch disables protocol fields without losing modes', async () => {
            await page.getByRole('button', {name:'代理设置', exact:true}).click();
            await page.getByRole('checkbox', {name:'启用透明代理', exact:true}).uncheck();
            assert.equal(await page.getByRole('combobox', {name:'TCP 代理方式', exact:true}).isDisabled(), true);
            await page.getByRole('checkbox', {name:'启用透明代理', exact:true}).check();
            assert.equal(await page.getByRole('combobox', {name:'UDP 代理方式', exact:true}).inputValue(), 'tun');
        });
        for (const width of [1440, 375, 844]) {
            await page.setViewportSize({width, height:width===844?390:1050});
            await page.emulateMedia({reducedMotion:'reduce'});
            await page.waitForTimeout(350);
            if (width !== 844) await screenshot(width===375?'router-settings-mobile.png':'router-settings-desktop.png');
            await result('layout without horizontal overflow at ' + width, async () => {
                if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) console.log(await page.evaluate(() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,items:Array.from(document.querySelectorAll('body *')).filter(x=>x.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(x).position!=='fixed').slice(0,12).map(x=>({tag:x.tagName,cls:x.className,width:x.getBoundingClientRect().width,right:x.getBoundingClientRect().right}))})));
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
                assert.equal(await page.evaluate(() => { const el = document.querySelector('.fii'); return el.scrollWidth > el.clientWidth; }), false);
            });
        }
        await page.setViewportSize({width:1440,height:1050});
        await page.getByRole('button', {name:'DNS', exact:true}).click();
        await screenshot('router-settings-dns.png');
        await page.emulateMedia({colorScheme:'dark'});
        await screenshot('router-settings-dark.png');
        await page.getByRole('button', {name:'本机代理', exact:true}).click();
        await screenshot('router-settings-router-dark.png');
        assert.deepEqual(errors, []);
        results.push({name:'no JavaScript page errors',passed:true});
    } catch (error) {
        console.log(await page.locator('body').innerText());
        await page.screenshot({path:path.join(output,'router-settings-ui-failure.png')});
        throw error;
    } finally {
        if (original) {
            const current = (await rpc('get')).data;
            current.settings = original.settings;
            await rpc('save', {data:current, revision:current.revision});
            if (initialStatus.activeOutputId && initialStatus.coreInstalled) await rpc('apply', {id:initialStatus.activeOutputId});
            if (!initialStatus.running) await rpc('service', {action:'stop'});
        }
        await fs.writeFile(path.join(output,'router-settings-ui-results.json'), JSON.stringify(results,null,2));
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode=1; });
