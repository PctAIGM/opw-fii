// Exercise the real LuCI import path, including rpcd frame boundaries.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
    const output = path.resolve(__dirname, '../build/vm');
    const browser = await chromium.launch({channel:'chrome',headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors = [], requests = [], results = [];
    let faultInjection = false, rejectedPosts = 0, rejectedReplies = 0, lostAcknowledgements = 0;
    page.on('pageerror', e => errors.push(e.message));
    page.on('requestfailed', r => { if (!faultInjection) errors.push(r.failure().errorText); });
    page.on('request', r => {
        if (r.method() === 'POST' && r.url().includes('ubus')) requests.push(Buffer.byteLength(r.postData() || ''));
    });
    let original, initial;
    async function rpc(method, payload={}) {
        return page.evaluate(async ({method,payload}) => {
            const r = await L.require('rpc');
            const invoke = async (name,data) => {
                let reply = await r.declare({object:'luci.fii',method:name,params:['payload'],expect:{'':{}},nobatch:true})(JSON.stringify(data));
                if (!reply.ok) throw new Error(reply.error || 'RPC failed');
                if (reply.responseTransfer) {
                    const t=reply.responseTransfer, bytes=new Uint8Array(t.size);
                    let offset=0;
                    while(offset<t.size) {
                        const part=await invoke('transfer_read',{id:t.id,offset});
                        const chunk=Uint8Array.from(atob(part.data),c=>c.charCodeAt(0));
                        bytes.set(chunk,offset); offset+=chunk.length;
                    }
                    await invoke('transfer_release',{id:t.id});
                    reply=JSON.parse(new TextDecoder().decode(bytes));
                }
                if (reply.result && reply.result.jobId) {
                    for (let i=0;i<240;i++) {
                        await new Promise(resolve=>setTimeout(resolve,750));
                        const job=await invoke('job',{id:reply.result.jobId});
                        if (job.done) {
                            if (!job.ok) throw new Error(job.error);
                            return job.result;
                        }
                    }
                    throw new Error('Job timed out');
                }
                return reply.result;
            };
            return invoke(method,payload);
        }, {method,payload});
    }
    async function check(name, action) {
        await action(); results.push({name,passed:true}); console.log(name);
    }
    async function choose(raw) {
        await page.getByRole('button',{name:'导入备份',exact:true}).click();
        await page.getByLabel('选择备份 JSON 文件').setInputFiles({name:'subconverter-backup.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(raw))});
        await page.getByRole('button',{name:'检查备份',exact:true}).click();
    }
    const backup = {
        format:'subconverter-backup',version:1,
        sources:[{id:7,name:'导入测试订阅',cachedYaml:'proxies:\n - {name: Imported, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n# 中文🚀 '+ 'a'.repeat(2*1024*1024)+'\n'}],
        templates:[{id:2,name:'规则',type:'RULES',yamlBody:'DOMAIN,example.com,DIRECT'}],
        outputProfiles:[{id:9,name:'导入测试输出',sourceIds:'7',overrideIds:'2'}]
    };
    try {
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii?backup-check=10#settings');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD || 'fii-test');
        await page.getByRole('button',{name:'登录',exact:true}).click();
        await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor();
        original=(await rpc('get')).data; initial=await rpc('status');
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
        await check('2 MiB SubConverter backup preview without oversized XHR', async () => {
            await choose(backup);
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor({timeout:180000});
            assert.match(await page.locator('#modal_overlay').innerText(), /1 个订阅、1 个覆写、1 个输出/);
            assert.equal((await rpc('get')).data.revision, original.revision);
            await page.screenshot({path:path.join(output,'backup-preview.png')});
        });
        await check('confirmed import reloads large state and preserves router settings and active snapshot', async () => {
            await page.getByRole('button',{name:'导入并替换配置库',exact:true}).click();
            await page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(x=>x.textContent === '导入并替换配置库')?.disabled, null, {timeout:180000});
            assert.equal(await page.evaluate(() => document.body.classList.contains('modal-overlay-active')),false,'Import did not close confirmation dialog');
            const current=(await rpc('get')).data, status=await rpc('status');
            assert.equal(current.sources[0].cachedYaml,backup.sources[0].cachedYaml);
            assert.deepEqual(current.settings,original.settings);
            assert.equal(status.activeOutputId,initial.activeOutputId);
            assert.equal(status.appliedRevision,initial.appliedRevision);
            assert.equal(status.running,initial.running);
            assert.equal(current.outputs[0].sourceIds[0],'7');
        });
        await check('large backup export round-trips without truncation', async () => {
            await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
            const pending=page.waitForEvent('download');
            await page.getByRole('button',{name:'导出备份',exact:true}).click();
            const download=await pending;
            const data=JSON.parse(await fs.readFile(await download.path(),'utf8'));
            assert.equal(data.sources[0].cachedYaml,backup.sources[0].cachedYaml);
        });
        await check('settings save round-trips the large configuration without applying it', async () => {
            const before=(await rpc('get')).data.revision;
            await page.getByRole('button',{name:'运行设置',exact:true}).click();
            await page.getByRole('textbox',{name:'订阅下载 User-Agent',exact:true}).fill('fii-backup-test');
            await page.getByRole('button',{name:'保存设置',exact:true}).click();
            await page.waitForFunction(() => !Array.from(document.querySelectorAll('.fii-settings-save button')).some(x=>x.disabled),null,{timeout:180000});
            const current=(await rpc('get')).data;
            assert.ok(current.revision>before);
            assert.equal(current.settings.userAgent,'fii-backup-test');
            assert.equal(current.sources[0].cachedYaml,backup.sources[0].cachedYaml);
            assert.equal((await rpc('status')).appliedRevision,initial.appliedRevision);
            await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
        });
        await check('invalid backup reports validation error and keeps saved state', async () => {
            const before=(await rpc('get')).data.revision;
            await choose({...backup,templates:[{id:2,name:'Unsupported type',type:'UNKNOWN',yamlBody:''}]});
            await page.getByText('覆写类型必须是 YAML、RULES 或 JS',{exact:true}).waitFor({timeout:180000});
            assert.equal((await rpc('get')).data.revision,before);
            await page.getByRole('button',{name:'取消',exact:true}).click();
        });
        await check('1.86 MiB preview retries under 4 KiB POST limit and a lost upload acknowledgement', async () => {
            faultInjection = true;
            await page.route(url=>url.pathname.includes('ubus'), async route => {
                const body=route.request().postData()||'';
                if (Buffer.byteLength(body)>4096) {
                    rejectedPosts++; return route.abort('connectionreset');
                }
                if (!lostAcknowledgements && body.includes('"transfer_append"')) {
                    await route.fetch();
                    lostAcknowledgements++;
                    return route.abort('connectionreset');
                }
                return route.continue();
            });
            await choose({...backup,sources:[{...backup.sources[0],cachedYaml:backup.sources[0].cachedYaml.slice(0,1950000)+'\n'}]});
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor({timeout:300000});
            assert.ok(rejectedPosts>0);
            assert.equal(lostAcknowledgements,1);
            await page.getByRole('button',{name:'取消',exact:true}).click();
            await page.unrouteAll({behavior:'wait'});
            faultInjection = false;
        });
        await check('large export retries under 4 KiB reply limit', async () => {
            await page.reload();
            await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor({timeout:180000});
            await page.getByRole('button',{name:'路由器设置',exact:true}).click();
            await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
            faultInjection = true;
            await page.route(url=>url.pathname.includes('ubus'),async route => {
                const response=await route.fetch(), body=await response.body();
                if(body.length>4096) {
                    rejectedReplies++;return route.abort('connectionreset');
                }
                return route.fulfill({response,body});
            });
            const pending=page.waitForEvent('download',{timeout:300000});
            await page.getByRole('button',{name:'导出备份',exact:true}).click();
            const download=await pending;
            const data=JSON.parse(await fs.readFile(await download.path(),'utf8'));
            assert.equal(data.sources[0].cachedYaml,backup.sources[0].cachedYaml);
            assert.ok(rejectedReplies>0);
            await page.unrouteAll({behavior:'wait'});
            faultInjection = false;
        });
        await check('all backup RPC POST frames stay below 64 KiB with no unexpected browser errors', async () => {
            assert.ok(requests.length>100);
            assert.ok(Math.max(...requests)<64*1024, 'Largest POST: '+Math.max(...requests));
            assert.deepEqual(errors,[]);
        });
    } catch (error) {
        console.log(await page.locator('body').innerText());
        await page.screenshot({path:path.join(output,'backup-ui-failure.png')});
        throw error;
    } finally {
        await page.unrouteAll({behavior:'wait'});
        faultInjection = false;
        if (original) {
            const current=(await rpc('get')).data;
            await rpc('save',{data:original,revision:current.revision});
        }
        await fs.writeFile(path.join(output,'backup-ui-results.json'),JSON.stringify({results,maxPostBytes:Math.max(...requests),rejectedPosts,rejectedReplies,lostAcknowledgements,errors},null,2));
        await browser.close();
    }
})().catch(e=>{console.error(e);process.exitCode=1});
