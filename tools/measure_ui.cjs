// Actual LuCI timings against a reversible, generated 1.86 MiB VM fixture.
const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const label=process.argv[2]||'current';
const ssh=['-batch','-hostkey','SHA256:dR+JUBlCKpYLptuuKQVlTz7A7ouvJU4J9zp1q/wwVFg','-P','2222','-l','root','-pw',process.env.FII_VM_PASSWORD||'fii-test','127.0.0.1'];
function fixture(action){return execFileSync('plink',[...ssh,'FII_VM_TEST=1 python3 /tmp/prepare_ui_benchmark.py '+action],{encoding:'utf8'}).trim();}
(async()=>{
    let browser,setup=false;
    const results=[];
    try {
        console.log(fixture('setup'));setup=true;
        browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
        const page=await browser.newPage({viewport:{width:1440,height:1050}});
        let frames=null;
        page.on('request',r=>{
            if(frames&&r.method()==='POST'&&r.url().includes('ubus')) frames.push(Buffer.byteLength(r.postData()||''));
        });
        async function measure(name,action){
            frames=[];const start=performance.now();await action();
            const result={name,milliseconds:Math.round(performance.now()-start),requests:frames.length,postedBytes:frames.reduce((a,b)=>a+b,0)};
            frames=null;results.push(result);console.log(JSON.stringify(result));
        }
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii#outputs');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD||'fii-test');
        await measure('initial_load',async()=>{
            await page.getByRole('button',{name:'登录',exact:true}).click();
            await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor({timeout:180000});
            await page.getByRole('button',{name:'输出配置',exact:true}).click();
            await page.getByRole('button',{name:'创建输出',exact:true}).waitFor({timeout:180000});
        });
        await page.getByRole('button',{name:'编辑',exact:true}).click();
        await page.getByRole('textbox',{name:'名称',exact:true}).fill('Benchmark Renamed');
        await measure('rename_output',async()=>{
            await page.getByRole('button',{name:'保存',exact:true}).click();
            await page.getByRole('heading',{name:'Benchmark Renamed',exact:true}).waitFor({timeout:180000});
        });
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'运行设置',exact:true}).click();
        await page.getByRole('textbox',{name:'订阅下载 User-Agent',exact:true}).fill('fii-benchmark');
        await measure('save_settings',async()=>{
            await page.getByRole('button',{name:'保存设置',exact:true}).click();
            await page.waitForFunction(()=>!Array.from(document.querySelectorAll('.fii-settings-save button')).some(x=>x.disabled),null,{timeout:180000});
        });
        await measure('reload_view',async()=>{await page.reload();await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor({timeout:180000});});
        if(label==='after'||label==='17') {
            await page.getByRole('button',{name:'订阅源',exact:true}).click();
            await page.getByRole('button',{name:'编辑',exact:true}).click();
            await page.getByRole('textbox',{name:'名称',exact:true}).fill('Benchmark Source Renamed');
            await measure('rename_source_without_reading_cache',async()=>{
                await page.getByRole('button',{name:'保存',exact:true}).click();
                await page.getByRole('heading',{name:'Benchmark Source Renamed',exact:true}).waitFor({timeout:30000});
            });
            assert.ok(results.at(-1).postedBytes<10000,'Source rename uploaded cached content');
            await page.getByRole('button',{name:'编辑',exact:true}).click();
            await page.getByText('筛选、刷新与本地内容',{exact:true}).click();
            assert.equal(await page.getByRole('checkbox',{name:'自动刷新',exact:true}).isChecked(),false);
            const body=page.getByRole('textbox',{name:'本地内容 / 上次成功缓存',exact:true,includeHidden:true});
            assert.equal(await body.isDisabled(),true);
            await page.locator('#modal_overlay summary').filter({hasText:'本地内容 / 上次成功缓存'}).click();
            await page.waitForFunction(()=>!document.querySelector('#modal_overlay textarea').disabled,null,{timeout:30000});
            assert.ok((await body.inputValue()).length>1900000,'Lazy editor content was truncated');
            await page.getByRole('textbox',{name:'节点前缀',exact:true}).fill('Bench-');
            await measure('save_source_with_unchanged_loaded_cache',async()=>{
                await page.getByRole('button',{name:'保存',exact:true}).click();
                await page.waitForFunction(()=>!document.body.classList.contains('modal-overlay-active'),null,{timeout:30000});
            });
            assert.ok(results.at(-1).postedBytes<10000,'Unchanged cached content was reuploaded');
            console.log(fixture('verify'));
        }
        await fs.writeFile(path.resolve(__dirname,'../build/vm/ui-performance-'+label+'.json'),JSON.stringify(results,null,2));
    } finally {
        if(browser)await browser.close();
        if(setup)console.log(fixture('restore'));
    }
})().catch(e=>{console.error(e);process.exitCode=1;});
