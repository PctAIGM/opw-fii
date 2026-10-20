"""Mihomo configuration defaults and legacy-policy migration, separate from routing."""
import copy
import ipaddress
import json

LEGACY_KEYS = {'dnsMode', 'fakeIpFilter', 'nameservers', 'defaultNameservers',
               'proxyNameservers', 'localDns', 'tunStack', 'tunMtu'}
DEFAULTS = {
    'ipv6': False,
    'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'fake-ip',
            'fake-ip-range': '198.18.0.1/16', 'fake-ip-range6': 'fc00::/18',
            'nameserver': ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query'],
            'default-nameserver': ['223.5.5.5', '119.29.29.29'],
            'fake-ip-filter': ['+.lan', '+.local'],
            'nameserver-policy': {'+.lan': ['127.0.0.1:53'], '+.local': ['127.0.0.1:53']}},
    'tun': {'stack': 'mixed', 'mtu': 1500},
    'profile': {'store-selected': True, 'store-fake-ip': True},
}


def seed(root, final=False):
    """Fill absent Mihomo defaults without consulting router settings."""
    from .model import check
    def fill(target, defaults):
        for key, value in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(value)
            elif isinstance(value, dict):
                check(isinstance(target[key], dict), key + ' 覆写必须是对象')
                fill(target[key], value)
    fill(root, DEFAULTS)
    if final:
        root['dns'].setdefault('proxy-server-nameserver', copy.deepcopy(root['dns']['nameserver']))
    return root


def migrate(data):
    """Move legacy policy settings into a visible, lowest-priority YAML override.

    Called on a decoded copy only. Store reads never rewrite the user's file.
    Existing overrides retain priority and the migration is idempotent.
    """
    from .model import check, text
    settings = data.get('settings')
    if not isinstance(settings, dict) or not LEGACY_KEYS.intersection(settings):
        return
    check(isinstance(data.get('overrides'), list), 'overrides 列表无效')
    dns, tun = {}, {}
    fields = {'dnsMode': 'enhanced-mode', 'fakeIpFilter': 'fake-ip-filter',
              'nameservers': 'nameserver', 'defaultNameservers': 'default-nameserver',
              'proxyNameservers': 'proxy-server-nameserver'}
    for old, new in fields.items():
        if old not in settings:
            continue
        value = settings[old]
        if old == 'dnsMode':
            check(value in ('fake-ip', 'redir-host'), 'DNS 模式无效')
        else:
            check(isinstance(value, list) and len(value) <= (256 if old == 'fakeIpFilter' else 16), 'DNS 地址或过滤列表无效')
            check(old not in ('nameservers', 'defaultNameservers') or bool(value), '至少设置一个 DNS 地址')
            for entry in value:
                check(bool(text(entry, 'DNS 地址或过滤域名', 2048).strip()) and not any(c.isspace() for c in entry), 'DNS 地址或过滤域名不能含空白')
                if old == 'defaultNameservers':
                    try: ipaddress.ip_address(entry)
                    except ValueError: raise ValueError('引导 DNS 必须是 IP 地址') from None
        if old == 'proxyNameservers' and not value:
            value = settings.get('nameservers', DEFAULTS['dns']['nameserver'])
        dns[new] = copy.deepcopy(value)
    if 'localDns' in settings:
        import re
        value = settings['localDns']
        check(isinstance(value, str) and re.fullmatch(r'127\.0\.0\.1(?::[0-9]{1,5})?', value), '本地 DNS 请使用回环地址')
        port = int(value.split(':')[1]) if ':' in value else 53
        check(1 <= port <= 65535 and port not in [settings.get(k) for k in ('mixedPort','redirPort','tproxyPort','dnsPort','controllerPort')], '本地 DNS 端口无效或冲突')
        dns['nameserver-policy'] = {domain: [value] for domain in ('+.lan', '+.local')}
    if 'tunStack' in settings:
        check(settings['tunStack'] in ('mixed', 'system', 'gvisor'), 'TUN 协议栈无效')
        tun['stack'] = settings['tunStack']
    if 'tunMtu' in settings:
        check(type(settings['tunMtu']) is int and 1280 <= settings['tunMtu'] <= 9000, 'tunMtu 超出允许范围')
        tun['mtu'] = settings['tunMtu']
    policy = {'ipv6': settings.get('ipv6', False)}
    dns['ipv6'] = settings.get('ipv6', False)
    policy['dns'] = dns
    if tun: policy['tun'] = tun
    used = {item.get('id') for item in data['overrides'] if isinstance(item, dict)}
    ident, index = 'fii_legacy_mihomo', 1
    while ident in used:
        ident = 'fii_legacy_mihomo_' + str(index); index += 1
    data['overrides'].insert(0, {'id': ident, 'name': 'Mihomo 基础配置（旧版迁移）',
        'type': 'YAML', 'global': True, 'enabled': True, 'origin': 'legacy-mihomo-settings',
        'body': json.dumps(policy, ensure_ascii=False, indent=2)})
    for key in LEGACY_KEYS: settings.pop(key, None)


def dns_options(config=None):
    """Read validated routing inputs from the generated Mihomo configuration."""
    from .model import check
    if isinstance(config, str):
        from .render import load_map
        config = load_map(config)
    dns = (config or {}).get('dns', {})
    check(isinstance(dns, dict), 'dns 覆写必须是对象')
    mode = dns.get('enhanced-mode', DEFAULTS['dns']['enhanced-mode'])
    check(mode in ('fake-ip', 'redir-host'), '输出 DNS 模式无效')
    check(type(dns.get('enable', True)) is bool, 'dns.enable 必须是布尔值')
    result = {'mode': mode, 'enabled': dns.get('enable', True)}
    for field, key, version in [('fake-ip-range', 'fake4', 4), ('fake-ip-range6', 'fake6', 6)]:
        value = dns.get(field, DEFAULTS['dns'][field])
        check(isinstance(value, str), field + ' 必须是网段文本')
        try: network = ipaddress.ip_network(value, strict=False)
        except ValueError: raise ValueError(field + ' 网段无效') from None
        check(network.version == version and network.prefixlen > 0, field + ' 网段无效')
        result[key] = str(network)
    return result
