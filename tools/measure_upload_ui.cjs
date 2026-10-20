// Measure upload separately from validation; preview/cancel never imports data.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const label=process.argv[2]||'current';
(async()=>{
    const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
    const page=await browser.newPage();
    const errors=[],frames=[];
    let started,uploadFinished;
    page.on('pageerror',e=>errors.push(e.message));
    page.on('requestfailed',r=>errors.push(r.failure().errorText));
    page.on('request',r=>{
        if(!started||r.method()!=='POST')return;
        let method;
        try{method=r.postDataJSON()?.params?.[2]}catch{}
        frames.push({method:method||(r.url().endsWith('/cgi-upload')?'file_upload':'other'),bytes:r.postDataBuffer()?.length||0});
    });
    page.on('requestfinished',r=>{
        if(!started||r.method()!=='POST')return;
        if(r.url().endsWith('/cgi-upload')||(r.postData()||'').includes('"transfer_append"'))uploadFinished=performance.now();
    });
    try{
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD||'fii-test');
        await page.getByRole('button',{name:'登录',exact:true}).click();
        await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor();
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'备份与迁移',exact:true}).click();
        const fixture={format:'subconverter-backup',version:1,sources:[{id:1,name:'Upload fixture',cachedYaml:'proxies:\n - {name: HK, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n# '+ 'x'.repeat(1950000)}],templates:[],outputProfiles:[]};
        const buffer=Buffer.from(JSON.stringify(fixture));
        await page.getByRole('button',{name:'导入备份',exact:true}).click();
        await page.getByLabel('选择备份 JSON 文件').setInputFiles({name:'upload-fixture.json',mimeType:'application/json',buffer});
        started=performance.now();
        await page.getByRole('button',{name:'检查备份',exact:true}).click();
        await page.getByRole('heading',{name:'确认导入',exact:true}).waitFor({timeout:300000});
        const result={label,fileBytes:buffer.length,uploadMilliseconds:Math.round(uploadFinished-started),checkTotalMilliseconds:Math.round(performance.now()-started),
            rpcPosts:frames.filter(x=>x.method!=='file_upload').length,appendPosts:frames.filter(x=>x.method==='transfer_append').length,
            fileUploads:frames.filter(x=>x.method==='file_upload').length,maxRpcPostBytes:Math.max(...frames.filter(x=>x.method!=='file_upload').map(x=>x.bytes)),errors};
        const release=page.waitForResponse(r=>r.url().includes('ubus')&&(r.request().postData()||'').includes('"transfer_release"'));
        await page.getByRole('button',{name:'取消',exact:true}).click();await release;
        assert.deepEqual(errors,[]);
        await fs.writeFile(path.resolve(__dirname,'../build/vm/upload-performance-'+label+'.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify(result));
    }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
