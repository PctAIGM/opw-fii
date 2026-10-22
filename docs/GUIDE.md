# FII 使用与开发指南

[返回项目首页](../README.md) · [验证记录](VALIDATION.md)

## 安装

构建后的文件位于 `dist/`：

| 文件 | 用途 |
| --- | --- |
| `fii_0.1.1-45_all.ipk` | 配置管理、内核下载器、rpcd、procd、DNS 与透明代理 |
| `luci-app-fii_0.1.1-45_all.ipk` | LuCI 管理页面，中文界面 |
| `SHA256SUMS` / `manifest.json` | 文件校验值及包信息 |

先停止 Nikki、OpenClash 等已有透明代理的接管，避免监听端口和策略路由冲突。FII 下载的内核使用自己的 `/etc/fii/bin/mihomo` 路径，不覆盖其他插件或软件包的 `/usr/bin/mihomo`。

将需要的 IPK 上传到路由器 `/tmp/`，在路由器 SSH 中执行：

```sh
opkg update
opkg install /tmp/fii_0.1.1-45_all.ipk /tmp/luci-app-fii_0.1.1-45_all.ipk
```

依赖由已配置的 OpenWrt 软件源安装：`python3`、`python3-yaml`、`node`、`ucode` 及其 `fs` / `ubus` / `uloop` 模块、`rpcd-mod-ucode`、`rpcd-mod-file`、`uclient-fetch`、`ca-bundle`、`firewall4`、`ip-full`、`kmod-nft-tproxy`、`kmod-nft-socket`、`kmod-tun`、`kmod-dummy`、`luci-base`。内核模块必须与实际固件内核版本匹配，安装错误应先修复固件软件源，不要强制忽略依赖。

0.1.1-15 起，安装/卸载脚本使用 `rpcd reload` 重新加载接口和权限，保留 LuCI 登录会话。首次从旧版升级仍可能退出一次：旧包的卸载脚本会先执行原来的 `rpcd restart`。新包安装完成后，后续安装使用保留会话的脚本。

安装后刷新 LuCI，打开 **服务 → FII**。默认不启用开机启动，也不会自动应用订阅。

1. 在“Mihomo → 内核管理”选择 **Release（稳定版）或 Alpha（开发版）**，点击“检查更新”，再点击“下载并安装内核”。也可以直接下载当前所选渠道的最新内核。
2. 添加订阅 URL 后刷新，或直接粘贴本地 Mihomo YAML / 分享链接。
3. 按需添加覆写。[`examples/groups.yaml`](../examples/groups.yaml) 提供自动选择、全部节点和简单规则，不需要 Geo 数据文件。
4. 创建输出，选择订阅和覆写。用上移/下移控制组合顺序。
5. 在“路由器设置 → 代理设置”选择 TCP / UDP 模式；在“局域网代理”选择接口，在“本机代理”配置本机流量。按需添加访问规则、绕过策略并开启 IPv6。
6. 预览输出，点击“更新运行配置”。内核停止时只更新配置，保持停止；内核运行时才重启并加载新配置。
7. 需要运行时，在“概览”点击“启动服务”；运行后可切换代理组节点。需要开机恢复时，在“运行设置”打开“开机自动启动”，点击“保存并更新配置”。

“更新运行配置”会校验输出并生成 `/etc/fii/active.json`，保留服务原有的运行/停止状态，首次生成也不自动启动。编辑配置库不会立即影响已有运行快照；服务启动/重启使用最近生成的快照。路由器设置中的“保存设置”只保存配置，“保存并更新配置”会重新生成当前输出，也保留服务状态；校验失败保留旧快照，运行中重载失败则尝试恢复旧快照和服务。概览也提供当前输出的“更新运行配置”按钮。确认不了服务状态时直接报错，不猜测状态。

