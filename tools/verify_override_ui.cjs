// Real LuCI import order, sorting, editing concurrency, global state and preview feedback.
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
    const modal=()=>page.locator('#modal_overlay');
    const row=name=>page.locator('.fii-override-row').filter({has:page.getByRole('heading',{name,exact:true})});
    const read=async()=> (await rpc('get')).data;
    const change=async patch=> rpc('save',{patch,compact:true,revision:(await read()).revision});
    const libraryOrder=async()=>page.locator('.fii-override-row').evaluateAll(nodes=>nodes.map(node=>node.dataset.id));
    const backup={format:'subconverter-backup',version:1,
        sources:[{id:7,name:'Order source',cachedYaml:'proxies:\n - {name: HK, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n'}],
        templates:[
            {id:1,name:'G2',sortOrder:20,type:'YAML',global:true,yamlBody:'trace+: [g2]'},
            {id:8,name:'S2',sortOrder:40,type:'JS',yamlBody:'function main(c){c.trace.push("s2");return c;}'},
            {id:3,name:'G1',sortOrder:10,type:'JS',global:true,yamlBody:'function main(c){c.trace=(c.trace||[]).concat("g1");return c;}'},
            {id:4,name:'S1',sortOrder:30,type:'YAML',remoteUrl:'http://127.0.0.1:9/fixture',yamlBody:'trace+: [s1]'},
            {id:9,name:'Disabled',sortOrder:15,type:'JS',global:true,enabled:false,yamlBody:'function main(c){while(true){}}'}],
        outputProfiles:[{id:6,name:'Ordered output',sourceIds:'7',overrideIds:'8,3,4,9'}]};
    let previewMetrics;
    try {
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii?override-check=17#settings');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD||'fii-test');
        await page.getByRole('button',{name:'登录',exact:true}).click();
        await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor({timeout:30000});
        original=await read();initial=await rpc('status');
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
        await check('Flutter database insertion order imports as sortOrder/id order and matching selected execution order',async()=>{
            await page.getByRole('button',{name:'导入备份',exact:true}).click();
            await page.getByLabel('选择备份 JSON 文件').setInputFiles({name:'ordered-backup.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))});
            await page.getByRole('button',{name:'检查备份',exact:true}).click();
            await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor({timeout:60000});
            await page.getByRole('button',{name:'导入并替换配置库',exact:true}).click();await waitSaved();
            const data=await read();
            assert.deepEqual(data.overrides.map(x=>x.id),['3','9','1','4','8']);
            assert.deepEqual(data.outputs[0].overrideIds,['3','9','4','8']);
            assert.deepEqual(data.settings,original.settings);
        });
        await check('override library is numbered in one column at desktop and mobile widths; local and remote actions are explicit',async()=>{
            await page.getByRole('button',{name:'覆写',exact:true}).click();
            assert.deepEqual(await libraryOrder(),['3','9','1','4','8']);
            assert.equal(await page.getByRole('button',{name:'刷新远程内容',exact:true}).count(),1);
            assert.match(await row('G1').innerText(),/本地覆写/);
            assert.equal(await row('G1').getByRole('button',{name:'刷新远程内容',exact:true}).count(),0);
            for(const width of [1440,390]) {
                await page.setViewportSize({width,height:1050});
                const boxes=await page.locator('.fii-override-row').evaluateAll(nodes=>nodes.map(x=>{const b=x.getBoundingClientRect();return{x:b.x,y:b.y,w:b.width,h:b.height}}));
                assert.ok(boxes.every(b=>Math.abs(b.x-boxes[0].x)<1));
                assert.ok(boxes.slice(1).every((b,i)=>b.y>=boxes[i].y+boxes[i].h));
                assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
                assert.deepEqual(await page.locator('.fii-order-number').allTextContents(),['1','2','3','4','5']);
                const title=await row('Disabled').locator('h3').boundingBox();
                assert.ok(title.width>60&&title.height<80,'Override title squeezed into a vertical column');
                await page.screenshot({path:path.join(output,'override-order-'+width+'.png'),fullPage:true});
            }
            await page.setViewportSize({width:1440,height:1050});
        });
        await check('move-to-position and up/down persist exact order without changing bodies or profile associations',async()=>{
            const before=await read();
            await row('G2').getByRole('button',{name:'移动 G2 到指定位置',exact:true}).click();
            await page.getByRole('spinbutton',{name:'移动到第几项',exact:true}).fill('1');
            await page.getByRole('button',{name:'保存',exact:true}).click();await waitSaved();
            assert.deepEqual(await libraryOrder(),['1','3','9','4','8']);
            await row('G2').getByRole('button',{name:'下移 G2',exact:true}).click();
            await page.waitForFunction(()=>document.querySelector('.fii-override-row')?.dataset.id==='3');
            assert.deepEqual(await libraryOrder(),['3','1','9','4','8']);
            const after=await read();
            for(const item of before.overrides)assert.equal(after.overrides.find(x=>x.id===item.id).body,item.body);
            assert.deepEqual(after.outputs,before.outputs);
        });
        await check('opening a stale override editor synchronizes latest body and revision without manual reload',async()=>{
            const body='function main(c){c.trace.push("s2");return c;}\n// fresh from background';
            await change({kind:'overrides',id:'8',fields:{body,lastRefreshAt:1792400000,name:'S2 refreshed'}});
            await row('S2').getByRole('button',{name:'编辑',exact:true}).click();
            await page.getByRole('textbox',{name:'覆写内容',exact:true}).waitFor();
            assert.equal(await page.getByRole('textbox',{name:'覆写内容',exact:true}).inputValue(),body);
            assert.equal(await page.getByRole('textbox',{name:'名称',exact:true}).inputValue(),'S2 refreshed');
            await page.getByRole('textbox',{name:'名称',exact:true}).fill('S2');
            await page.getByRole('button',{name:'保存',exact:true}).click();await waitSaved();
            assert.equal((await read()).overrides.find(x=>x.id==='8').body,body);
        });
        await check('an edit that truly races a background write is blocked and reopening recovers the latest item',async()=>{
            await row('S2').getByRole('button',{name:'编辑',exact:true}).click();
            await page.getByRole('textbox',{name:'覆写内容',exact:true}).waitFor();
            await page.getByRole('textbox',{name:'名称',exact:true}).fill('must-not-overwrite');
            await change({kind:'overrides',id:'8',fields:{lastRefreshAt:1792400001}});
            await page.getByRole('button',{name:'保存',exact:true}).click();
            await modal().getByRole('alert').filter({hasText:'配置已被其他操作更新'}).waitFor();
            assert.equal((await read()).overrides.find(x=>x.id==='8').name,'S2');
            await page.getByRole('button',{name:'取消',exact:true}).click();
            await row('S2').getByRole('button',{name:'编辑',exact:true}).click();
            await page.getByRole('textbox',{name:'覆写内容',exact:true}).waitFor();
            await page.getByRole('button',{name:'保存',exact:true}).click();await waitSaved();
        });
        await check('failed remote refresh preserves script content and remains editable with the new revision',async()=>{
            const body=(await read()).overrides.find(x=>x.id==='4').body;
            await row('S1').getByRole('button',{name:'刷新远程内容',exact:true}).click();
            await row('S1').getByText(/下载失败/).waitFor();
            assert.equal((await read()).overrides.find(x=>x.id==='4').body,body);
            await row('S1').getByRole('button',{name:'编辑',exact:true}).click();
            assert.equal(await page.getByRole('textbox',{name:'覆写内容',exact:true}).inputValue(),body);
            await page.getByRole('button',{name:'取消',exact:true}).click();
        });
        await check('output editor shows checked automatic globals, excludes disabled globals and permits selected-order changes',async()=>{
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.locator('.fii-card').getByRole('button',{name:'编辑',exact:true}).click();
            await modal().getByRole('heading',{name:'全局覆写（自动生效）',exact:true}).waitFor();
            for(const name of ['G1','G2']) {
                const box=modal().getByRole('checkbox',{name,exact:true});
                assert.equal(await box.isChecked(),true);assert.equal(await box.isDisabled(),true);
            }
            assert.equal(await modal().getByRole('checkbox',{name:'Disabled',exact:true}).isChecked(),false);
            assert.deepEqual(await modal().locator('.fii-execution li').evaluateAll(nodes=>nodes.map(x=>x.dataset.id)),['3','1','4','8']);
            await modal().getByRole('button',{name:'上移 S2',exact:true}).click();
            assert.deepEqual(await modal().locator('.fii-execution li').evaluateAll(nodes=>nodes.map(x=>x.dataset.id)),['3','1','8','4']);
            await page.getByRole('button',{name:'保存',exact:true}).click();await waitSaved();
            assert.deepEqual((await read()).outputs[0].overrideIds,['8','4']);
        });
        await check('preview immediately opens a progress modal, then reports actual backend order and generated YAML',async()=>{
            let delayed=false;
            await page.route(url=>url.pathname.includes('ubus'),async route=>{
                if(!delayed&&(route.request().postData()||'').includes('"preview"')) {delayed=true;await new Promise(r=>setTimeout(r,1200));}
                await route.continue();
            });
            const start=performance.now();
            await page.getByRole('button',{name:'预览',exact:true}).click();
            await modal().getByRole('status').filter({hasText:'正在生成预览'}).waitFor();
            const feedbackMs=Math.round(performance.now()-start);
            assert.ok(feedbackMs<1000,'feedback delayed '+feedbackMs);
            await page.getByRole('textbox',{name:'生成的订阅 YAML',exact:true}).waitFor({timeout:60000});
            const yaml=await page.getByRole('textbox',{name:'生成的订阅 YAML',exact:true}).inputValue();
            assert.match(yaml,/trace:\s*- g1\s*- g2\s*- s2\s*- s1/);
            assert.deepEqual(await modal().locator('.fii-execution li').evaluateAll(nodes=>nodes.map(x=>x.dataset.id)),['3','1','8','4']);
            previewMetrics={feedbackMs,totalMs:Math.round(performance.now()-start),artificialRequestDelayMs:1200};
            await modal().getByText('本次执行的覆写顺序',{exact:true}).click();
            await page.screenshot({path:path.join(output,'override-execution-preview.png')});
            await page.getByRole('button',{name:'关闭',exact:true}).last().click();
            await page.unrouteAll({behavior:'wait'});
        });
        await check('closing an in-flight preview keeps it closed when rendering finishes',async()=>{
            await page.getByRole('button',{name:'预览',exact:true}).click();
            await modal().getByRole('heading',{name:'Ordered output · 预览',exact:true}).waitFor();
            await modal().getByRole('button',{name:'关闭',exact:true}).click();
            await page.waitForFunction(()=>!Array.from(document.querySelectorAll('.fii-body button')).some(x=>x.textContent==='预览'&&x.disabled));
            assert.equal(await page.evaluate(()=>document.body.classList.contains('modal-overlay-active')),false);
        });
        assert.deepEqual(errors,[]);
        results.push({name:'no unexpected JavaScript page or browser request errors',passed:true});
    } catch(error) {
        console.log((await page.locator('body').innerText()).slice(-7000));
        await page.screenshot({path:path.join(output,'override-ui-failure.png')});throw error;
    } finally {
        await page.unrouteAll({behavior:'wait'});
        if(original) await rpc('save',{data:original,revision:(await read()).revision});
        await fs.writeFile(path.join(output,'override-ui-results.json'),JSON.stringify({results,previewMetrics,errors},null,2));
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exitCode=1});
