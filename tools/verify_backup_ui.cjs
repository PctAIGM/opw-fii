// Exercise the real LuCI import path, including rpcd frame boundaries.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
    const output = path.resolve(__dirname, '../build/vm');
    const browser = await chromium.launch({channel:'chrome',headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors = [], requests = [], frames = [], results = [];
    let confirmationMetrics;
    let faultInjection = false, rejectedPosts = 0, rejectedReplies = 0, lostAcknowledgements = 0;
    page.on('pageerror', e => errors.push(e.message));
    page.on('requestfailed', r => { if (!faultInjection) errors.push(r.failure().errorText); });
    page.on('request', r => {
        if (r.method() === 'POST' && r.url().includes('ubus')) {
            const bytes=Buffer.byteLength(r.postData() || ''); requests.push(bytes);
            let method;
            try {method=r.postDataJSON().params?.[2];} catch {}
            frames.push({method,bytes});
        }
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
        sources:[{id:7,name:'导入测试订阅',url:' \nhttps://example.com/sub\t ',cachedYaml:'proxies:\n - {name: Imported, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n# 中文🚀 '+ 'a'.repeat(2*1024*1024)+'\n'}],
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
            const from=frames.length, started=performance.now();
            await page.getByRole('button',{name:'导入并替换配置库',exact:true}).click();
            await page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(x=>x.textContent === '导入并替换配置库')?.disabled, null, {timeout:180000});
            assert.equal(await page.evaluate(() => document.body.classList.contains('modal-overlay-active')),false,'Import did not close confirmation dialog');
            const confirmation=frames.slice(from);
            confirmationMetrics={milliseconds:Math.round(performance.now()-started),requests:confirmation.length,
                postedBytes:confirmation.reduce((n,r)=>n+r.bytes,0),maxPostBytes:Math.max(...confirmation.map(r=>r.bytes)),methods:confirmation.map(r=>r.method)};
            assert.ok(confirmation.some(r=>r.method==='import'));
            assert.ok(!confirmation.some(r=>['transfer_begin','transfer_append','transfer_invoke'].includes(r.method)),'Confirmation uploaded the backup again');
            assert.ok(confirmationMetrics.maxPostBytes<1024,'Confirmation request contains backup contents');
            console.log('Token-only confirmation:',JSON.stringify(confirmationMetrics));
            const current=(await rpc('get')).data, status=await rpc('status');
            assert.equal(current.sources[0].cachedYaml,backup.sources[0].cachedYaml);
            assert.equal(current.sources[0].url,'https://example.com/sub');
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
        await check('URL errors identify each source and override inline without leaking addresses or changing state', async () => {
            const before=(await rpc('get')).data;
            await choose({format:'subconverter-backup',version:1,
                sources:[{id:7,name:'缺少协议',url:'example.com/private-token'},
                    {id:8,name:'认证订阅',url:'https://user:private-password@example.com/sub'}],
                templates:[{id:2,name:'远程脚本',type:'JS',remoteUrl:'https:///private-token',yamlBody:''}],outputProfiles:[]});
            const alert=page.locator('#modal_overlay').getByRole('alert');
            await alert.filter({hasText:'3 项地址错误'}).waitFor({timeout:45000});
            const message=await alert.innerText();
            for(const part of ['订阅「缺少协议」（ID: 7）','订阅「认证订阅」（ID: 8）','覆写「远程脚本」（ID: 2）','内嵌用户名密码','缺少域名']) assert.ok(message.includes(part),message);
            for(const secret of ['private-token','private-password','example.com']) assert.ok(!message.includes(secret));
            assert.deepEqual((await rpc('get')).data,before);
            assert.equal(await page.getByRole('heading',{name:'确认导入',exact:true}).count(),0);
            await page.screenshot({path:path.join(output,'backup-url-errors.png')});
            await page.getByRole('button',{name:'取消',exact:true}).click();
        });
        await check('invalid backup reports validation error and keeps saved state', async () => {
            const before=(await rpc('get')).data.revision;
            await choose({...backup,templates:[{id:2,name:'Unsupported type',type:'UNKNOWN',yamlBody:''}]});
            await page.getByText('覆写类型必须是 YAML、RULES 或 JS',{exact:true}).waitFor({timeout:180000});
            assert.equal((await rpc('get')).data.revision,before);
            await page.getByRole('button',{name:'取消',exact:true}).click();
        });
        const small={...backup,sources:[{...backup.sources[0],cachedYaml:backup.sources[0].cachedYaml.split('#')[0]}]};
        async function cancelChecked() {
            const released=page.waitForResponse(r=>{
                try {return r.request().postDataJSON().params?.[2]==='transfer_release';} catch {return false;}
            });
            await page.getByRole('button',{name:'取消',exact:true}).click();
            await released;
        }
        await check('canceling a checked backup releases its cached contents without changing revision',async()=>{
            const before=(await rpc('get',{compact:true})).data.revision;
            await choose(small);
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor();
            await cancelChecked();
            assert.equal((await rpc('get',{compact:true})).data.revision,before);
        });
        await check('a configuration change after checking blocks confirmation without uploading again',async()=>{
            await choose(small);
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor();
            const before=(await rpc('get',{compact:true})).data;
            await rpc('save',{patch:{settings:{userAgent:'backup-race-test'}},compact:true,revision:before.revision});
            const from=frames.length;
            await page.getByRole('button',{name:'导入并替换配置库',exact:true}).click();
            await page.getByRole('alert').filter({hasText:'配置在检查备份后已变化'}).waitFor({timeout:30000});
            assert.ok(!frames.slice(from).some(r=>r.method==='transfer_append'));
            const after=(await rpc('get',{compact:true})).data;
            assert.equal(after.revision,before.revision+1);assert.equal(after.settings.userAgent,'backup-race-test');
            await cancelChecked();
            await page.reload();
            await page.getByRole('button',{name:'路由器设置',exact:true}).click();
            await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
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
        await fs.writeFile(path.join(output,'backup-ui-results.json'),JSON.stringify({results,confirmationMetrics,maxPostBytes:Math.max(...requests),rejectedPosts,rejectedReplies,lostAcknowledgements,errors},null,2));
        await browser.close();
    }
})().catch(e=>{console.error(e);process.exitCode=1});