概览展示运行状态和内核管理，不保留“三步开始”，也不读取代理组。“控制面板”Tab（兼容原 `#proxies` 地址）参考 zashboard 的紧凑面板和节点网格，显示策略、当前节点、成员数及延迟概况，支持统一搜索组名/节点、按延迟或名称排序。桌面点击组标题展开节点；手机双列展示代理组，点击后在弹层中查看并定位当前节点。大量节点通过虚拟滚动浏览，不需要反复点击“显示更多”。手动组运行时可点击节点切换，支持键盘方向键及 Home/End；自动测速组和故障转移组可点击节点钉住，并通过“取消钉住”恢复自动选择。钉住节点与当前使用节点分别标注，节点失效时仍遵循 Mihomo 的回退策略；负载均衡和链式组按策略展示。延迟来自内核最近的检测记录，刷新不会自动测速或清除钉住。内核停止时只读展示已经生成的配置，不推测当前节点及钉住状态；订阅提供者或 `include-all` 等动态成员会明确标注，待运行后展开。

控制面板的“外部资源”展示 GEOSITE、GEOIP、ASN、MMDB 的本地文件名、大小、修改时间、配置下载源，以及运行配置中的 GEO 模式和自动更新设置。“更新 GEO 数据”调用内核的统一更新接口，只更新内核已经启用的数据；GEOIP 与 MMDB 按 `geodata-mode` 选择，未启用资源会跳过。文件存在仅表示已下载，不能据此判定规则正在使用；修改时间也不等同于上游版本。多文件更新由内核处理，失败时可能已有部分数据更新，页面会提示检查日志并重新读取状态。

Ruleset 列表在运行时读取内核 `/providers/rules`，显示名称、来源类型、规则类型、格式、条数和内核报告的更新时间；HTTP 资源支持逐项更新，File 资源支持重新加载，Inline 资源在覆写中修改。停止时明确展示已生成配置的快照，所有更新按钮禁用。资源更新使用后台任务并显示进度和结果，不重启服务；资源配置仍在覆写中管理。来源地址展示会去除用户名、密码、查询参数和片段。

全站使用紧凑管理界面：桌面正文 13px，常规按钮与输入框约 32px 高，卡片内边距 12px；缩小页头和导航空白，宽屏订阅三列、端口设置三列，覆写操作靠右并将摘要入口合并到元数据行。控制面板的 GEO 数据宽屏四列，下载源按需展开，ruleset 使用紧凑列表；节点卡片与日志行同步收紧。触屏设备保留至少 44px 的常规按钮点击区域，窄屏输入框保留 16px 字体，支持深色模式。

界面样式地址带 CSS 内容摘要，页面等待样式加载成功后再显示。构建工具会同步摘要并核对包内 CSS，避免升级后新页面复用旧样式；样式加载失败会明确报错并允许重新载入。首次升级到包含此修复的版本后，可用浏览器 Ctrl+F5 加载新的页面代码。

## 路由器设置

路由器接管按以下分组组织，与 Mihomo 参数分开：

| 分类 | 配置项 |
| --- | --- |
| 代理设置 | 透明代理总开关；TCP 独立选择关闭 / REDIRECT / TPROXY / TUN；UDP 独立选择关闭 / TPROXY / TUN；IPv4 / IPv6 代理与 DNS 接管开关；Fake-IP Ping |
| 局域网代理 | 接口选择；可排序访问规则；IP、IPv6、网段、MAC 条件；每条规则独立控制流量代理与 DNS |
| 访问控制 | 直连设备名单；支持 MAC、IPv4、IPv6、网段；优先跳过代理、DNS 劫持与 Fake-IP Ping 接管 |
| 本机代理 | 可排序访问规则；用户、用户组、CGroup v2 条件；每条规则独立控制流量代理与 DNS |
| 绕过设置 | 中国大陆 IPv4 / IPv6；TCP / UDP 目标端口范围与常用端口预设；DSCP；FWMark / 掩码；额外直连网段 |
| 其他设置 | IPv4 / IPv6 保留地址；TUN 接口等待时间与检查间隔 |
| DNS 接管 | 只决定局域网 / 本机的 DNS 请求是否交给 Mihomo；DNS 模式、解析服务器与过滤在覆写中管理 |
| 运行设置 | 开机启动与开机延迟；每日定时重启；订阅更新后自动应用；内核运行环境（GOMAXPROCS、GOMEMLIMIT、QUIC GSO/ECN、跳过 IPv6 检查）；下载 User-Agent；端口、控制器与通用参数默认值在 Mihomo 页面管理 |
| 备份与迁移 | FII / SubConverter Flutter 备份导入与导出 |

