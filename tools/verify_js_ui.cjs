// Real LuCI JS override import, editor, rendering, activation and failure checks.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
    const output=path.resolve(__dirname,'../build/vm');
    const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1440,height:1050}});
    const errors=[],results=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('requestfailed',r=>errors.push({url:r.url(),error:r.failure().errorText}));
    let original,initial;
    async function rpc(method,payload={}) {
        return page.evaluate(async ({method,payload})=>{
            const rpc=await L.require('rpc');
            async function call(name,data) {
                let reply=await rpc.declare({object:'luci.fii',method:name,params:['payload'],expect:{'':{}},nobatch:true})(JSON.stringify(data));
                if(!reply.ok)throw Error(reply.error||'RPC failed');
                if(reply.responseTransfer) {
                    const t=reply.responseTransfer,bytes=new Uint8Array(t.size);
                    let offset=0;
                    while(offset<t.size) {
                        const part=await call('transfer_read',{id:t.id,offset});
                        const chunk=Uint8Array.from(atob(part.data),c=>c.charCodeAt(0));
                        bytes.set(chunk,offset);offset+=chunk.length;
                    }
                    await call('transfer_release',{id:t.id});
                    reply=JSON.parse(new TextDecoder().decode(bytes));
                }
                if(reply.result?.jobId) {
                    for(let i=0;i<240;i++) {
                        await new Promise(resolve=>setTimeout(resolve,500));
                        const job=await call('job',{id:reply.result.jobId});
                        if(job.done) {
                            if(!job.ok)throw Error(job.error);
                            return job.result;
                        }
                    }
                    throw Error('Job timed out');
                }
                return reply.result;
            }
            return call(method,payload);
        },{method,payload});
    }
    async function check(name,fn){await fn();results.push({name,passed:true});console.log(name);}
    async function waitSaved(){await page.waitForFunction(()=>!document.body.classList.contains('modal-overlay-active'),null,{timeout:60000});}
    async function editScript(name,body) {
        await page.getByRole('button',{name:'覆写',exact:true}).click();
        await page.locator('.fii-card').filter({has:page.getByRole('heading',{name,exact:true})}).getByRole('button',{name:'编辑',exact:true}).click();
        await page.getByRole('textbox',{name:'覆写内容',exact:true}).fill(body);
        assert.equal(await page.getByRole('textbox',{name:'覆写内容',exact:true}).inputValue(),body);
        assert.deepEqual(await page.locator('.fii-editor').evaluate(form=>Array.from(form.elements).filter(x=>!x.checkValidity()).map(x=>({type:x.type,message:x.validationMessage}))),[]);
        await page.getByRole('button',{name:'保存',exact:true}).click();
    }
    const backup={format:'subconverter-backup',version:1,
        sources:[{id:7,name:'JS Test Source',prefix:'Test-',cachedYaml:'proxies:\n - {name: HK, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n'}],
        templates:[
            {id:1,name:'Global YAML',type:'YAML',global:true,yamlBody:'log-level: warning'},
            {id:2,name:'Global JS',type:'JS',global:true,yamlBody:'const marker=1;function main(c){c.trace=["global"];return c;}'},
            {id:3,name:'Selected JS',type:'JS',remoteUrl:'https://fii-test-token@raw.githubusercontent.com/tanwov/clash-configs/refs/heads/master/scripts/dns.js',yamlBody:'const marker=2;function main(c){c.trace.push("selected");c.rules.unshift("DOMAIN,example.com,DIRECT");return c;}'},
            {id:4,name:'Selected YAML',type:'YAML',yamlBody:'trace+: [yaml]'}],
        outputProfiles:[{id:9,name:'JS Test Output',sourceIds:'7',overrideIds:'4,2,3'}]};
    try {
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii?js-check=11#settings');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD||'fii-test');
        await page.getByRole('button',{name:'登录',exact:true}).click();
        await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor();
        original=(await rpc('get')).data;initial=await rpc('status');
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
        await check('SubConverter Flutter JS backup imports through the real file picker',async()=>{
            await page.getByRole('button',{name:'导入备份',exact:true}).click();
            await page.getByLabel('选择备份 JSON 文件').setInputFiles({name:'js-backup.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))});
            await page.getByRole('button',{name:'检查备份',exact:true}).click();
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor({timeout:60000});
            await page.getByRole('button',{name:'导入并替换配置库',exact:true}).click();
            await waitSaved();
            const data=(await rpc('get')).data;
            assert.equal(data.overrides.filter(x=>x.type==='JS').length,2);
            assert.equal(data.overrides[2].body,backup.templates[2].yamlBody);
            assert.equal(data.overrides[2].url,backup.templates[2].remoteUrl);
            assert.deepEqual(data.settings,original.settings);
            assert.equal((await rpc('status')).appliedRevision,initial.appliedRevision);
        });
        await check('preview applies global deduplication and mixed YAML JS order',async()=>{
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.getByRole('button',{name:'预览',exact:true}).click();
            const area=page.getByRole('textbox',{name:'生成的订阅 YAML',exact:true});
            await area.waitFor({timeout:60000});
            const yaml=await area.inputValue();
            assert.match(yaml,/trace:\s*- global\s*- yaml\s*- selected/);
            assert.match(yaml,/DOMAIN,example.com,DIRECT/);
            assert.match(yaml,/Test-HK/);
            await page.screenshot({path:path.join(output,'js-override-preview.png')});
            await page.getByRole('button',{name:'关闭',exact:true}).last().click();
        });
        await check('JS editor accepts a GitHub token URL and saves the default script',async()=>{
            await page.getByRole('button',{name:'覆写',exact:true}).click();
            await page.getByRole('button',{name:'添加覆写',exact:true}).click();
            await page.getByRole('combobox',{name:'类型',exact:true}).selectOption('JS');
            assert.match(await page.getByRole('textbox',{name:'覆写内容',exact:true}).inputValue(),/function main\(config\)/);
            await page.getByRole('textbox',{name:'名称',exact:true}).fill('Created JS');
            await page.locator('#modal_overlay').getByText('远程更新',{exact:true}).click();
            const address='https://fii-test-token@raw.githubusercontent.com/tanwov/clash-configs/refs/heads/master/scripts/dns.js';
            await page.getByRole('textbox',{name:'远程更新 URL（可选）',exact:true}).fill(address);
            await page.getByRole('button',{name:'保存',exact:true}).click();
            await waitSaved();
            const created=(await rpc('get')).data.overrides.find(x=>x.name==='Created JS');
            assert.equal(created.type,'JS');assert.equal(created.url,address);
        });
        await check('invalid JS syntax preserves saved revision',async()=>{
            const before=(await rpc('get')).data.revision;
            await editScript('Created JS','function main( {');
            await page.getByRole('alert').filter({hasText:'JS 覆写「Created JS」校验失败'}).waitFor({timeout:60000});
            assert.equal((await rpc('get')).data.revision,before);
            await page.getByRole('button',{name:'取消',exact:true}).click();
        });
        await check('JS output validates and activates with the installed Mihomo',async()=>{
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.getByRole('button',{name:'应用到路由器',exact:true}).click();
            await page.waitForFunction(()=>!Array.from(document.querySelectorAll('.fii-body button')).some(x=>x.textContent==='应用到路由器'&&x.disabled),null,{timeout:60000});
            const status=await rpc('status');
            assert.equal(status.activeOutputId,'9');assert.equal(status.running,true);
        });
        await check('runtime JS error keeps the previously running snapshot',async()=>{
            const before=await rpc('status');
            await editScript('Selected JS','function main(c){throw Error("fixture-boom");}');await waitSaved();
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.getByRole('button',{name:'应用到路由器',exact:true}).click();
            await page.getByText(/JS 覆写「Selected JS」失败: fixture-boom/).first().waitFor({timeout:60000});
            const after=await rpc('status');
            assert.equal(after.appliedRevision,before.appliedRevision);assert.equal(after.running,true);
        });
        await check('main timeout is shown without interrupting the current service',async()=>{
            await editScript('Selected JS','function main(c){while(true){}}');await waitSaved();
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.getByRole('button',{name:'预览',exact:true}).click();
            await page.getByText(/JS 覆写「Selected JS」失败: JS 覆写执行超时/).first().waitFor({timeout:60000});
            assert.equal((await rpc('status')).running,true);
        });
        assert.deepEqual(errors,[]);
        results.push({name:'no JavaScript page errors or failed browser requests',passed:true});
    } catch(error) {
        console.log(await page.locator('body').innerText());
        await page.screenshot({path:path.join(output,'js-ui-failure.png')});throw error;
    } finally {
        if(original) {
            const current=(await rpc('get')).data;
            await rpc('save',{data:original,revision:current.revision});
            if(initial.activeOutputId&&initial.coreInstalled)await rpc('apply',{id:initial.activeOutputId});
            if(!initial.running)await rpc('service',{action:'stop'});
        }
        await fs.writeFile(path.join(output,'js-ui-results.json'),JSON.stringify({results,errors},null,2));
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exitCode=1});
