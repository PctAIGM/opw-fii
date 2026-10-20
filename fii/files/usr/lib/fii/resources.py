"""Inspect the applied core resources and update them through Mihomo's API."""
from urllib.parse import quote, urlsplit, urlunsplit

from . import runtime
from .model import check
from .store import lock, rooted


GEO_FILES = (
    ('geosite', 'GeoSite.dat', ('geosite.dat',)),
    ('geoip', 'GeoIP.dat', ('geoip.dat',)),
    ('asn', 'ASN.mmdb', ('asn.mmdb',)),
    ('mmdb', 'geoip.metadb', ('country.mmdb', 'geoip.db', 'geoip.metadb')),
)


def source_url(value):
    """Keep resource locations useful without exposing URL authentication."""
    if not isinstance(value, str) or not value:
        return ''
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return ''
        host = parts.hostname
        if ':' in host:
            host = '[' + host + ']'
        if parts.port:
            host += ':' + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except ValueError:
        return ''


def geo_files(config):
    home = rooted('/etc/fii/core')
    # Mihomo searches these file names case-insensitively in sorted order.
    files = sorted(home.iterdir(), key=lambda item: item.name) if home.is_dir() else []
    urls = config.get('geox-url') or {}
    urls = urls if isinstance(urls, dict) else {}
    result = []
    for kind, default, aliases in GEO_FILES:
        path = next((item for item in files if item.name.lower() in aliases and item.is_file()), home / default)
        stat = path.stat() if path.is_file() else None
        result.append({'id': kind, 'name': kind.upper(), 'file': path.name,
                       'exists': stat is not None, 'size': stat.st_size if stat else None,
                       'modifiedAt': int(stat.st_mtime) if stat else None,
                       'url': source_url(urls.get(kind))})
    return result


def rule_providers():
    reply = runtime.controller('/providers/rules')
    providers = reply.get('providers') if isinstance(reply, dict) else None
    check(isinstance(providers, dict), '规则资源响应无效')
    return providers


def rule_view(name, value, configured=None, running=True):
    configured = configured if isinstance(configured, dict) else {}
    vehicle = value.get('vehicleType') or configured.get('type', '')
    count = value.get('ruleCount')
    return {'name': name, 'vehicleType': vehicle,
            'behavior': value.get('behavior') or configured.get('behavior', ''),
            'format': value.get('format') or (configured.get('format', 'yaml') if configured else ''),
            'ruleCount': count if type(count) is int and count >= 0 else None,
            'updatedAt': value.get('updatedAt') if isinstance(value.get('updatedAt'), str) else '',
            'url': source_url(configured.get('url')),
            'canUpdate': running and str(vehicle).lower() in ('http', 'file')}


def view():
    bundle = runtime.active()
    config = runtime.load_map(bundle['yaml']) if bundle else {}
    running = bool(bundle and rooted('/tmp/fii/ready').exists() and runtime.ready(bundle))
    configured = config.get('rule-providers') or {}
    configured = configured if isinstance(configured, dict) else {}
    result = {'running': running, 'source': 'runtime' if running else 'snapshot' if bundle else 'none',
              'geodataMode': config.get('geodata-mode') is True,
              'geoAutoUpdate': config.get('geo-auto-update') is True,
              'geoUpdateInterval': config.get('geo-update-interval', 24),
              'geo': geo_files(config), 'rulesets': [], 'rulesError': ''}
    if running:
        try:
            providers = rule_providers()
            result['rulesets'] = [rule_view(name, value, configured.get(name))
                                 for name, value in providers.items() if isinstance(value, dict)]
        except ValueError as exc:
            result['rulesError'] = str(exc)
    else:
        result['rulesets'] = [rule_view(name, {}, value, running=False)
                             for name, value in configured.items() if isinstance(value, dict)]
    return result


def update(kind, name=None, progress=None):
    check(kind in ('geo', 'ruleset'), '资源类型无效')
    check((kind == 'geo' and name is None) or
          (kind == 'ruleset' and isinstance(name, str) and bool(name) and len(name) <= 1024), '资源名称无效')
    # Service/apply/core replacement uses the same lock. Never race a download
    # against a new active snapshot, restart, or a second manual resource update.
    with lock('operation', timeout=1):
        bundle = runtime.active()
        check(bundle and rooted('/tmp/fii/ready').exists() and runtime.ready(bundle),
              '内核已停止，请启动服务后更新资源')
        if kind == 'geo':
            if progress:
                progress('正在由内核更新已启用的 GEO 数据…')
            runtime.controller('/configs/geo', method='POST', timeout=600)
            message = 'GEO 更新完成；仅更新内核已启用的数据，未启用的数据会跳过'
        else:
            value = rule_providers().get(name)
            check(isinstance(value, dict), '运行中的内核没有此规则资源，请刷新列表')
            check(str(value.get('vehicleType', '')).lower() in ('http', 'file'),
                  '此规则资源不支持更新（内联规则请通过覆写修改）')
            if progress:
                progress('正在更新规则资源：' + name)
            runtime.controller('/providers/rules/' + quote(name, safe=''), method='PUT', timeout=120)
            message = '规则资源已更新：' + name
    return {'message': message}