访问规则从上到下匹配，**第一条命中决定行为**；一条规则内任一条件匹配即可命中，条件为空时匹配全部。未命中任何规则的流量维持原路径。可用“直连流量、接管 DNS”或“代理流量、保留原 DNS”等组合。旧版设备范围与系统用户绕过在页面保存时转换成等价规则。

备份导入支持不超过 16 MiB 的 FII / SubConverter Flutter JSON 文件。优先通过 LuCI 的文件上传通道一次直传，上传后在路由器后台检查，页面分别显示上传和检查进度；检查通过后再确认替换配置库。固件或反向代理不支持直传时，自动回退到兼容分块；已完整上传但回执丢失时，先核对接收状态，不再次发送文件。大配置的读取、保存与备份导出也使用分块传输，避免超过 LuCI RPC 的消息大小限制。导入保留当前路由器设置和已应用的运行快照；格式错误或检查失败时不修改配置库。

检查通过后，已校验的备份在路由器临时目录保留 15 分钟。确认导入只发送检查凭证，不再次上传文件或重复检查 YAML/JS。取消会释放临时内容；过期、路由器重启或检查后配置发生变化时，需要重新检查。确认请求重试不会重复导入。

导入会去掉地址两端多余的空白。地址校验失败时，导入窗口逐项显示订阅或覆写的名称、ID 和具体原因，区分空地址、协议、域名、认证信息与端口错误；不回显私密 URL。远程地址支持 HTTP(S)。HTTPS GitHub 地址支持 `https://TOKEN@raw.githubusercontent.com/...` 和 `https://USERNAME:TOKEN@raw.githubusercontent.com/...`，下载时取出 Token，转换为 Bearer 认证头，实际请求 URL 不含认证信息；跨域、换端口或降级到 HTTP 的重定向不会携带认证头。其他站点的内嵌用户名密码暂不支持。本地订阅可留空地址，但必须有节点缓存。

页面列表读取摘要，编辑覆写时才读取该脚本，展开订阅的“本地内容 / 上次成功缓存”时才读取缓存。保存仅提交改动字段，未修改的 YAML 和 JS 不重复解析或启动执行器；修改内容后仍校验，版本冲突仍会阻止覆盖。备份导出保留完整内容。

REALITY 的 `short-id` 在 YAML 解析时按原始文本保留，输出时加引号，避免前导零、八进制或科学计数法识别改变标识。该规则覆盖已有订阅缓存、YAML/JS 覆写、预览与路由器配置生成；端口和布尔字段仍按原类型处理。内核若仍判定 ID 无效，会显示具体节点名称及输出中的位置，保留当前运行配置。

SubConverter Flutter 备份中的覆写按 `sortOrder`、ID 恢复原应用的显示顺序，输出关联也恢复原应用的执行顺序；不带排序字段的旧备份保留数组和关联中明确给出的顺序。覆写页使用单列编号列表，支持上移、下移和移动到指定位置。全局覆写先按列表顺序执行，专属覆写在输出编辑器内勾选和排序；全局覆写自动勾选，已禁用项明确标识且不执行。输出卡片、编辑窗口及预览均可核对实际执行顺序。

本地覆写显示本地来源，远程覆写提供“刷新远程内容”和最近刷新时间。打开编辑窗口会同步最新条目及配置版本，避免后台刷新导致仅打开编辑就报版本冲突；编辑期间若另一个操作修改配置，保存仍会阻止覆盖。预览点击后立即显示窗口和订阅解析、覆写执行、结果读取进度，关闭后不会因后台任务完成而再次弹出。固件提供 libyaml 时使用安全解析器的加速实现，仍保留别名数量和循环结构检查。

