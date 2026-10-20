// Local LuCI view + real Python persistence, with isolated DHCP/network fixtures.
// No physical-router requests or service actions.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises'), path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
    const output = path.resolve('build/access-control');
    await fs.mkdir(output, {recursive:true});
    const state = await fs.mkdtemp(path.join(output, 'state-'));
    const script = [
        'import os,sys',
        'from tools.preview import Handler, ThreadingHTTPServer',
        'os.environ["FII_ROOT"] = sys.argv[1]',
        'from fii import network',
        'network.choices = lambda: {"devices":[{"name":"br-lan","networks":["lan"],"up":True}],"hosts":[{"name":"测试手机","ip":"192.168.1.100","mac":"AA:BB:CC:DD:EE:FF"}],"users":[],"groups":[],"cgroups":[]}',
        'server = ThreadingHTTPServer(("127.0.0.1",8765),Handler)',
        'print("ready",flush=True)',
        'server.serve_forever()'
    ].join('\n');
    const server = spawn(process.env.FII_PYTHON || 'python', ['-u', '-c', script, state], {stdio:['ignore','pipe','pipe'], windowsHide:true});
    let diagnostic = '', browser;
    server.stderr.on('data', bytes => diagnostic += bytes.toString());
    try {
        await new Promise((resolve,reject) => {
            const timer = setTimeout(() => reject(Error('Preview startup timeout: '+diagnostic)),15000);
            server.once('error', reject);
            server.once('exit', code => {clearTimeout(timer);reject(Error('Preview exited '+code+': '+diagnostic));});
            server.stdout.on('data', bytes => {if (bytes.toString().includes('ready')) {clearTimeout(timer);resolve();}});
        });
        browser = await chromium.launch({channel:'chrome',headless:true});
        const page = await browser.newPage({viewport:{width:1440,height:1000}});
        const errors = [], results = [];
        page.on('pageerror', error => errors.push(error.message));
        async function rpc(method,payload={}) {
            const response = await page.request.post('http://127.0.0.1:8765/api',{data:{method,payload}});
            const body = await response.json(); assert.equal(body.ok,true,body.error); return body.result;
        }
        async function access() {
            await page.getByRole('button',{name:'路由器设置',exact:true}).click();
            await page.getByRole('button',{name:'访问控制',exact:true}).click();
        }
        async function save() {
            await page.getByRole('button',{name:'保存设置',exact:true}).click();
            await page.getByRole('button',{name:'保存设置',exact:true}).waitFor({state:'visible'});
            await page.waitForFunction(() => Array.from(document.querySelectorAll('.toast')).some(el => el.textContent.includes('设置已保存')));
            await page.locator('.toast').evaluateAll(nodes => nodes.forEach(node=>node.remove()));
        }
        const label = '直连设备（MAC / IP / 网段）';
        await page.goto('http://127.0.0.1:8765/#settings');
        await access();
        const before = (await rpc('get')).data;
        assert.deepEqual(before.settings.lanBypassDevices,[]);
        const dnsNotice = page.getByText('已开启路由器本机代理和 DNS 接管。', {exact:false});
        assert.equal(await dnsNotice.isVisible(),false);
        await page.getByRole('button',{name:'本机代理',exact:true}).click();
        await page.getByRole('checkbox',{name:'代理路由器本机',exact:true}).check();
        await page.getByRole('button',{name:'访问控制',exact:true}).click();
        assert.equal(await dnsNotice.isVisible(),true);
        await page.getByRole('button',{name:'本机代理',exact:true}).click();
        await page.getByRole('checkbox',{name:'代理路由器本机',exact:true}).uncheck();
        await page.getByRole('button',{name:'访问控制',exact:true}).click();
        assert.equal(await dnsNotice.isVisible(),false);
        await page.getByRole('combobox',{name:'选择'+label,exact:true}).selectOption('AA:BB:CC:DD:EE:FF');
        // An unsubmitted draft must also be included by the standard entries editor.
        await page.getByRole('textbox',{name:'添加'+label,exact:true}).fill('2001:db8::/64');
        await page.getByRole('button',{name:'运行设置',exact:true}).click();
        await page.getByRole('button',{name:'访问控制',exact:true}).click();
        await save();
        const saved = (await rpc('get')).data;
        assert.deepEqual(saved.settings.lanBypassDevices,['AA:BB:CC:DD:EE:FF','2001:db8::/64']);
        assert.deepEqual(saved.sources,before.sources);
        assert.deepEqual(saved.overrides,before.overrides);
        assert.deepEqual(saved.outputs,before.outputs);
        assert.deepEqual(saved.settings.lanRules,[{name:'默认规则',enabled:true,proxy:true,dns:true}]);
        results.push('conditional router DNS notice, DHCP MAC selection, IPv6 draft, tab switching and real backend save');
        await page.reload(); await access();
        await page.getByRole('button',{name:'移除 AA:BB:CC:DD:EE:FF',exact:true}).waitFor();
        assert.deepEqual((await rpc('export')).data.settings.lanBypassDevices,saved.settings.lanBypassDevices);
        results.push('reload and backup export retain direct devices');
        await page.locator('.fii').screenshot({path:path.join(output,'desktop.png')});
        await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
        await page.locator('.fii').screenshot({path:path.join(output,'mobile.png')});
        results.push('390px viewport has no page overflow');
        await page.getByRole('textbox',{name:'添加'+label,exact:true}).fill('invalid-device');
        await page.getByRole('button',{name:'保存设置',exact:true}).click();
        await page.getByText('直连设备地址无效: invalid-device',{exact:true}).waitFor();
        assert.equal((await rpc('get')).data.revision,saved.revision);
        results.push('invalid address reports validation error and preserves revision');
        await page.getByRole('textbox',{name:'添加'+label,exact:true}).fill('');
        await page.getByRole('button',{name:'移除 AA:BB:CC:DD:EE:FF',exact:true}).click();
        await page.getByRole('button',{name:'移除 2001:db8::/64',exact:true}).click();
        await save();
        assert.deepEqual((await rpc('get')).data.settings.lanBypassDevices,[]);
        results.push('removing all entries restores an empty bypass list');
        assert.deepEqual(errors,[]);
        await fs.writeFile(path.join(output,'ui-results.json'),JSON.stringify({results,errors},null,2)+'\n');
        console.log(JSON.stringify({results,errors},null,2));
    } finally {
        if (browser) await browser.close();
        server.kill();
    }
})().catch(error => {console.error(error);process.exitCode=1;});
