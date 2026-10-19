# FII — OpenWrt 上简单的 Mihomo 配置管理

FII 面向 **OpenWrt 24.10.8 / x86_64（Intel J4125）**，把日常配置整理为：

```text
订阅源 → 节点筛选和前缀 → 全局覆写 → 输出专属覆写 → 输出 YAML
                                                        ↓
                                            运行参数 → 校验 → 应用到路由器
```

配置管理方式参考本地 `sub-converter-flutter`；OpenWrt 服务生命周期、配置测试、防火墙重载和清理机制参考 [OpenWrt-nikki](https://github.com/nikkinikki-org/OpenWrt-nikki)。FII 使用独立的配置目录、rpcd 接口和 nftables 表。

当前为 **0.1.1-11 试用包**。应用安装包不包含内核，也不依赖内核软件包；安装后在 LuCI 下载或更新 Release / Alpha 内核。已在 iStoreOS 24.10.8（QEMU/x86_64）完成真实安装和运行验证，并在隔离客户端与服务器之间完成实际网络检查，覆盖路由器本机与 LAN 的 TCP、UDP、DNS、IPv6、访问控制及防火墙重载。实体设备性能、真实交换网络和重启自启仍需单独验收。本机没有 Linux OpenWrt SDK，IPK 通过随附工具构建并检查包结构，SDK Makefile 留作标准 feed 构建入口。实测记录见 docs/VALIDATION.md。

## 功能

| 页面 | 功能 |
| --- | --- |
| 概览 | Release / Alpha 内核下载与更新、版本、运行状态、启停、日志、代理组节点切换 |
| 订阅源 | URL / 本地 YAML / 常见分享链接、前缀、保留/排除正则、节点预览、流量和到期时间、自动刷新 |
| 覆写 | YAML / RULES / JS、本地编辑 / 远程更新、全局覆写 |
| 输出配置 | 多订阅聚合、有序选择覆写、预览和下载、应用为路由器配置 |
| 路由器设置 | TCP / UDP 独立模式与 TUN、LAN / 本机有序访问规则、IPv4 / IPv6 与 DNS 独立接管、大陆地址 / DSCP / FWMark / 端口绕过、保留网段、DNS、运行参数、备份迁移、保存并应用 |

- 流量耗尽的订阅只在生成输出时排除，保留关联、启用状态和刷新任务。只有正数总额度且已报告使用量达到总额时才判定耗尽，未知/零额度保留。
- 远程刷新失败保留上次成功缓存；重新渲染失败或没有节点时保留当前运行配置。
- 应用前执行 `mihomo -t`；切换失败尝试恢复上一次运行配置；恢复仍失败时停止服务并清理接管。
- 配置库原子写入、进程锁和 revision 冲突检测，避免后台刷新覆盖正在编辑的数据。
- LuCI 经 rpcd 白名单调用，无通用命令执行或任意文件读写权限。下载、启停和应用使用可轮询的后台任务。
- 配置处理使用 Python 3 / PyYAML，按需运行；没有常驻的 Python 配置服务。Mihomo 由 procd 托管，订阅刷新由 cron 触发。

## 安装到 J4125 路由器

构建后的文件位于 `dist/`：

| 文件 | 用途 |
| --- | --- |
| `fii_0.1.1-11_all.ipk` | 配置管理、内核下载器、rpcd、procd、DNS 与透明代理 |
| `luci-app-fii_0.1.1-11_all.ipk` | LuCI 管理页面，中文界面 |
| `SHA256SUMS` / `manifest.json` | 文件校验值及包信息 |

先停止 Nikki、OpenClash 等已有透明代理的接管，避免监听端口和策略路由冲突。FII 下载的内核使用自己的 `/etc/fii/bin/mihomo` 路径，不覆盖其他插件或软件包的 `/usr/bin/mihomo`。

将需要的 IPK 上传到路由器 `/tmp/`，在路由器 SSH 中执行：

```sh
opkg update
opkg install /tmp/fii_0.1.1-11_all.ipk /tmp/luci-app-fii_0.1.1-11_all.ipk
```

依赖由已配置的 OpenWrt 软件源安装：`python3`、`python3-yaml`、`node`、`ca-bundle`、`firewall4`、`ip-full`、`kmod-nft-tproxy`、`kmod-nft-socket`、`kmod-tun`、`kmod-dummy`、`luci-base`。内核模块必须与实际固件内核版本匹配，安装错误应先修复固件软件源，不要强制忽略依赖。

安装后刷新 LuCI，打开 **服务 → FII**。默认不启用开机启动，也不会自动应用订阅。

1. 在“概览 → Mihomo 内核管理”选择 **Release（稳定版）或 Alpha（开发版）**，点击“检查更新”，再点击“下载并安装内核”。也可以直接下载当前所选渠道的最新内核。
2. 添加订阅 URL 后刷新，或直接粘贴本地 Mihomo YAML / 分享链接。
3. 按需添加覆写。`examples/groups.yaml` 提供自动选择、全部节点和简单规则，不需要 Geo 数据文件。
4. 创建输出，选择订阅和覆写。用上移/下移控制组合顺序。
5. 在“路由器设置 → 代理设置”选择 TCP / UDP 模式；在“局域网代理”选择接口，在“本机代理”配置本机流量。按需添加访问规则、绕过策略并开启 IPv6。
6. 预览输出，点击“应用到路由器”。概览中可以切换代理组节点。
7. 需要重启自动恢复时，在“运行设置”打开“开机自动启动”，点击“保存并应用”。

首次实际应用会生成 `/etc/fii/active.json`。编辑配置库不会立即影响正在运行的版本；服务重启使用上次应用的快照。路由器设置中的“保存设置”只保存配置，“保存并应用”会校验并重新应用当前输出，失败时保留或恢复上一份运行配置。

## 路由器设置

代理功能按 Nikki 的五组设置组织，另保留 DNS、运行设置与备份迁移：

| 分类 | 配置项 |
| --- | --- |
| 代理设置 | 透明代理总开关；TCP 独立选择关闭 / REDIRECT / TPROXY / TUN；UDP 独立选择关闭 / TPROXY / TUN；IPv4 / IPv6 代理与 DNS 接管开关；Fake-IP Ping |
| 局域网代理 | 接口选择；可排序访问规则；IP、IPv6、网段、MAC 条件；每条规则独立控制流量代理与 DNS |
| 本机代理 | 可排序访问规则；用户、用户组、CGroup v2 条件；每条规则独立控制流量代理与 DNS |
| 绕过设置 | 中国大陆 IPv4 / IPv6；TCP / UDP 目标端口范围与常用端口预设；DSCP；FWMark / 掩码；额外直连网段 |
| 其他设置 | IPv4 / IPv6 保留地址；TUN 协议栈、MTU、接口等待时间与检查间隔 |
| DNS | Fake-IP / Redir-Host；局域网 / 本机 DNS 接管；上游、节点域名、引导与本地域名 DNS；Fake-IP 过滤 |
| 运行设置 | 开机启动、订阅更新后自动应用、监听端口、控制器密钥、下载 User-Agent |
| 备份与迁移 | FII / SubConverter Flutter 备份导入与导出 |

访问规则从上到下匹配，**第一条命中决定行为**；一条规则内任一条件匹配即可命中，条件为空时匹配全部。未命中任何规则的流量维持原路径。可用“直连流量、接管 DNS”或“代理流量、保留原 DNS”等组合。旧版设备范围与系统用户绕过在页面保存时转换成等价规则。

备份导入支持不超过 16 MiB 的 FII / SubConverter Flutter JSON 文件。检查和导入采用分块传输与后台任务，页面显示传输进度；检查通过后再确认替换配置库。大配置的读取、保存与备份导出也使用分块传输，避免超过 LuCI RPC 的消息大小限制。导入保留当前路由器设置和已应用的运行快照；格式错误或检查失败时不修改配置库。

不同固件或反向代理的消息上限不同时，传输会在失败后自动缩小分块，最低到 1 KiB，再重试当前块；回包也采用同样的调整方式。上传重试会核对已收到的字节，避免回执丢失后重复追加。前后端传输协议不匹配时会提示同时升级两个包；仍无法传输时会显示失败的操作和参数大小。

TUN 支持与 REDIRECT、TPROXY 混用。FII 管理独立策略路由，不开启 Mihomo 的全局自动路由。核心出站标记绕过所有接管规则；停止服务会清理 TPROXY / TUN 路由、nft 表和 FII 的 IPv6 Fake-IP 虚拟接口。使用 TUN 需要固件匹配的 `kmod-tun`，IPv6 Fake-IP 路由需要 `kmod-dummy`。

大陆地址列表随安装包提供，来源与快照版本见 `NOTICE.md`。保留地址、DSCP 和端口范围不会放走 Fake-IP 流量；FWMark 与访问规则仍可明确绕过。更改运行设置后可直接“保存并应用”。

## 页面内下载和更新内核

- Release 读取官方仓库的最新正式版；Alpha 读取官方 `Prerelease-Alpha` 发布。下载器固定选择 **Linux amd64-compatible**，适合 J4125，不要求 amd64-v3 指令集。
- 页面显示当前安装渠道、版本、所选渠道的新版本和下载进度。渠道选择会保存，切换渠道后可以下载并替换当前内核。
- Alpha 发布标签固定不变，因此使用资产提交版本和 SHA-256 摘要判断更新，不仅比较标签或发布创建日期。
- 只接受官方发布元数据中的 SHA-256；校验压缩包大小、摘要、ELF64 x86_64 架构、静态加载特征及内核版本输出。缺失摘要、下载不完整或文件不匹配时保留原内核。
- 下载和校验期间现有服务继续运行。有运行快照时先用新内核测试当前配置；通过后才停止旧服务、原子替换并恢复运行。启动失败时恢复原二进制和版本记录，再尝试恢复服务。
- 原来停止的服务在更新后仍保持停止。首次下载不自动接管流量，下载后再应用输出配置。
- 需要路由器能够通过 HTTPS 访问 GitHub API 和官方发布资产。网络或请求频率错误会在页面显示，不会替换当前内核。
- 内核和上次版本位于 `/etc/fii/bin/`，渠道与安装记录位于 `/etc/fii/core-channel.json` 和 `/etc/fii/core-install.json`，随 `/etc/fii/` 纳入 sysupgrade 保留范围。

## 配置语义

订阅只提取节点；机场自带的规则、代理组与端口不会自动并入输出。FII 先生成默认 `PROXY` 代理组，再执行覆写。

全局覆写按覆写列表顺序执行，然后按输出的专属覆写选择顺序执行，同一覆写只运行一次。YAML 支持：

```yaml
# 前插列表
+rules:
  - DOMAIN,example.com,DIRECT

# 追加列表
rules+:
  - DOMAIN-SUFFIX,example.org,DIRECT

# 完整替换列表或对象
proxy-groups!:
  - name: PROXY
    type: select
    proxies: '{{proxy_names}}'
```

普通对象递归合并，普通列表替换；`{{proxy_names}}` 展开为筛选、重命名和去重后的节点名称列表。RULES 覆写每行一条规则，跳过空行和 `#` 注释，插入到当时的 `rules` 开头；多个 RULES 覆写中后执行的规则在更前面。

JS 覆写兼容 SubConverter Flutter 的同步入口 `main(config)`，接收当时的完整配置对象，返回修改后的对象。YAML、RULES、JS 按上述顺序逐项执行，可以交错排列；每个 JS 都有独立上下文，不继承前一个脚本的全局变量。可参考 `examples/override.js`：

```js
function main(config) {
  config.rules.unshift('DOMAIN,example.com,DIRECT');
  return config;
}
```

保存、导入和远程刷新时校验 JS 语法与 `main` 入口；预览和应用时才调用 `main(config)`。语法错误、执行异常、非法返回值或超时会中止当前操作，保留已保存的有效脚本或当前运行配置。JS 需要 `node` 依赖；只在校验和生成时启动子进程，Linux 路由器上以 UID/GID 65534 运行，不常驻。

执行环境不注入 `require`、`process`、文件、网络或定时器 API，禁用动态代码生成；不支持 async/Promise 返回。单阶段执行上限 2 秒，子进程总等待上限 6 秒，V8 旧生代堆上限 128 MiB，配置输入/输出上限 8 MiB。上下文隔离和进程限制不等同于独立的安全沙箱；Node 官方也明确说明 [vm 不应作为安全机制](https://nodejs.org/download/release/latest-v21.x/docs/api/vm.html)。

运行时最后统一设置监听、控制器、DNS 和流量标记，因此路由器集成字段不会因覆写导致防火墙与核心配置不一致。通用输出预览和“路由器运行 YAML”预览都可下载，后者含控制器密钥。

## 透明代理边界

- 接管所选局域网接口，以及按需开启的路由器本机出站。TPROXY 本机流量经 OUTPUT 标记、策略路由和回环接收；REDIRECT 的本机 TCP 在 OUTPUT 重定向。核心自身出站设置独立标记，避免环路。
- TPROXY 使用标记 `0xf101/0xffff`、路由表 `20230`、优先级 `30000`；TUN 使用 `0xf102/0xffff`、路由表 `20231`、优先级 `30001`。核心绕过标记为 `0xf100`，设置代理标记时保留高 16 位。
- 使用独立 `inet fii` 表；不执行 `flush ruleset`。fw4 重载后重新安装 FII 规则。
- 常规 TCP/UDP 53 端口按代理范围重定向到 Mihomo DNS，保留 dnsmasq 配置。DoH/DoT 不在此接管范围内。本机发往本机 DNS 的查询保留，由该 DNS 服务的上游查询进入本机 DNS 接管。
- 默认将 `.lan` / `.local` DNS 查询交回 `127.0.0.1:53` 的 dnsmasq。可在“高级 DNS 设置”修改本地 DNS 端口；自定义域名可通过 YAML 覆写 `dns.nameserver-policy`。
- 默认 IPv6 接管关闭；未接管的 IPv6 流量保持原路径。开启后生成对应 IPv6 透明代理和 DNS 规则。
- 本机混合代理默认 `127.0.0.1:7890`，控制器默认 `127.0.0.1:9090` 且使用随机密钥。透明代理监听面向本机和 LAN，由 OpenWrt 防火墙限制访问。
- procd 托管一个核心包装进程；核心退出或服务停止时清理规则。该行为是恢复原有网络路径，不提供 kill switch。
- LAN 防火墙区域需允许访问路由器 DNS 端口；标准 LAN 区域满足这一点。自定义 guest/隔离区域需要结合自己的区域策略验证。
- `仅内核` 模式只提供本机监听，不接管 LAN。

当前尚未提供网页订阅发布、WebDAV/Gist 同步、二维码、节点预解析或内置 Zashboard。支持常见 SS / VMess / VLESS / Trojan / Hysteria2 分享链接及 Base64 订阅；不支持的协议或传输明确报错，可先转换成完整 Mihomo YAML 再导入。

## 从 SubConverter Flutter 迁移

在 SubConverter Flutter 导出完整 JSON 备份，在 FII 的“路由器设置 → 导入备份”选择文件。FII 先检查并显示摘要，再执行替换；支持 `subconverter-backup` v1。

迁移订阅、缓存、流量信息、YAML/RULES/JS 覆写、远程覆写 URL 和输出关联；保留路由器当前端口、密钥、接管设置与运行快照。JS 原文、类型和关联顺序保留；脚本校验失败时整次导入失败并保留原数据。自定义订阅 DNS / 节点预解析等未支持设置会在导入摘要中提示。

FII 自身的备份导入同样保留当前路由器设置，适合迁移配置内容。完整灾备应另外保管 `/etc/fii/`；备份包含订阅凭证和节点密码。

## 本地开发与构建

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node --check luci-app-fii/htdocs/luci-static/resources/view/fii/app.js

# 构建仅包含应用的两个安装包
python tools/build_ipk.py
python tools/verify_packages.py

# 可选：检查 Release / Alpha 发布信息、真实下载和 ELF 格式（不执行 Linux 文件）
python tools/verify_core_downloads.py

# 可选：Windows 上使用真实核心测试 YAML 和控制器
python tools/fetch_core.py --windows
python tools/verify_mihomo.py

# 本地界面预览（只监听回环，使用 build/preview-root 独立数据）
python tools/preview.py
```

打开 `http://127.0.0.1:8765`。预览的配置编辑、备份导入、输出生成和检查官方内核版本使用真实后端；服务启停、应用配置和安装 Linux 内核被禁用，不代表路由器运行验证。

标准 OpenWrt SDK / 源码树中，可以把此目录作为本地 feed：

```sh
echo 'src-link fii /absolute/path/to/opw-fii' >> feeds.conf.default
./scripts/feeds update fii
./scripts/feeds install -p fii fii luci-app-fii
make defconfig
make package/fii/compile V=s
make package/luci-app-fii/compile V=s
```

应用包不包含内核；页面下载器当前支持 Linux x86_64。

## 排障与卸载

在路由器执行：

```sh
fii status
logread -e fii
ubus -v list luci.fii
nft list table inet fii
ip -4 rule show
ip -4 route show table 20230

# 停止服务并清理接管
/etc/init.d/fii stop

# 卸载管理界面与服务；动态配置文件保留
opkg remove luci-app-fii fii
```

配置和下载的内核目录是 `/etc/fii/`，运行文件位于 `/tmp/fii/`。`/lib/upgrade/keep.d/fii` 将配置目录纳入 sysupgrade 保留范围。卸载应用时保留该目录，以便重新安装后继续使用。

代码以 GPL-3.0-or-later 提供。参考项目与核心来源见 [NOTICE.md](NOTICE.md)，验证记录与目标路由器验收步骤见 [docs/VALIDATION.md](docs/VALIDATION.md)。