不同固件或反向代理的消息上限不同时，兼容分块会在失败后自动缩小，最低到 1 KiB，再重试当前块；回包也采用同样的调整方式。上传和下载分别记忆分块大小，下载受限不会连带拖慢上传。分块重试会核对已收到的字节，避免回执丢失后重复追加。前后端传输协议不匹配时会提示同时升级两个包；仍无法传输时会显示失败的操作和参数大小。

TUN 支持与 REDIRECT、TPROXY 混用。FII 管理独立策略路由，不开启 Mihomo 的全局自动路由。核心出站标记绕过所有接管规则；停止服务会清理 TPROXY / TUN 路由、nft 表和 FII 的 IPv6 Fake-IP 虚拟接口。使用 TUN 需要固件匹配的 `kmod-tun`，IPv6 Fake-IP 路由需要 `kmod-dummy`。

大陆地址列表随安装包提供，来源与快照版本见 `NOTICE.md`。保留地址、DSCP 和端口范围不会放走 Fake-IP 流量；FWMark 与访问规则仍可明确绕过。更改运行设置后可直接“保存并更新配置”。

**运行维护**：开机延迟仅在系统启动路径生效（后台等待后启动，不阻塞其他服务），手动启动不受影响。定时重启由每分钟的调度任务检查，到达设定的每日时间且服务正在运行时执行一次重启；12 小时内的重复触发会被抑制，停止状态不会被动拉起。内核运行环境以环境变量注入（`GOMAXPROCS`、`GOMEMLIMIT`、`QUIC_GO_DISABLE_GSO`、`QUIC_GO_DISABLE_ECN`、`SKIP_SYSTEM_IPV6_CHECK`），随“更新运行配置”在下次内核启动时生效。启用 TPROXY 时若检测到 `br_netfilter` 将桥接流量送入 iptables（常见于安装 Docker / 桥接防火墙的路由器），FII 会临时关闭对应的 `bridge-nf-call-iptables` / `bridge-nf-call-ip6tables`，停止接管或关闭 TPROXY 时恢复原值；仅改回 FII 动过的开关，未加载该模块时不做任何事。

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

JS 覆写兼容 SubConverter Flutter 的同步入口 `main(config)`，接收当时的完整配置对象，返回修改后的对象。YAML、RULES、JS 按上述顺序逐项执行，可以交错排列；每个 JS 都有独立上下文，不继承前一个脚本的全局变量。可参考 [`examples/override.js`](../examples/override.js)：

```js
function main(config) {
  config.rules.unshift('DOMAIN,example.com,DIRECT');
  return config;
}
```

保存、导入和远程刷新时校验 JS 语法与 `main` 入口；预览和应用时才调用 `main(config)`。语法错误、执行异常、非法返回值或超时会中止当前操作，保留已保存的有效脚本或当前运行配置。JS 需要 `node` 依赖；只在校验和生成时启动子进程，Linux 路由器上以 UID/GID 65534 运行，不常驻。

