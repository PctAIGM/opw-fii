// Real opkg upgrade/reinstall with an already authenticated LuCI browser.
// Only run against the disposable QEMU VM; never write session IDs to results.
const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const sshArgs=['-batch','-hostkey','SHA256:dR+JUBlCKpYLptuuKQVlTz7A7ouvJU4J9zp1q/wwVFg','-P','2222','-l','root','-pw',process.env.FII_VM_PASSWORD||'fii-test','127.0.0.1'];
function ssh(command){return execFileSync('plink',[...sshArgs,command],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();}
const version='0.1.1-15';
(async()=>{
    const browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050}});
    const results=[];
    const originalHash=ssh('sha256sum /etc/fii/state.json /etc/fii/active.json');
    const boot=ssh('if /etc/init.d/fii enabled; then echo enabled; else echo disabled; fi');
    assert.equal(JSON.parse(ssh('fii status')).running,false,'Stop the disposable VM service before this test');
    async function login(){
        await page.goto('http://127.0.0.1:8080/cgi-bin/luci/admin/services/fii');
        await page.locator('#cbi-input-password').fill(process.env.FII_VM_PASSWORD||'fii-test');
        await page.getByRole('button',{name:'登录',exact:true}).click();
        await page.getByRole('button',{name:'路由器设置',exact:true}).waitFor();
    }
    async function ready(){
        for(let i=0;i<50;i++){
            try {ssh("ubus list luci.fii | grep -qx luci.fii");return;}
            catch {await new Promise(r=>setTimeout(r,100));}
        }
        throw Error('FII RPC did not return after installation');
    }
    async function access(sid){
        return page.evaluate(async sid=>{
            const response=await fetch('/ubus/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'call',params:[sid,'session','access',{scope:'ubus',object:'luci.fii',function:'get'}]})});
            const reply=await response.json();
            return reply.result?.[0]===0&&reply.result[1]?.access===true;
        },sid);
    }
    async function install(name,command,required){
        const sid=await page.evaluate(()=>L.env.sessionid);
        assert.ok(await access(sid));
        const pid=ssh('pidof rpcd');
        ssh(command);await ready();
        const authorized=await access(sid);
        const samePid=ssh('pidof rpcd')===pid;
        await page.reload();
        await page.waitForFunction(()=>document.querySelector('#cbi-input-password')||Array.from(document.querySelectorAll('button')).some(x=>x.textContent==='路由器设置'),null,{timeout:30000});
        const loggedIn=await page.getByRole('button',{name:'路由器设置',exact:true}).count()>0;
        const sameSession=loggedIn&&await page.evaluate(()=>L.env.sessionid)===sid;
        const preserved=authorized&&loggedIn&&sameSession;
        assert.equal(ssh('sha256sum /etc/fii/state.json /etc/fii/active.json'),originalHash);
        assert.equal(ssh('if /etc/init.d/fii enabled; then echo enabled; else echo disabled; fi'),boot);
        results.push({name,sessionPreserved:preserved,rpcAuthorized:authorized,pageAuthenticated:loggedIn,sameSession,sameRpcdPid:samePid,configurationUnchanged:true});
        console.log(JSON.stringify(results.at(-1)));
        if(required)assert.ok(preserved&&samePid,'Installed 0.1.1-15 hooks lost the original session');
        if(!loggedIn)await login();
    }
    try {
        await login();
        await install('upgrade from legacy package',`opkg install /tmp/fii_${version}_all.ipk /tmp/luci-app-fii_${version}_all.ipk`,false);
        await install('reinstall core package',`opkg install --force-reinstall /tmp/fii_${version}_all.ipk`,true);
        await install('reinstall LuCI package',`opkg install --force-reinstall /tmp/luci-app-fii_${version}_all.ipk`,true);
        await page.getByRole('button',{name:'路由器设置',exact:true}).click();
        await page.getByRole('button',{name:'代理设置',exact:true}).waitFor();
        await page.screenshot({path:path.resolve(__dirname,'../build/vm/upgrade-session-preserved.png')});
    } finally {
        await fs.writeFile(path.resolve(__dirname,'../build/vm/upgrade-session-results.json'),JSON.stringify(results,null,2));
        await browser.close();
    }
})().catch(e=>{console.error(e);process.exitCode=1;});
