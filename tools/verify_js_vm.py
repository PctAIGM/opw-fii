#!/usr/bin/env python3
"""Real Node and Mihomo checks; run only in the disposable OpenWrt VM."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, '/usr/lib')
from fii import javascript, model, render, fetch


def main():
    assert os.environ.get('FII_VM_TEST') == '1', 'Set FII_VM_TEST=1 on the disposable VM'
    directory=Path('/tmp/fii-js-verify-'+secrets.token_hex(8))
    directory.mkdir()
    results=[]
    def record(name):
        results.append({'name':name,'passed':True});print(name,flush=True)
    try:
        actual_run=subprocess.run
        with patch.object(javascript.subprocess,'run',wraps=actual_run) as spawned:
            result=javascript.execute('function main(c){c.rules.unshift("DOMAIN,example.com,DIRECT");return c;}',{'rules':['MATCH,PROXY']})
        assert result['rules'][0]=='DOMAIN,example.com,DIRECT'
        assert spawned.call_args.kwargs['user']==65534 and spawned.call_args.kwargs['extra_groups']==[]
        record('real Node main(config) runs as UID/GID 65534')
        data=model.defaults()
        data['sources']=[{'id':'s','name':'Fixture','cachedYaml':'proxies:\n - {name: Test, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n'}]
        data['outputs']=[{'id':'p','name':'Fixture','sourceIds':['s'],'overrideIds':['yaml','global','js']}]
        data['overrides']=[
            {'id':'global','name':'Global JS','type':'JS','global':True,'body':'const x=1;function main(c){c.trace=["global"];return c;}'},
            {'id':'yaml','name':'YAML','type':'YAML','body':'trace+: [yaml]'},
            {'id':'js','name':'Selected JS','type':'JS','body':'const x=2;function main(c){c.trace.push("js");c.rules.unshift("DOMAIN,example.com,DIRECT");return c;}'}]
        output=render.render(data,'p')['yaml']
        assert render.load_map(output)['trace']==['global','yaml','js']
        record('global deduplication, YAML/JS order and independent lexical declarations')
        config=directory/'config.yaml'
        config.write_text(output)
        check=subprocess.run(['/etc/fii/bin/mihomo','-t','-d',str(directory),'-f',str(config)],capture_output=True,text=True,timeout=30)
        assert check.returncode==0,check.stdout+check.stderr
        record('JS-generated configuration passes installed Linux Mihomo -t')
        javascript.validate('function main(c){throw Error("only when executed");}')
        record('validation does not invoke main')
        for value in ('null','[]','1','Promise.resolve({})'):
            try: javascript.execute('function main(c){return '+value+';}',{})
            except ValueError: pass
            else: raise AssertionError(value)
        record('invalid object and asynchronous returns rejected')
        for script in ('while(true){};function main(c){return c;}','function main(c){while(true){}}'):
            try: javascript.execute(script,{})
            except ValueError as error: assert '超时' in str(error)
            else: raise AssertionError('timeout not enforced')
        record('top-level and main loops interrupted in real Node')
        result=javascript.execute('function main(c){console.log("test");return {apis:[typeof require,typeof process,typeof fetch,typeof setTimeout]};}',{})
        assert result['apis']==['undefined']*4
        record('host APIs absent and console output does not corrupt protocol')
        item={'id':'js','name':'Remote JS','type':'JS','url':'https://example.com/script','body':'function main(c){return c;}'}
        with patch.object(fetch,'download',return_value=('function main( {',{})):
            assert not fetch.refresh(item,data['settings'],'overrides')
        assert item['body']=='function main(c){return c;}'
        record('invalid remote refresh retains the previous script')
        Path('/tmp/fii-js-vm-results.json').write_text(json.dumps({'node':subprocess.check_output(['node','--version'],text=True).strip(),'results':results},indent=2))
    finally:
        assert directory.resolve().parent==Path('/tmp') and directory.name.startswith('fii-js-verify-')
        shutil.rmtree(directory)


if __name__=='__main__':
    main()