执行环境不注入 `require`、`process`、文件、网络或定时器 API，禁用动态代码生成；不支持 async/Promise 返回。单阶段执行上限 2 秒，子进程总等待上限 6 秒，V8 旧生代堆上限 128 MiB，配置输入/输出上限 8 MiB。上下文隔离和进程限制不等同于独立的安全沙箱；Node 官方也明确说明 [vm 不应作为安全机制](https://nodejs.org/download/release/latest-v21.x/docs/api/vm.html)。

Mihomo 参数只在输出与覆写中管理：DNS 模式、解析服务器、DNS IPv6、Fake-IP 网段/过滤、域名策略、TUN 协议栈/MTU 等不再放进路由器设置。默认 Mihomo 配置在生成输出时补齐，路由器的 IPv6 流量接管开关不会改写 Mihomo 的 IPv6 参数。路由器从最终输出读取 DNS 模式和 Fake-IP 网段生成对应接管规则；输出关闭 DNS 而路由器仍要求接管 DNS 时明确报错。

Mihomo 页面另提供控制器、通用参数与 GEO 数据默认值。控制器：端口、监听地址、密钥与外部用户界面（`external-ui` 目录、子目录名 `external-ui-name`、下载地址 `external-ui-url`，留空不启用；zashboard、yacd、metacubexd 等面板经 控制器地址:端口/ui 访问，目录相对内核工作目录）。通用参数：运行模式、日志等级、进程匹配、IPv6 流量（`ipv6`，允许内核接受 IPv6 流量）、统一延迟、TCP 并发、Keep-Alive 间隔/空闲、ETag 支持。GEO 数据单独一卡：自动更新（默认开启）、更新间隔、GEOIP dat 格式与 GeoIP / GeoSite / MMDB / ASN 自定义下载地址（留空使用官方地址）。默认值与 Mihomo 官方一致的有意例外：进程匹配按官方对路由器的建议默认关闭（转发的局域网流量没有本地进程可匹配），GEO 自动更新默认开启，IPv6 流量默认关闭（与路由器 IPv6 接管默认关闭的姿态一致；官方默认开启）。生成运行配置时写入默认值，覆写中显式配置的同名字段优先于此处；自定义 GEO 地址只写入已填写的类型，其余由内核补内置地址。

旧版设置中的 DNS / TUN 参数会在读取时转换为最先执行的全局 YAML 覆写“Mihomo 基础配置（旧版迁移）”，保留原值，让现有覆写继续拥有更高优先级。读取不改写配置文件；后续保存时才持久化转换结果。迁移覆写与普通覆写一样在覆写页管理（Mihomo 页不再有专属编辑卡片）。导入旧 FII 备份时同样迁移 Mihomo 参数，同时保留目标路由器的接管设置与凭据。

运行时适配写入路由器接入所需的监听端口、控制器地址/密钥、TUN 启停/设备/路由接管及流量标记，并按 Mihomo 页面的通用参数、嗅探、TUN、GEO 与外部用户界面默认值补齐缺失字段（已存在的覆写字段保持不变；外部 UI 三项不再从覆写中剥离）。这些字段的写入会在路由器运行预览中列出，不显示密钥值。通用输出预览和“路由器运行 YAML”预览都可下载，后者含控制器密钥。

Mihomo 页按二级页签组织：「内核」为内核管理；「全局配置」为四张默认值卡；「嗅探」为嗅探默认值——启用、强制嗅探 DNS 映射、嗅探纯 IP、覆盖目标地址、HTTP/TLS/QUIC 端口与跳过域名（默认 `+.lan`、`+.local`、`+.home.arpa`、`Mijia Cloud` 等）。嗅探按字段写入运行配置：覆写只改某个字段（如仅关闭 enable 或仅改 TLS 端口）时，其余字段仍取 FII 默认值。「TUN」为虚拟网卡默认值——协议栈（mixed/system/gvisor，默认 mixed）、MTU（默认 1500，1280–9000）、GSO 通用分段卸载与 GSO 最大长度（默认 65536）、端点无关 NAT（默认关闭，P2P 兼容性优先时再开）；同样按字段写入，覆写显式配置优先。网卡名称、自动路由、自动出口探测与 DNS 劫持由 FII 透明代理接管固定，不在页面开放。

## 透明代理边界

- 接管所选局域网接口，以及按需开启的路由器本机出站。TPROXY 本机流量经 OUTPUT 标记、策略路由和回环接收；REDIRECT 的本机 TCP 在 OUTPUT 重定向。核心自身出站设置独立标记，避免环路。
- TPROXY 使用标记 `0xf101/0xffff`、路由表 `20230`、优先级 `30000`；TUN 使用 `0xf102/0xffff`、路由表 `20231`、优先级 `30001`。核心绕过标记为 `0xf100`，设置代理标记时保留高 16 位。
- 使用独立 `inet fii` 表；不执行 `flush ruleset`。fw4 重载后重新安装 FII 规则。
- 常规 TCP/UDP 53 端口按代理范围重定向到 Mihomo DNS，保留 dnsmasq 配置。DoH/DoT 不在此接管范围内。本机发往本机 DNS 的查询保留，由该 DNS 服务的上游查询进入本机 DNS 接管。
- 指定设备全部直连：打开“路由器设置 → 访问控制”，从 DHCP 设备列表选择 MAC，或填写 MAC、IPv4、IPv6、网段，然后“保存并更新配置”。直连名单在所选局域网接口上优先于普通访问规则，跳过 TCP / UDP 代理、DNS 劫持及 Fake-IP Ping 接管；移除条目并应用后恢复普通规则。MAC 可覆盖设备的 IPv4 / IPv6，IP 条目仅覆盖对应地址，地址变化后需更新或绑定静态租约。跨路由设备应按源 IP / 网段匹配。
- 直连设备需要正常 DNS 返回真实 IP；应用名单前已缓存的 Fake-IP 需要重连网络或清理缓存。不要将这些设备的 DNS 直接指向 Mihomo。若设备使用路由器 dnsmasq，需确保其上游正常解析；开启“代理路由器本机 / 接管路由器本机 DNS”后，上游查询属于路由器进程，设备名单无法再区分原始客户端，可在本机访问规则中对 DNS 进程同时关闭代理与 DNS 接管。
- 默认 Mihomo 配置将 `.lan` / `.local` DNS 查询交回 `127.0.0.1:53` 的 dnsmasq。其他本地 DNS 端口和自定义域名策略通过覆写 `dns.nameserver-policy` 管理。
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

### GitHub Actions 自动构建

仓库的 `Build IPK` 工作流在 push、pull request 和手动触发时运行，使用 Ubuntu 24.04、Python 3.11 和 Node.js 22。先运行后端测试、JavaScript 语法和日志解析/过滤检查，再使用仓库自带的构建器生成并检查两个应用 IPK；不下载或打包 Mihomo 内核，也不需要 OpenWrt SDK。

在 **Actions → Build IPK → 成功的运行 → Artifacts** 下载 `fii-ipk-运行编号-尝试次数`，解压后包含两个 IPK、`SHA256SUMS` 和 `manifest.json`。产物保留 30 天；运行摘要列出文件大小和 SHA-256。需要重新构建时，在工作流页面选择 **Run workflow**。构建不会自动创建 GitHub Release。

包版本来自 `tools/build_ipk.py`，升级版本时同时更新两个包的 Makefile。工作流使用只读仓库权限，第三方 Action 固定到提交 SHA；PR 使用普通 `pull_request` 事件。

### 本地构建与预览

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
logread -e '^\(fii\|fii-prepare\|mihomo\|core-run\)\(\[[0-9][0-9]*\]\)\{0,1\}:' -l 500
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

代码以 GPL-3.0-or-later 提供。参考项目与核心来源见 [NOTICE.md](../NOTICE.md)，验证记录与目标路由器验收步骤见 [验证记录](VALIDATION.md)。


## 概览网址延迟测试

概览页默认提供百度、Google（gstatic 的 generate_204）和 GitHub 卡片。点击“点击检测”或已有的耗时可重新测试，“全部测试”每次最多并发 3 个。通过“添加网站”输入名称和完整 HTTP(S) 地址；每个卡片可编辑或删除，最多保存 24 个，清空列表后不会重新补回默认项。列表随 FII 备份保存，修改后无需应用运行配置或重启内核。

请求由路由器发起，遵循当前路由和本机代理接管策略，不受浏览器代理设置影响。显示从开始请求到首个响应头的耗时，包含 DNS、TCP 和 HTTPS 的 TLS 协商，不跟随重定向，也不下载响应正文。它用于判断网站响应情况，不能当作 ICMP Ping 或单个代理节点的延迟。HTTP 403/404 等同样保留耗时，并用红色提示状态；DNS、TLS、连接错误单独显示。测试最多等待 8 秒。

仅手动触发请求；结果和测试时间保留在当前页面会话中，刷新后重新测试，不持久写入设备闪存。测试任务临时文件位于路由器的 `/tmp`。
