'use strict';
// Only this host wrapper has Node APIs. Each override gets a fresh context
// containing JS intrinsics and JSON data, with no host objects or callbacks.
const fs = require('node:fs');
const vm = require('node:vm');
const limit = 8 * 1024 * 1024;
const options = {timeout:2000, breakOnSigint:true};
try {
    const input = fs.readFileSync(0, 'utf8');
    if (Buffer.byteLength(input)>13*1024*1024) throw new Error('JS 请求过大');
    const request = JSON.parse(input);
    const context = vm.createContext(Object.create(null), {
        codeGeneration:{strings:false,wasm:false}, microtaskMode:'afterEvaluate'
    });
    new vm.Script('globalThis.console=Object.freeze({log(){},info(){},warn(){},error(){},debug(){}});').runInContext(context,options);
    new vm.Script(request.script, {filename:'override.js'}).runInContext(context,options);
    if (new vm.Script('typeof main === "function"').runInContext(context,options)!==true)
        throw new Error('缺少入口函数 main(config)');
    if (request.validateOnly) {
        process.stdout.write(JSON.stringify({ok:true}));
    } else {
        // The literal is parsed inside the context so no host object crosses it.
        const literal = JSON.stringify(JSON.stringify(request.config));
        const code = `(function(){
            const result=main(JSON.parse(${literal}));
            if(result===null || typeof result!=='object' || Array.isArray(result))
                throw new Error('main(config) 必须返回配置对象');
            if(typeof result.then==='function')
                throw new Error('JS 覆写必须同步返回对象，不支持 async / Promise');
            const json=JSON.stringify(result);
            if(typeof json!=='string' || json.length>${limit}) throw new Error('JS 输出配置超过 8 MiB');
            return json;
        })()`;
        const output = new vm.Script(code,{filename:'main(config)'}).runInContext(context,options);
        if(typeof output!=='string' || Buffer.byteLength(output)>limit) throw new Error('JS 输出配置超过 8 MiB');
        process.stdout.write('{"ok":true,"config":'+output+'}');
    }
} catch(error) {
    let message;
    try { message=String(error && error.message || error).slice(0,1500); }
    catch { message='JS 执行失败'; }
    if(message.includes('Script execution timed out')) message='JS 覆写执行超时（单阶段最多 2 秒）';
    process.stdout.write(JSON.stringify({ok:false,error:message}));
}
