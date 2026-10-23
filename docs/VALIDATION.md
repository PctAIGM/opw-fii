# FII 0.1.1 validation

Target requested by the user: **OpenWrt 24.10.8 / Intel J4125 / x86_64**.

## Mixed listener binding and custom inbound preservation: 0.1.1-44 (2026-10-21)

The runtime overlay no longer discards custom `listeners`. Protocol, authentication, routing and other listener fields remain intact, and adding the managed default does not produce a misleading listener-loss warning. A custom entry explicitly named `fii-mixed` takes precedence over the generated default without duplication. `mixedAddress` is a validated IPv4/IPv6 setting exposed on the Mihomo page; legacy stores retain the loopback default without a write on read.

All **234 Python tests passed**, including six listener regressions covering default migration without writes, IP validation, `+listeners` rendering, credentials/routing preservation, same-name precedence and idempotence, invalid shapes, and setting save/export. Two existing maintenance environment tests were corrected to use an isolated FII_ROOT rather than write to the drive's `/tmp` equivalent. JavaScript syntax, whitespace and both application-only **0.1.1-44** package inspections passed. The actual router's Alpha core validated the candidate configuration before installation/application.

Both packages were installed with opkg on the physical router, verifying **34 payload files**, preserving revision **68**, existing active/runtime configuration, DHCP, installed core binary and enabled boot preference during package upgrade. Backup: `/etc/fii/backups/package-upgrade-0.1.1-44-20261021-131740`. Applying only `mixedAddress=192.168.100.1` then produced revision **69**, with all other runtime fields unchanged and the custom `inss` Shadowsocks listener restored with its complete configured fields. Settings/active backup: `/etc/fii/backups/listener-binding-20261021-131829`.

Real socket inspection showed mixed TCP/UDP at **192.168.100.1:7890** and Shadowsocks TCP/UDP at **:::24313** (configured as 0.0.0.0). TCP connections to both ports succeeded from the development PC. HTTPS requests through the explicit LAN HTTP proxy **192.168.100.1:7890** returned Baidu **200** and Google generate_204 **204**. The latter verifies the mixed HTTP CONNECT path; encrypted Shadowsocks client traffic and SOCKS/UDP relay traffic were not exercised. The installed LuCI page displayed the new binding setting as **192.168.100.1**; screenshot: `build/performance/router-listener-address.png`. Existing browser tabs were not modified.

## Maintenance features (restart schedule, boot delay, core env, br_netfilter): 0.1.1-43 (2026-10-21)

Four Nikki-parity maintenance features were added and verified on the iStoreOS 24.10.8 QEMU VM: **daily scheduled restart** checked by the per-minute scheduler (fires only while the service is running; a marker under `/etc/fii/scheduler.json` suppresses duplicate fires within 12 hours), **boot start delay** implemented as a backgrounded `boot()` sleep that reads `fii boot-delay` (manual start unaffected), **Go runtime environment variables** (`GOMAXPROCS`/`GOMEMLIMIT`/`QUIC_GO_DISABLE_GSO`/`QUIC_GO_DISABLE_ECN`/`SKIP_SYSTEM_IPV6_CHECK`) written to `/tmp/fii/core-env` by `prepare()` and sourced by `core-run`, and **br_netfilter compatibility** (TPROXY up flips `bridge-nf-call-iptables`/`ip6tables` from 1 to 0 with flag files under `/tmp/fii/`; firewall-down restores exactly the flipped sysctls).

Python coverage grew from 208 to **228 tests** (new `tests/test_maintenance.py`: defaults migration on old stores, range/format validation, env mapping, `prepare()` env file, restart firing/suppression/stopped-service cases, `boot-delay` CLI, sysctl flip/restore/idempotence). VM evidence: `mihomo` PID changed **7942 → 8998** when the wall clock reached the configured minute and the marker `{"restartAt": 1792550221}` was written; a second `fii scheduled` in the same minute did not restart (DUP-SUPPRESSED). After applying settings `goMaxProcs=2, goMemLimit=256, disableQuicGso=1`, the running core's `/proc/<pid>/environ` contained `GOMAXPROCS=2`, `GOMEMLIMIT=256MiB`, `QUIC_GO_DISABLE_GSO=1`; restoring defaults yields an empty env file. With `bridge-nf-call-iptables` forced to 1, `firewall-up` set it to 0 with the flag present and the nft table installed; `firewall-down` restored 1, removed the flag and the table. A cold boot with `bootDelay=15` logged `Mihomo ready` at **10:47:52** against kernel start 10:47:34 (≈18 s including the sleep; an immediate start would be ready ≈10 s earlier), and a manual `boot()` invocation showed the `sleep 15` process followed by the service starting. LuCI's 运行设置 section renders the new 启动与更新 fields and the 内核运行环境 card; the daily time input enables only with the restart toggle, and a UI save round-trip (`03:05`, GOMAXPROCS 2) persisted and reloaded correctly. Screenshot: `build/vm/maintenance-settings-ui.png`. Post-test regression: apply, ready file, nft table `dns_hijack` chain, policy rule `30000 … lookup 20230` and the running core were re-verified after restoring neutral settings.

## Control-panel group order and collapse: 0.1.1-42 (2026-10-20)

Running groups now follow the active YAML `proxy-groups` array, independently of controller JSON object order or a customized GLOBAL member list. Python orders the compact live result against that snapshot. The native RPC parses just the ordered names through the private worker once per active-file signature and keeps them in memory; subsequent reads use the native controller path, and an active-snapshot change invalidates the cache. Live groups absent from the snapshot remain appended. No order cache is written to disk.

Group cards are direct row-major grid children instead of alternating column containers, so desktop, narrow single-column and phone layouts retain configuration order. Each desktop card shows its expand/collapse action; matching nodes found by search can also be manually collapsed. A separate header button collapses the entire proxy-group panel while leaving its heading/status visible, moving external resources directly below it. Panel state survives tab changes within the current page instance; node selections and configuration are unaffected.

Validation passed: **208 Python tests**, including YAML order winning over JSON/custom GLOBAL order and an order-only read making no controller request; **18 isolated native RPC checks on the physical router**, including one-time parsing, cache reuse and active-snapshot invalidation; JavaScript syntax and both application-only 0.1.1-42 package inspections. Interactive Chrome QA with the local fixture covered a **512-node** group, manual collapse during node search, whole-panel collapse, **805px** single-column and **390px** two-column order without horizontal overflow, and the phone node modal.

Six files were hot-updated on the actual router (Python runtime/CLI, native RPC, view JavaScript, stylesheet and menu resource version), with originals under `/etc/fii/backups/control-panel-order-20261020-221344`. The public native RPC returned all **17 groups** in the exact active-configuration order on repeated reads. The installed LuCI page confirmed the same DOM/visual order, expansion and collapse of the real **499-node** group (36 virtualized nodes rendered at the checked viewport), and whole-panel collapse with external resources visible at the top of the content area. Evidence: `build/performance/control-panel-live-order.json` and `router-control-panel-collapsed.png`. State revision **52**, active snapshot, runtime YAML, DHCP configuration and core binary hashes were unchanged; the Mihomo PID was unchanged and no Mihomo restart occurred. The package database remains **0.1.1-40 with hotfixes**; the newly built **0.1.1-42** packages were inspected locally, not fully installed through opkg.

On 2026-10-21 the proxy panel outer container was given the same shared border, radius, surface background and padding rule as external resources (12px desktop, 10px phone). The stylesheet plus its view/menu cache hashes were hot-updated, preserving current configuration/core hashes and the Mihomo PID; originals are under `/etc/fii/backups/panel-border-20261021-074134`. The user's current Chrome tab confirmed both panels compute to the same **1px border, 6px radius, 12px padding and white background** in the checked desktop theme, including when the proxy panel is collapsed. Evidence: `build/performance/router-control-panel-border.png`. The application-only 0.1.1-42 packages were rebuilt and inspected after this style change; the physical package database remains 0.1.1-40 with hotfixes.

Subsequent user-requested full package installation on 2026-10-21 upgraded both opkg records to **0.1.1-42** and verified **34 installed payload files** (hashes and permissions). The first SSH-driven attempt did not complete its checks and rolled back; a detached router-side installer with persistent logging then completed the upgrade and validation. State and active revision **61**, state/active bytes, runtime YAML, installed Mihomo binary and DHCP configuration were preserved, as was the enabled boot preference. FII restarted through the normal upgrade hooks and its management API recovered. Persistent backup: `/etc/fii/backups/package-upgrade-0.1.1-42-20261021-074551`. Post-install router-origin HTTPS without an explicit proxy returned Baidu **200**, Google generate_204 **204**, and GitHub **200**. A separate authenticated Chrome tab verified the installed 17-group configuration order, panel collapse, border styling and external resources; the user's open override editor was left untouched. Screenshot: `build/performance/router-package42-installed.png`.

## Access-control device choices and labels: 0.1.1-41 (2026-10-20)

The shared entries editor now filters already-added values out of its suggestions, restores choices when entries are removed, and compares MAC addresses without case sensitivity. Duplicate DHCP suggestions are collapsed. When every suggestion has been added, the picker displays an empty-state label and is disabled until a choice becomes available again. The direct-device list displays DHCP device name, IP and MAC for matching entries, including manually entered MAC/IP values; unknown addresses retain their original text. Stored match values and firewall semantics are unchanged.

The isolated real-browser test (`tools/verify_access_ui.cjs`) passed six checks including immediate filtering/restoration, duplicate DHCP/MAC handling, name/IP/MAC labels across save/reload, unknown-address fallback, invalid-save preservation and 390px layout. Both application-only 0.1.1-41 packages passed inspection.

The physical router received the corrected view script and menu resource version as hotfixes; its package database remains **0.1.1-40**. State, active snapshot, runtime YAML and core binary hashes were unchanged, and the Mihomo PID did not change. The current user configuration was revision **52**, with **10 selected devices**. A fresh authenticated Chrome session using only installed assets verified all ten device labels, exclusion of all selected devices from the dropdown, unique candidate MACs, no page errors and no horizontal overflow at 390px; it made no configuration writes. Evidence: `build/access-control/access-device-labels-browser-result.json`. The original view is backed up under `/etc/fii/backups/access-ui-hotfix-20261020-183232`. A complete 0.1.1-41 package installation was not performed.

The user's existing Chrome tab still reproduced the old MAC-only list and unfiltered choices after reload, despite the fresh-session checks passing. The installed LuCI dispatcher supports `action.version` on view menus and passes it to `ui.instantiateView`; the old FII action supplied no independent version, so its XHR view URL retained LuCI's shared resource version. The package builder now records the packaged JavaScript SHA-256 prefix as the menu action version, and package inspection verifies the match. The router menu was updated to `38731c807d9ed5af`, the existing `/tmp/luci-indexcache.44d9aa60.json` was cleared, and rpcd reloaded. The same original Chrome tab then showed all **10 device name / IP / MAC labels** and only **7 unselected choices** after an ordinary reload, without changing saved settings. Evidence: `build/access-control/user-chrome-access-fixed.png`. The menu backup is `/etc/fii/backups/ui-cache-version-20261020-184105`; protected configuration/core hashes and the Mihomo PID remained unchanged across this deployment.

## Direct device access control: 0.1.1-40 (2026-10-20)

Router settings now provide a dedicated access-control section for devices that bypass FII. `lanBypassDevices` accepts MAC, IPv4, IPv6 and CIDR entries. A terminal LAN decision is inserted before explicit and legacy access rules, covering TPROXY, REDIRECT, TUN, DNS interception and Fake-IP Ping. An empty list retains existing behavior; router-process rules remain separate. The setting uses existing partial-save, export and activation snapshot paths. Existing subscription/override/output semantics are preserved.

Validation passed:

- **207 Python tests**, including priority over explicit/legacy rules, IPv4/IPv6/MAC matches, terminal decisions for all four firewall actions across three TCP/UDP mode combinations, disabled interception, old-setting defaults, input rejection, partial-save/export persistence and atomic invalid-save rejection.
- `tools/verify_access_ui.cjs` uses an isolated local Python backend with DHCP/network fixtures and real Chrome. Five browser checks cover DHCP MAC selection plus an unsubmitted IPv6 draft, switching settings sections, save/reload/export, invalid-input revision preservation, removing all entries, and a 390px viewport without page overflow. No JavaScript page errors occurred. Desktop/mobile screenshots and the result are under `build/access-control/`. This is the local LuCI view harness, not installed-router validation.
- JavaScript syntax, diff whitespace checks and both application-only IPK metadata/content checks passed. Packages contain no Mihomo binary and retain the existing managed Release/Alpha core workflow.

Device DNS must resolve real IPs. Previously cached Fake-IP answers need to expire or be cleared. If clients use dnsmasq and router-process DNS is also intercepted, its upstream queries have the router's identity; the device list cannot distinguish the original clients. The UI displays a notice when router proxy and router DNS interception are enabled, and the guide explains bypassing the DNS process in router access rules. The initial feature validation did not include physical-router installation or real client forwarding checks.

Subsequent requested deployment on the physical iStoreOS 24.10.8 / J4125 router installed both **0.1.1-40** packages and verified **34 installed payload files** against the locally checked packages. Configuration revision **51**, the state and active snapshot bytes, runtime YAML, Mihomo binary and DHCP configuration were unchanged; service operation and the enabled boot preference were preserved. A first installer check incorrectly required opkg's `install ok installed` status instead of accepting the router's `install user installed` status and triggered an automatic downgrade; after correcting the verifier, the upgrade and checks completed successfully. Private persistent backups remain under `/etc/fii/backups/package-upgrade-0.1.1-40-20261020-182354`.

The installed LuCI assets passed a fresh authenticated, read-only Chrome check: access control visible, save/apply available, running state and applied revision 51, no page errors, and no horizontal page overflow at 390px. No configuration was saved by that browser check. Router-origin HTTPS without an explicit proxy returned Baidu **200**, Google generate_204 **204**, and GitHub **200** after upgrade. Evidence is in `build/access-control/package40-browser-result.json` and `package40-network-check.txt`. Assigning a physical LAN device to the bypass list and checking that device's forwarding/DNS behavior remains untested.

## Running core log read fix: 0.1.1-39 (2026-10-20)

On the physical router, the installed `luci.fii logs` RPC returned zero records while Mihomo was running and the system ring buffer contained hundreds of `core-run[PID]` records. Two assumptions in the old filter were wrong: ubox `logread -e` compiles a basic regular expression (`REG_NOSUB`, without `REG_EXTENDED`), and applies it to the raw tag-prefixed message before formatting its timestamp/facility. The old extended-expression syntax and leading space therefore matched nothing. The filter also omitted the `core-run` tag used by procd for the wrapper's inherited stdout/stderr. Earlier mock query assertions and stopped-core browser validation did not expose these faults.

Both Python fallback and native ucode now use an anchored BRE for the exact `fii`, `fii-prepare`, `mihomo` and `core-run` tags, with an optional numeric PID. Unrelated cron/access records containing FII paths remain excluded. The frontend recognizes `core-run` as a core source even when startup errors lack embedded logfmt/JSON levels. Existing log size bounds, preparation snapshot separation and error propagation are retained. The command examines the system buffer's last 500 messages before filtering, as ubox implements `-l`; it does not guarantee 500 matching service records.

Validation passed:

- All **203 Python tests**, JavaScript syntax and the actual parser check (`tools/verify_log_parser.cjs`), including raw startup errors, logfmt/JSON severity and application/preparation separation.
- The candidate passed all **18 isolated native ucode checks** on the actual router. The candidate Python query passed the router's real BRE engine with **8 accepted tag/PID forms and 9 excluded unrelated/invalid messages** (`tools/verify_log_filter.py`). These fixtures use temporary roots and do not apply/start a core or inject syslog messages.
- Three normalized candidate files were compared against the installed 0.1.1-38 originals; reversing only this fix reproduced all three original SHA-256 hashes. Originals were backed up under `/tmp/fii-logfix-20261020-puqqfdsu/backup`, then the files were atomically replaced and rpcd reloaded. The real RPC returned **443 records / 441 core records / 90,478 bytes**, without truncation. Mihomo PID stayed **17317** and the state, active snapshot, runtime YAML, core binary, init script and wrapper hashes were unchanged across deployment.
- A fresh headless Chrome login using the router's installed assets displayed **433 records / 431 core records / 2 application records** in a later snapshot. Core/application filtering, literal search/clear, pause, transfer cleanup and a 390px viewport passed. No page errors or disallowed RPCs occurred. It used no candidate asset routes and performed no service start/stop/apply. Counts naturally vary as the ring buffer advances. Evidence contains counts/hashes, not raw traffic logs or credentials: `build/log-fix/live-ui-result.json`.
- Both application-only **0.1.1-39** IPKs passed package inspection. The physical router received the minimal three-file hotfix; its package database still reports **0.1.1-38**. A complete 0.1.1-39 package upgrade was not performed on the running router.

The hotfix does not require a Mihomo restart. Existing browser tabs should reload their cached LuCI view (Ctrl+F5). The backup is in `/tmp` and will disappear on reboot; the corrected program files persist and the 0.1.1-39 packages contain the same fix.

## Global compact UI: 0.1.1-38 (2026-10-20)

All eight pages now share a compact administration layout: desktop body text is 13px, regular controls are about 32px high, cards have 12px padding, form fields have 10px bottom spacing, and the header/navigation no longer reserve a large introductory area. The original palette, status distinctions, labels and keyboard focus indicators are retained. Wide subscription layouts use three columns where the available width permits; port settings use three columns. Override metadata and collapsed summary share a line, with actions aligned to the right on wider screens. GEO resources occupy four columns on wide screens, source addresses expand on demand, rulesets use aligned compact rows, and log rows use less vertical space. Proxy tiles are 56px high; virtual scrolling and keyboard row calculations use a matching 64px pitch.

The isolated browser comparison uses identical data at **1440x900**: nine subscriptions, twelve overrides, eight outputs, six proxy groups and twelve rulesets. Measurements describe the FII root height, not a physical router/theme guarantee:

| Page | Before / after FII height | Reduction |
| --- | ---: | ---: |
| Overview | 847 / 460 px | 46% |
| Subscriptions | 1783 / 729 px | 59% |
| Mihomo | 1416 / 806 px | 43% |
| Overrides | 3262 / 1148 px | 65% |
| Outputs | 1598 / 939 px | 41% |
| Router settings, proxy section | 1004 / 563 px | 44% |
| Control panel | 2842 / 1394 px | 51% |
| Logs | 1080 / 851 px | 21% |

Fully visible subscription entries increased from **2 to 9**, overrides from **2 to 8**, and outputs from **2 to 6** in this fixture. Log row height changed from 39px to 27px. Results and before/after screenshots are under `build/ui-density/`, with exact measurements in `results.json`.

`tools/verify_density_ui.cjs` checks every page at 1440, 1024, 768 and 390px, every router-settings subsection, subscription/output editor overflow, expanded override summaries, dark mode, the 512-node proxy list's last item and Home navigation, and node geometry. It also emulates a mobile touch device: ordinary buttons retain at least 44px hit areas, mobile editor inputs use 16px text, and proxy modal tiles remain 56px high without overflow. No browser errors or mutation requests occurred. The local preview shim now carries both modal style classes and modal-open state so proxy-modal tests exercise the relevant scoped CSS.

All **203 Python tests**, the resource-update browser regression, JavaScript syntax checks and `git diff --check` passed. Both application-only **0.1.1-38 / all** IPKs passed archive/payload/CSS-hash inspection. Previous 0.1.1-37 packages are retained in `build/packages-before-density/`. This turn did not install packages on a router or validate the new layout under a live LuCI theme; all new browser evidence uses the local fixture.

## Control panel and core resource updates: 0.1.1-37 (2026-10-20)

The former proxy-groups tab is now **控制面板**, retaining the `#proxies` deep link and all group controls. Its new external-resource section shows local GEOSITE / GEOIP / ASN / MMDB file presence, size and modification time, applied GEO mode/automatic-update settings, and sanitized configured source URLs. File discovery follows Mihomo's case-insensitive aliases, including `Country.mmdb`, `geoip.db`, and `geoip.metadb`.

The GEO action uses Mihomo's [POST /configs/geo implementation](https://github.com/MetaCubeX/mihomo/blob/Meta/hub/route/configs.go) and its [enabled-database updater](https://github.com/MetaCubeX/mihomo/blob/Meta/component/updater/update_geo.go). It updates the databases enabled by the core, selecting GEOIP or MMDB according to geodata mode. It does not promise to download all four files or roll back a partially successful multi-file update. Runtime rulesets come from the core's [GET /providers/rules and PUT /providers/rules/{name} API](https://github.com/MetaCubeX/mihomo/blob/Meta/hub/route/provider.go), rather than the editable configuration library. HTTP resources can update, File resources can reload, and Inline resources are read-only. Stopped cores expose only an explicitly labeled applied snapshot and reject every update. A failed runtime-provider read remains an error rather than silently substituting a stale snapshot.

Read and write RPC ACLs are separate. Updates run as polled background jobs, validate the current provider's existence/type, URL-encode its name, and share the service-operation lock with apply/restart/core replacement. They do not restart Mihomo or write the configuration library. GEO inspection never opens arbitrary file paths. Resource source display removes URL credentials, query strings and fragments; controller HTTP failures surface a status code without returning a potentially private remote error body.

All **203 Python tests** passed, including nine new resource tests with a real loopback HTTP controller fixture. Coverage includes authenticated verbs/encoded names, stopped-service rejection, current-runtime membership, file aliases/metadata, credential removal, partial read failures, unsupported resource types, failure propagation, and no restart/config mutation. The nine resource tests were rerun after the final metadata-format adjustment and passed. `node --check` passed for the LuCI view and browser driver, and `git diff --check` passed.

`tools/verify_control_panel_ui.cjs` passed isolated headless-Chrome checks for the renamed tab, retained proxy selection, runtime resource rendering, literal HTML-like names, Inline read-only behavior, background progress, disabled duplicate updates, tab navigation during an update, stopped controls after completion, update/read failure recovery, separate GEO/ruleset errors, 390-pixel overflow, dark layout and cleanup. No browser errors occurred. Evidence: `build/performance/control-panel-ui.json` and `control-panel-{desktop,390,dark}.png`. These are simulated-core browser checks, not router runtime verification.

Both application-only **0.1.1-37 / all** IPKs passed `tools/verify_packages.py`, including archive metadata, payload paths/modes, absence of embedded core binaries, and the packaged CSS content hash. Exact sizes and SHA-256 values are in `dist/manifest.json` and `dist/SHA256SUMS`. The earlier 0.1.1-36 packages were preserved under `build/packages-before-control-panel/`. This turn did **not** install packages on a router, run the OpenWrt ucode VM, or trigger actual GEO/ruleset downloads.

## Dedicated log viewer and native log reads: 0.1.1-29 (2026-10-20)

The [Nikki log page](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/78e1a751d55ed0762811f054ee699049473da7b8/luci-app-nikki/htdocs/luci-static/resources/view/nikki/log.js) was inspected as a reference for separate application/core logs, automatic refresh and scrolling to the bottom. FII now has a dedicated **日志** tab; the overview's existing log button opens that tab. Its source selector, exact level filter, literal case-insensitive search, wrap toggle, pause/resume, follow-latest toggle and export of the filtered raw records work locally. Each record displays its timestamp, textual level, source and message. Logfmt and JSON core levels take precedence over the syslog transport severity. Dynamic messages/timestamps use text nodes so log text is never interpreted as HTML. Scoped level classes avoid theme-wide `.warning`/`.error` formatting. Desktop/mobile and dark layouts use existing FII tokens and no new dependencies.

Automatic reads occur every 3 seconds only while the viewer is active and visible. Requests never overlap; pausing during an automatic request discards its response. Leaving the tab removes its timer/listener and ignores late replies. Identical snapshots retain the DOM/scroll position. Scrolling upward disables following; changed tail snapshots preserve the first visible record and its offset when that record remains in the buffer. Errors retain the previous records and expose a retry message. Filtering issues no RPCs and exported text includes only the current result.

The previous `logread -e fii` query included unrelated cron commands and access records containing FII paths. Both native ucode and Python fallback now match FII/Mihomo service tags, read up to 500 matching syslog records (bounded to 96 KiB), and return the last preparation output separately (bounded to 16 KiB; the viewer shows up to 128 preparation lines). Preparation output has an explicit **最近准备** timestamp label, rather than appearing to be a new event on each refresh. Logread failures are reported rather than silently shown as empty history. The normal RPC uses asynchronous `file.exec` inside rpcd and does not start Python. Logs stay in the existing system ring buffer and temporary preparation file; this change adds no persistent log files or service-start operations.

All **191 Python tests** passed, including tag arguments, complete UTF-8 tail limits, missing preparation output and read-error propagation. The candidate ucode library passed all **12 isolated checks** on the physical router's ucode VM, including log success/error responses, bounded complete lines, preparation tails and zero log worker calls. The fixture creates its own `FII_ROOT`; it does not launch/apply a core. Browser fixture checks passed for source/level/search filters, literal HTML-like log text, filtered export, empty search, pause/resume including an in-flight reply, hidden-page suspension, unchanged scroll, history anchoring across tail rollover, failed refresh/retry, wrapping, desktop/mobile/dark layout and cleanup after tab exit. JavaScript syntax, real warm-cache stylesheet upgrade/retry and both package inspections passed. The stylesheet content hash is `8cdad64bbc11a084`.

Both application-only **0.1.1-29** IPKs were installed on the physical router. All 32 persistent payload hashes/modes and package ownership records match. Fresh pre-install state/active/core/init hashes and the prior boot preference were preserved, with no unexpected core startup observed. Read-only Chrome checks against installed assets passed for the log entry/tab, empty stopped-state history, pause, wrapping, desktop/mobile/dark layouts and tab-exit cleanup. The real log snapshot was empty after excluding unrelated records; populated core log display was tested with browser fixtures, not a running core.

Eight sequential local `ubus call luci.fii logs` measurements were taken before and after installation, discarding the first sample. The seven-sample median fell from **151.96 ms** (0.1.1-28) to **6.99 ms** (0.1.1-29). This measures router-local process/RPC latency with the actual stopped snapshots, not browser round-trip time or running-core throughput. Mihomo remained stopped throughout.

Evidence: `build/logs-tests.log`, `build/performance/logs-{before,after}.txt`, `logs-ucode.txt`, `logs-fixture.json`, `logs-router.json`, `logs-{fixture,router}-{desktop,390,dark}.png`, `logs-cache-console.txt`, `ui-cache.json`, `package-install-29.txt` and `package-install-29-final-guard.txt`. Local pre-install backup: `build/router-backups/before-install-0.1.1-29.tar.gz` (SHA-256 `768b87959599d37b6103389fc5d1a8c1955f6ba99cea501584b56f65344ba7fa`).

## Manual selection and automatic-group pinning: 0.1.1-28 (2026-10-20)

FII previously allowed selection only in Selector groups. The Mihomo [controller API](https://wiki.metacubex.one/api/#proxiesproxies_name) and Meta branch source (commit `88dcbf7f1614a67c3b36b848ee3592dfa92ada36`, `hub/route/proxies.go`, `adapter/outboundgroup/{urltest,fallback}.go`) confirm that URLTest and Fallback implement selection as a `fixed` preference, and `DELETE /proxies/{name}` clears it. URLTest may use another live node while retaining a dead fixed preference; Fallback can clear its fixed preference when the chosen node fails. FII exposes those core semantics without imposing a permanent lock or rewriting the configuration library.

Live group metadata now includes sanitized `fixed` alongside `now`. Stopped snapshots return an unknown fixed state and remain read-only. Selector selection retains its existing PUT behavior; URLTest/Fallback PUT pins a valid group member, including the automatically selected current node. An explicit `select` RPC payload action `unpin` issues DELETE without a body and is restricted to those two automatic types. Unknown groups, invalid members/actions, accidental node names on unpin requests, unsupported group types and stopped operations fail before a controller write. The existing write ACL applies; no arbitrary controller path or HTTP verb is exposed through the browser. Fallback selection has an 8-second controller timeout because Mihomo may synchronously health-check a dead candidate for up to 5 seconds.

Desktop and mobile tiles expose pinning, mark the pinned node independently of the current one, show a compact group summary when those names differ, and provide **取消钉住** while pinned. Unpin failures retain the displayed state and can be retried. Successful operations refresh authoritative core metadata and preserve the virtual-list scroll position/focus. Browsing, searching and refreshing never initiate group delay tests or clear fixed choices. LoadBalance and Relay remain read-only. Persistence continues to belong to Mihomo's `profile.store-selected` behavior, not FII's state/active configuration files.

All **188 Python tests** passed. New/updated tests cover pinning current/other members, bounded Fallback timeout, exact URL escaping, DELETE without a body, unsupported/invalid actions, stopped pin/unpin rejection and distinct fixed/current metadata. A local HTTP server verified the real controller client's PUT and DELETE verbs; no real core was launched. Browser fixture checks passed for the 512-node list, automatic-current-node pinning, changed fixed/now display, failed unpin retention, retry, stale-control disabling and mobile Fallback pin/unpin. All **10 select RPCs** in that browser run were intercepted fixtures. JavaScript syntax, cache-upgrade regression and both package inspections also passed; the new stylesheet hash is `aefcc990eef2e899`.

Both final application-only IPKs were installed as **0.1.1-28** on the physical router. All 32 persistent payload hashes/modes, installed versions and ownership records match. State/active/core/init hashes and the prior boot preference were preserved; Mihomo stayed stopped with no unexpected startup observed. Installed-assets read-only browser checks passed on wide/narrow desktop and mobile layouts, retaining the stopped preview with no selection or pin controls. No live core selection/pinning/forwarding test was run. Evidence: `build/proxy-pin-tests.log`, `build/performance/proxy-pin-{fixture,router}-console.txt`, `proxies-fixture.json`, `proxies-fixture-pin-mobile.png`, `proxies-router.json`, `ui-cache.json`, `package-install-28.txt` and `package-install-28-final-guard.txt`. Local pre-install backup: `build/router-backups/before-install-0.1.1-28.tar.gz` (SHA-256 `2e15cea4fd31790b08e9aa733a731316aebfe56cfd05967f94b3e79a32cc21fa`).

## Stylesheet cache mismatch after upgrading: 0.1.1-27 (2026-10-20)

The user's desktop screenshot showed the new proxy-page DOM with unstyled group controls and overlapping virtual nodes, consistent with old CSS missing the new layout rules. Read-only HTTP checks on the physical router returned the correct installed CSS and JavaScript with status 200. The stylesheet URL was unversioned, while the reproducible package timestamp produced an unchanged Last-Modified value. Earlier browser checks used fresh contexts and request routing, which does not exercise an existing browser HTTP cache; they missed this upgrade scenario. The user's actual browser cache was not directly inspected.

The stylesheet URL now includes the first 16 hex digits of its normalized SHA-256 content hash. The IPK builder synchronizes that marker in the view source automatically, including same-version CSS rebuilds; package inspection independently compares the marker with the CSS actually inside the archive. The view load waits for the stylesheet before rendering. An existing old link remains until its replacement loads successfully and is then removed. Failed loads produce a named error, remove the failed link, clear the pending attempt and allow retry. Matching already-loaded CSS is reused without a new request.

`tools/verify_ui_cache.cjs` passed against a local HTTP fixture using a real Chrome cache with no request routing: the old unversioned CSS was fetched once and stayed cached across navigation, the new content-version URL fetched the correct CSS once, rendering remained blocked while that response was held, and unchanged new CSS stayed cached on subsequent navigation. A 404 blocked the broken view with an error, and retry after recovery succeeded. The fixture reproduces the upgrade cache condition; it is separate from physical-router verification.

Both application-only packages were installed as **0.1.1-27**. All **32 persistent payload hashes/modes**, package versions and ownership records matched. The installation-time state/active/core/init hashes and boot preference stayed unchanged, and Mihomo remained stopped without any unexpected startup. The user-owned state/active snapshot had advanced since the earlier release; the installer protected the current values rather than restoring old ones. Read-only installed-assets Chrome checks confirmed `/luci-static/resources/fii/style.css?v=4038ae92e3290010`, one active stylesheet link, working desktop/mobile proxy layouts and no page errors. An additional **805 px** viewport check matching the screenshot width confirmed grid layout and no horizontal overflow, alongside 1440 px and 390 px checks. No live service/apply/select request was sent.

Evidence: `build/performance/ui-assets-before.txt`, `ui-cache.json`, `ui-cache-candidate-console.txt`, `ui-cache-router-console.txt`, `proxies-router.json`, `proxies-router-{desktop,805,mobile,mobile-nodes,dark}.png`, `package-install-27.txt` and `package-install-27-final-guard.txt`. The local pre-install backup is `build/router-backups/before-install-0.1.1-27.tar.gz` (SHA-256 `d2b9d702f8483e92c4894f55ef18b4f9116e38ee5a01d993ddba1278c8bca3f1`).

## Dedicated proxy-group tab and compact presentation: 0.1.1-26 (2026-10-20)

The proxy panel now lives in an independent **代理组** tab at `#proxies`. The overview contains service status and core management, makes no group request, and retains the removal of **三步开始**. Status-driven group refresh hooks, media listeners, pending frames and ResizeObservers are disposed when leaving the proxy page; direct navigation/reload to the new hash works.

The presentation was redesigned using [zashboard's proxy components and screenshots](https://github.com/Zephyruso/zashboard/tree/6225aac84439425ae4f4493437b110a314753436/src/components/proxies) as a visual/interaction reference. The LuCI implementation uses compact two-column group panels, policy/member metadata, current selection and compact latency previews. Node tiles place the name above protocol/delay information and use a filled selection state. Desktop expansion stays within its group column; groups remain assigned to the same columns while opening panels. Mobile uses two columns of compact group entries and opens nodes in a LuCI modal, with an explicit close action, Escape dismissal and focus return. The modal uses separate class arguments as required by the actual device's LuCI API.

One local search covers group and node names; node sorting supports configuration order, name and recorded latency. Large groups use a fixed-row virtual grid with a bounded scroll viewport, rendering the visible rows plus overscan rather than appending every node. Arrow keys and Home/End can reach members outside the rendered range. Opening the mobile modal locates the current node. Selections preserve the actual scroll offset before rebuilding, and detached old viewport events cannot overwrite that offset. Resize notifications skip identical ranges to preserve keyboard focus. No delay test or extra per-node request is issued by browsing, searching, sorting or scrolling. The existing stopped snapshot/dynamic-provider/read-only behavior remains intact.

`tools/verify_overview_ui.cjs --fixture` passed with **512 simulated nodes**. Checks cover local search, bounded rendering (fewer than 60 mounted tiles in the desktop fixture), reaching the last node, name/latency controls, keyboard End, desktop and mobile selection failures/successes, current-node positioning, preserved scroll/focus, read-only automatic groups, failed-refresh retention and disabled stale controls, modal close/Escape, light/dark layouts, and direct `#proxies` reload. All **four select calls** were intercepted by the browser and never sent to the router. Candidate assets were also checked against the real stopped snapshot before installation. JavaScript syntax, diff whitespace and both package inspections passed; the Python backend was unchanged in this revision.

Both final application-only packages were installed as **0.1.1-26** on the physical router. The last modal-heading style adjustment was included by reinstalling the verified final packages. The final installed-assets browser run confirmed the real **18 groups**, no group panel/request in the overview, the independent tab, desktop expansion, mobile modal/read-only members, Escape/close behavior and hash reload. The 390 px mobile panel and modal have no horizontal overflow, and the node targets exceed 44 px in both dimensions. No page errors or unauthorized RPC attempts occurred. Live selection/forwarding was not tested: Mihomo stayed stopped throughout. All **32 persistent package file hashes/modes**, package versions and ownership records matched, while state/active/core/init hashes and boot preferences stayed unchanged. No unexpected core start was observed during installation.

Evidence: `build/performance/proxies-{fixture,candidate,router}.json`, `proxies-{fixture,candidate,router}-{desktop,mobile,mobile-nodes,dark}.png`, `overview-compact-before.txt`, `package-install-26.txt` and `package-install-26-final-guard.txt`. The original pre-upgrade backup is retained at `build/router-backups/before-upgrade-25-to-26.tar.gz` (SHA-256 `d00c489104cd47a64b1c107e4c305b7eb1035d44021f5035eefa060cc4a1854d`), and the final reinstall backup is `build/router-backups/before-install-0.1.1-26.tar.gz` (SHA-256 `7681b0e1f9ad22b9ffc7da115b54f1fdc665aabcf785d260dab834201783a738`).

## Overview proxy-group cards: 0.1.1-25 (2026-10-20)

Removed the **三步开始** block. The overview now places the proxy-group panel after service status, followed by core management. Searchable group cards show policy type, current node and membership; native disclosure controls expand into node cards with protocol, selection state and the latest known delay/availability. Manual selectors support direct node selection with pending, failure and keyboard-focus feedback. Automatic/relay/load-balancing groups describe their policy instead of offering misleading manual controls. Desktop uses two group columns and three node columns; the mobile node grid uses two columns and supports long names and dark mode.

Closed groups create no member DOM. Expanded groups show 24 nodes per batch and filter locally; typing does not issue a new RPC. Refresh retrieves group/node information in one response and does not trigger a delay test. Only compact type/availability/latest-delay metadata is returned from the controller; full history and node credentials are omitted. A refresh failure retains the last visible data with an error and disables selection until a successful refresh. Group state and searches survive successful refreshes.

When the core is stopped, the panel reads the existing active YAML without rendering scripts, contacting the controller or starting a service. It labels the result **配置预览**, omits current selection and recorded latency, and makes all members read-only. Dynamic provider/include-all members are labelled **动态成员** rather than showing a misleading zero count. Selecting a proxy while stopped is rejected before any controller write. A running controller failure is reported instead of silently presenting a snapshot as live data.

All **184 Python tests** passed, including six new compact-metadata, stopped-preview, selection-validation and error-boundary tests. JavaScript syntax, diff whitespace and both application-only package inspections passed. `tools/verify_overview_ui.cjs --fixture` routes the candidate assets into Chrome and intercepts status/groups/select with browser-only fixtures. It verified batching, local search, manual selection success/failure, automatic-group read-only behavior, dynamic labels, stale-refresh handling, preserved search/selection/focus, literal text rendering, desktop/mobile geometry and dark mode. Both mocked select requests were consumed by the browser; none reached the device.

The physical router was upgraded from 0.1.1-24 to **0.1.1-25** through its specified proxy. The final dynamic-member wording was included by reinstalling the verified 25 packages. All **32 persistent payload hashes/modes** and opkg ownership records match the final packages. Installation preserved the state/active/core/init hashes and boot preference, and monitored opkg execution without observing a Mihomo process. Read-only Chrome checks against the final installed assets displayed the real **18 groups**, including dynamic-member labels, with no page errors, manual-selection controls or fabricated current nodes. Desktop and 390 px mobile captures showed no panel overflow. Neither live selection nor forwarding was tested because the physical core remained stopped throughout.

Evidence: `build/overview-tests.log`, `build/performance/overview-fixture.json`, `overview-router.json`, `overview-{fixture,router}-{desktop,mobile,dark}.png`, `package-install-25.txt` and `package-install-25-final-guard.txt` (all but the test log under `build/performance/`). The original pre-upgrade backup is retained locally as `build/router-backups/before-upgrade-24-to-25.tar.gz` (SHA-256 `29a0914ce80f5049dcb51123c7aa4e0d1f0c354e720bc508ca1117e5f5fb619d`); the final reinstall backup is `build/router-backups/before-install-0.1.1-25.tar.gz` (SHA-256 `2f953725674e213cbdaeeac0941155f43de66a139eb5873fd9e64fd02ea4bf17`).

## Update runtime configuration without starting a stopped core: 0.1.1-24 (2026-10-20)

Previously the apply operation always called `restart_and_wait()`, so clicking the output action started Mihomo even when the service was stopped. It now reads FII's service instances from procd before replacing the active snapshot. A stopped service updates the validated snapshot/revision and boot preference without invoking start/restart. A running or respawning managed instance restarts with the new snapshot and retains the existing rollback behavior. Missing/invalid service-state responses fail before snapshot replacement instead of guessing. Configuration validation remains required in both paths.

The output action is now **更新运行配置**, also available for the selected output in the overview. Router settings use **保存并更新配置**. The separate **启动服务** control remains the explicit way to start a stopped core. Status text describes the generated snapshot and directs stale-library users to update it; it no longer implies that applying a snapshot necessarily starts traffic interception.

All **178 Python tests** passed. Added checks cover first/stopped snapshot updates without any restart, revision and previous-snapshot recording, uncertain-state retention, valid/stopped/respawning procd responses and malformed response rejection. Existing running-update success/rollback and validation-failure checks also pass. JavaScript syntax and both package inspections passed. VM forwarding/UI drivers now start their fixtures explicitly; they were not executed on the physical router.

An isolated check on the physical router rendered a temporary copy of its real configuration and read the actual stopped procd service state. Both first-snapshot and existing-snapshot updates returned **运行配置已更新，内核保持停止** and advanced the private snapshot revision. Start/restart calls were forbidden; core/capability validation and boot-preference mutations were stubbed. No live snapshot or core process was changed by this check. This exercises real Linux rendering/state detection and the stopped transition, not real core execution or live running-service rollback.

Both packages were formally upgraded from 0.1.1-23 to **0.1.1-24** using their verified IPKs. All 32 persistent payload hashes/modes matched; boot preference, state/active/core/init hashes and the stopped state were preserved, with no unexpected core start detected during installation. Real read-only browser checks verified the new output/overview/settings controls, absence of old apply labels and unchanged preview policy behavior, with no page errors. The editable migration override was checked against its current user-owned content rather than assuming its initial fields. No live apply or startup test was run. Evidence: `build/apply-preserve-state-tests.log`, `build/performance/apply-stopped-isolated.txt`, `package-install-24.txt`, `apply-stop-browser.json`, and `package-install-24-final-guard.txt` (the last four under `build/performance/`).

## Separate Mihomo policy from router integration: 0.1.1-23 (2026-10-20)

Mihomo DNS mode/resolvers/filter/policy/IPv6/Fake-IP ranges and TUN stack/MTU are no longer settings-page options or router-settings defaults. Mihomo defaults live in `mihomo.py` and are part of generated output; YAML/JS overrides own the explicit policy. The router page contains interfaces, traffic/DNS interception, ACLs, firewall bypasses, interface readiness waits and service/integration endpoints. Its DNS section is now **DNS 接管**, with a link to the Mihomo overrides. Runtime adaptation preserves DNS mode, IPv6 and Fake-IP ranges; DNS listener binding, controller access, TUN interface/routing management and marks remain explicit integration fields.

Old settings are decoded into one visible first global YAML override, **Mihomo 基础配置（旧版迁移）**, and removed from the normalized router-settings object. Existing overrides run afterward and retain priority. Reads do not rewrite the saved library; the normalized representation is persisted on a later explicit save. Old FII backup policy migrates before target-router settings are preserved, so the import neither loses old Mihomo values nor imports another router's network settings/credentials. Old UI patches attempting to submit Mihomo options as router settings receive a named error. Metadata schema 2 invalidates old cached mixed settings; full virtual migration-editor contents fall back to the normalized Python read until persisted.

Router nftables generation now reads DNS mode and both Fake-IP networks from the actual generated/active YAML. Networks are parsed and family-checked before entering command arguments or nft text. Fake-IP ping follows the output's DNS mode; the IPv6 dummy route uses its actual network and removes superseded routes only from FII's verified dedicated interface. Switching away from IPv6 Fake-IP removes only FII's own dummy interface. Disabling Mihomo DNS while router ACLs still require DNS interception fails with an actionable conflict instead of silently enabling DNS or creating a broken interception path.

All **174 Python tests** passed. New coverage checks read-only/idempotent migration and persistence, old-backup policy/router isolation, router edits leaving Mihomo policy intact, rejected legacy patches, dynamic DNS-mode/network inputs and network injection bounds, disabled-DNS conflicts, old metadata invalidation, custom IPv6 routes and mode-change cleanup. Existing runtime/JS/backup tests continue to pass under the separated model. The actual router ucode interpreter passed **11 fixture groups**, including old-schema rejection and virtual-editor fallback. Legacy full-workflow VM drivers were updated for the new UI/fixture boundary, but were not executed because they start the core.

An isolated candidate package on the physical router rendered its existing revision-30 library. It produced the order **Mihomo 基础配置（旧版迁移） → common → dns → proxies → rules → 规则覆写**, preserving every supplied DNS field other than the integration listener, the Mihomo IPv6 policy and TUN stack/MTU. Routing text consumed the output's DNS networks. All live configuration/core/init hashes stayed unchanged. Nine management files were then backed up and updated directly with one rpcd SIGHUP reload; no package hooks, apply, save, refresh or service-start operation was invoked. opkg still reports 0.1.1-19. The active snapshot remains untouched and Mihomo remains stopped.

Real read-only Chrome/LuCI verification through the requested SOCKS proxy checks normal/runtime DNS and common fields, opens the virtual migrated override for editing and cancels, confirms router policy controls are absent while DNS interception controls remain, and follows the Mihomo navigation link. Evidence: `build/separation-tests.log`, `build/performance/separation-candidate.txt`, `separation-deployment.txt`, `separation-browser.json`, and `separation-final-guard.txt` (the last four under `build/performance/`). Both application-only 0.1.1-23 packages passed structure inspection. No live forwarding or service activation is claimed for this change.

### Formal package installation on the physical router

At the user's subsequent request, both exact 0.1.1-23 IPKs were uploaded through the specified SOCKS/SSH path, their SHA-256 hashes were verified, and `opkg install` upgraded `fii` and `luci-app-fii` from 0.1.1-19 to 0.1.1-23. All dependencies were already installed. The final status of both packages is **install user installed**, with complete payload ownership recorded in opkg's file lists. All **32 persistent payload files** matched the packages' hashes and modes; the one-time initialization script was successfully consumed.

Installation held the scheduler, operation and state locks, backed up the current files/configuration/package records, and monitored for an unexpected Mihomo process throughout opkg execution. None was detected. The pre-existing enabled boot preference was preserved; the current stopped state, saved revision 30, active revision 29 and state/active/core/init hashes were unchanged. The scheduled refresh entry remains present exactly once. No configuration apply, service start or core update was invoked. Package initialization reloaded firewall integration and rpcd as declared by the package hooks.

opkg emitted a cleanup warning because the obsolete public Python RPC file had already been removed by the earlier management update; the service-delete command likewise found no running FII instance. Installation exited successfully. The final package statuses, complete new file lists, absence of the obsolete RPC plugin, payload checks and healthy native API were verified explicitly after these notices. The firewall reload also emitted notices about existing third-party fw4 sections; no unrelated firewall configuration was edited by this task.

The complete read-only Chrome/LuCI separation/preview/editor checks passed again after installation, with no page errors. The backup was also downloaded to `build/router-backups/before-install-0.1.1-23.tar.gz` so it survives a router reboot; its SHA-256 is `5f6e1431c875f2d23c9a6e8a59925a7411c059d7ac097e6259e264b13918a387`. Evidence: `build/performance/package-install-before.txt`, `package-install-23.txt`, `package-install-after.txt`, `package-install-browser.json`, and `package-install-final-guard.txt` (all under `build/performance/`). These are real-device installation and stopped-management checks, not live forwarding validation.

## Preserve common/DNS overrides at runtime: 0.1.1-22 (2026-10-20)

The physical router's saved library and active snapshot were compared read-only at revision 29. The `common`, `dns`, `proxies`, `rules` and global RULES overrides all executed. Running the four JS scripts individually and through the optimized batch produced identical results, excluding batching as the cause. The active snapshot already contained common's Geo updates/URLs, unified delay, process lookup, TCP concurrency and profile settings. Its controller address and secret were intentionally replaced by FII's integration settings. However, runtime integration also replaced the DNS override's `default-nameserver`, `nameserver`, `proxy-server-nameserver` and `fake-ip-filter` with management-page values. That was a real loss of override content after successful script execution.

Runtime integration now uses the output's explicit resolver/filter values and fills only missing fields from router settings. A missing node resolver follows the effective upstream resolver unless an explicit node resolver is configured. Existing domain policies remain intact, with missing `.lan`/`.local` defaults still added. TUN stack, MTU and other non-routing options are preserved, and profile options are filled individually instead of replacing the whole object; explicit false values and empty filter arrays are retained. Malformed TUN/profile objects fail with a named error rather than silently disappearing.

Integration-owned fields still follow FII settings: listener/controller endpoints and secret, routing marks, DNS enable/listen/mode/IPv6/Fake-IP ranges, and TUN enable/device/automatic routing/DNS hijack/IPv6 interface addresses. Runtime preview and apply responses now list supplied fields changed by integration, exposing field names only. Additions of missing defaults are not reported as replacements. The settings UI describes the precedence and the runtime-preview UI displays the warnings.

All **167 Python tests** passed, including four new end-to-end JS/runtime-preview, missing-resolver default, integration-warning secrecy and malformed-object checks. On the physical router, a temporary candidate module rendered the existing output and asserted that common fields, nine supplied DNS options, domain policies and non-routing TUN options match the original overrides. The four previously overwritten DNS fields changed from mismatch to exact match. After replacing only `runtime.py`, `cli.py` and the LuCI view, a fresh backend process passed runtime preview. Real Chrome/LuCI through the requested SOCKS proxy then compared normal and runtime preview: all nine tested DNS fields and nine common fields matched, the integration warning appeared, and no page errors occurred. Browser requests were restricted to a read/preview/temporary-transfer allowlist.

No configuration was applied and no live state or active snapshot was rewritten by this fix or its verification; all protected hashes matched at deployment completion. The final audit observed a separate saved-library revision advance from 29 to 30 after deployment. It preserved that update, confirmed a stable read of revision 30, and verified the active snapshot remained revision 29 with its original hash. The core binary and init script were also unchanged and Mihomo remained stopped. rpcd did not need a reload/restart. Deployed code was backed up under `/tmp/fii-override-fix-22/` until reboot; opkg's database remains 0.1.1-19 because package hooks were not invoked. These checks verify generated runtime configuration, not resolver reachability or live forwarding. The existing active snapshot retains its previous DNS values until the user explicitly applies a newly generated configuration.

Evidence: `build/performance/override-audit.txt`, `common-dns-before.json`, `override-fix-deployment.json`, `override-runtime-browser.json`, `override-fix-final-guard.txt`, and `build/override-tests.log` (all performance summaries except the test log are under `build/performance/`). Both application-only 0.1.1-22 packages passed structure/dependency inspection.

## Native management RPC on the physical router: 0.1.1-21 (2026-10-20)

The public `luci.fii` object now runs inside rpcd's persistent ucode interpreter. Normal metadata/editor reads, stopped-service status, cached core information, network choices, job reads and outgoing transfer reads/releases use native code. Python remains the validated configuration/rendering backend and handles mutations, heavy jobs and cold/invalid-cache fallback through the asynchronous private `luci.fii.worker` object. That worker has no browser ACL grant. Successful Python saves also publish the validated compact snapshot, so the next ordinary read does not require another Python validation process. The subscription → override → output workflow is unchanged.

The following timings are **actual ubus calls on the physical router**, including the local ubus CLI process but excluding HTTP, the SOCKS proxy and browser rendering. Each row covers four calls; caches were warm, except the first metadata call also loaded the native document cache. A core-information cache miss still performs one Python lookup (167 ms in the private-rpcd test), and a missing/invalid metadata cache still delegates to the Python validator.

| Query | Previous 0.1.1-20 | Native 0.1.1-21 |
| --- | ---: | ---: |
| Compact library metadata | 99.28–100.79 ms | 2.88–4.34 ms |
| Service status, core stopped | 171.62–175.18 ms | 1.75–1.81 ms |
| Cached core information | 165.24–166.71 ms | 1.55–1.67 ms |
| Job result | 96.28–97.91 ms | 1.63–2.09 ms |
| Network choices | 203.43–203.63 ms | 54.14–56.29 ms |

The final read-only Chrome/LuCI run used the requested SOCKS proxy and the existing real library. These measurements include the full browser/request/download path and are therefore different from the ubus timings above. The immediate baseline was measured again before switching to native RPC; original 0.1.1-19 timings are documented below.

| Browser operation | Immediate 0.1.1-20 baseline | Final 0.1.1-21 |
| --- | ---: | ---: |
| Open output editor | 8 ms | 7 ms |
| Open source metadata editor | 10 ms | 9 ms |
| Open override editor with full content | 503 ms | 177 ms |
| Preview existing output, parsed cache warm | 1,516 / 1,523 ms | 1,348 / 1,403 ms |

Preview still executes subscription parsing, four JS overrides and YAML generation in the existing backend; this change does not make those operations take only a few milliseconds. With native CGI downloads deliberately rejected, all editors and previews still completed via chunked RPC, with five rejected downloads and no unexpected page errors; previews took 2,193 / 2,482 ms. The final normal run also had no page errors.

The installed rpcd is `2025.09.01~bba95191-r3`; its ucode request API has no `request.defer()`. Pending jobs therefore return immediately with a 100 ms browser polling hint on this router. Newer APIs can use bounded timers without blocking rpcd. System queries and Python work use asynchronous ubus requests; transfer locks are nonblocking, with busy-lock fallback. Regular expressions are compiled once, including the bounded cgroup-name pattern whose repeated compilation initially delayed network queries. Large native results use private mode-0600 files under the existing shared transfer quota/expiry/cleanup rules and authenticated CGI/chunk transport.

Validation passed **163 Python tests**, JavaScript syntax, ShellCheck, and both application-only 0.1.1-21 package inspections. The actual router ucode interpreter passed **10 native fixture groups**, including Python/native response equivalence, Unicode transfers/private modes, argument bounds, cached core/readiness checks, native network choices, both job API variants, busy locks, shared quotas, atomic replacement invalidation and cold/corrupt-cache fallback. A separate temporary ubus/rpcd instance verified real asynchronous delivery: a concurrent core-information query completed in **2.78 ms** while another job completed after 251.29 ms. Its scoped session denied direct access to the private worker. The readiness HTTP command was exercised against a temporary loopback fixture server, without starting Mihomo.

The five management files were backed up and updated directly. rpcd was restarted while recovering an initial registration failure; the final deployment successfully used SIGHUP reload. No package hooks were invoked, so opkg still reports 0.1.1-19. The final guard confirmed that deployed management hashes match the source manifest, `state.json`, `active.json`, the core binary and FII init script are byte-identical, the temporary private rpcd was removed, and **Mihomo remained stopped**. No live apply/save/import/refresh/start operation was invoked. These results do not establish live activation or forwarding performance.

Evidence: `build/performance/rpc-before-ucode.txt`, `rpc-after-ucode.txt`, `management-before-ucode.json`, `management-ucode-final.json`, `management-ucode-fallback.json`, `ucode-fixtures.txt`, `private-rpcd.txt`, `ucode-deployment.json`, and `ucode-final-guard.txt`, all under `build/performance/`. Fixture drivers are `tools/verify_rpc_ucode.py` and `tools/verify_rpc_ucode.uc`.

## Physical-router management performance: 0.1.1-20 (2026-10-20)

Measured on the user's real iStoreOS router through the requested SOCKS proxy, using its existing library: 7 sources, 7 overrides, one output, about 1.9 MB of saved JSON, and 509 final nodes. Four global JS overrides execute for this output. Mihomo was stopped before the work and remained stopped throughout. No apply, start, restart, import, subscription refresh or live configuration save was invoked. Browser measurements reject every FII operation except the explicit read/preview/temporary-transfer allowlist in `tools/measure_management_ui.cjs`.

| Actual browser operation | Original management code | Updated management code |
| --- | ---: | ---: |
| Open output editor | 973 ms | 7–8 ms, no RPC |
| Open source metadata editor | 924 ms | 10 ms, no RPC |
| Open first override editor, including full content download | 1,280 ms | 408–514 ms |
| Preview existing output, parsed-node cache warm | 7,875–7,943 ms | 1,462–1,631 ms |
| Preview after explicitly emptying the new parsed-node cache | No parsed-node cache | 3,226 ms |

These browser times include the proxy path and RPC/result transport. The original preview issued 13 serial `transfer_read` calls in addition to job polling. The updated warm preview used two job reads and one native CGI download. The same path's system/board RPC took 73–76 ms, while original FII read calls took roughly 280–420 ms. This does not establish Nikki's internal processing time or equalize different measurement methods.

Lightweight RPCs no longer import the YAML/fetch/runtime stack unconditionally. Compact metadata is cached in `/tmp/fii/state-view.json` against inode, size and nanosecond modification/change times. Changed files, corrupt caches, cache-write failures and externally replaced snapshots are handled without returning a persistent stale view. Source/output metadata editors use the current page snapshot; revision conflicts still block stale saves, while full script/cache editors continue fetching complete content. Settings submit changed fields only. Metadata saves return directly, avoiding a background-process/job round trip, and a Store reuses its read revision only while the exact file signature is unchanged; validated data is not validated a third time.

Parsed subscription nodes use a content SHA-256 cache in `/tmp/fii/node-cache-v1`, capped at 32 entries / 32 MiB. Results are decoded afresh before mutation, and a changed subscription naturally selects a new key. Consecutive JS overrides share one disposable Node process while retaining a fresh VM for each script, per-stage deadlines, per-result limits, execution order and named failures. YAML/RULES boundaries still split JS batches. There is no persistent Python/Node configuration daemon and no cache writes to persistent configuration storage. Jobs accept a bounded wait (maximum 1 second); the browser uses 600 ms reads instead of sleeping for up to 750 ms before finding an already completed result.

Large responses advertise a private `*.out` file for authenticated LuCI CGI download. Read permissions cover only `/tmp/fii/transfers/*.out`; existing incoming `*.in` writes remain separate. Path/ID/size checks and release/expiry cleanup remain in place. Explicitly rejecting native downloads exercised the original adaptive RPC fallback: five CGI downloads were rejected; editors and both previews still completed, with no page errors. Warm preview through that deliberately slower fallback took 3,555 / 3,668 ms. These checks do not exercise a live configuration import.

An isolated copy of the real state on the same router compared the original and candidate renderer. Plain backend preview fell from 3,261–3,306 ms to 740–748 ms with warm node caches. All results contained exactly 189,783 UTF-8 bytes and the same SHA-256 (`887123e6c0bd09ec08b1ae3c44e20071d008ef2dd7f04695c65c755ee28d8a02`). Compact dispatch fell from about 119–122 ms to 0.7–0.8 ms after cache creation; these dispatch numbers exclude interpreter startup and HTTP/proxy costs. Save timings were taken only against that isolated state copy, never the live library.

The management Python files, JS runner, LuCI view and narrow download ACL were backed up and replaced directly, followed only by `rpcd reload`. Package install/uninstall hooks were not run, so the physical router's opkg database still reports 0.1.1-19. The live `state.json`, `active.json`, core binary and FII init script stayed byte-identical, and `pidof mihomo` was empty before and after. Original code is backed up under `/tmp/fii-performance/original` until reboot. This work makes no new claim about start/apply/restart latency, forwarding or boot behavior.

Local validation passed **161 tests**, JavaScript syntax checks, ShellCheck for the existing integration scripts, and both application-only 0.1.1-20 package inspections. Added coverage exercises metadata/node cache invalidation, corruption, optional-cache failure, bounded entry count, snapshot replacement races, optimistic save conflicts, bounded job waits, and JS batch isolation/order/failure attribution.

Evidence: `build/router-operations-before.json`, `build/router-operations-final.json`, `build/performance/management-final-native.json`, `build/performance/management-final-cold-and-warm.json`, `build/performance/management-fallback.json`, `build/router-isolated-performance-final.txt`, `build/router-management-hotpatch.json`, and `build/router-final-guard.txt`. Credentials and subscription contents are not included in these summaries. The final code hashes were checked against the deployed files after the last browser run; configuration/core hashes still matched and Mihomo remained stopped.

## Router-level validation (iStoreOS 24.10.8 VM, 2026-10-19–20)

### Preserve REALITY short IDs: 0.1.1-19

The supplied error named `proxy 143: invalid REALITY short ID`, although the same backup worked in the user's desktop/mobile clients. The private backup was not available for direct inspection. A generated 144-proxy fixture reproduced that exact index/error on the installed Linux v1.19.32 core: the old parser converted bare `00112233` to the YAML 1.1 octal integer `38043`, and `0000000000000001` to integer `1`. The generated odd-length IDs then failed core validation. Other numeric-looking IDs could lose leading zeroes or be emitted bare and interpreted differently by the next YAML implementation.

The safe loader now preserves the source spelling of scalar `short-id` values as text, without mutating global resolvers or shared alias nodes. The dumper explicitly quotes string IDs, including those returned by JS, across render and runtime overlay. Other numeric/boolean fields remain typed; missing/null values and duplicate/merged-key precedence remain distinct. No ID is guessed, padded, truncated, removed or replaced, and genuine invalid values still fail. Core failures now identify the final proxy's name and one-based position instead of only a zero-based core index. Candidate files are cleaned and the active snapshot is unchanged on failure.

The local suite passed **151 tests**, including six REALITY cases covering lexical preservation, repeated overlays, aliases/merges/duplicates/null, original saved-cache and YAML/JS handling, share links, node-specific errors and failure retention. `tools/verify_reality.py` generated 144-node configurations for six ID shapes; all six passed the unchanged official **Linux and Windows v1.19.32** cores after the fix, with exact string equality. Before/after evidence: `build/vm/reality-before.json`, `build/vm/reality-after.txt`, `build/reality-windows.json`. These tests verify generation/core configuration acceptance, not an authenticated connection to the user's real REALITY server.

All **9 real LuCI JS workflow checks** passed on the installed 0.1.1-19 packages. The Flutter-format backup fixture now includes a bare `00112233` REALITY ID; its JS override asserts the exact string before execution, preview emits the quoted ID, and actual Mihomo activation succeeds. A later override deliberately changes it to `not-hex`: application reports `Test-REALITY` / output item 2 and leaves the previously running revision intact. Existing JS syntax/error/timeout protections also pass. No unexpected browser errors occurred. Original Home data and stopped/boot-disabled state were restored; installed renderer/runtime hashes match the sources. Evidence: `build/vm/js-ui-results.json`.

### Native backup upload: 0.1.1-18

The previous import page serialized the complete backup inside an RPC envelope and sent ordered 16 KiB base64 chunks, starting a Python backend for every chunk. A generated 1,950,251-byte JSON backup required 120 append RPCs even on an unrestricted VM. The upload/download paths also shared their remembered chunk size; a restricted reply could reduce later uploads to 1 KiB, multiplying request/worker overhead.

Backup checking now reserves a random private incoming file under the existing transfer quota, sends the original File once through LuCI's authenticated `cgi-upload` endpoint, then submits a small preview request referring to that reservation. The existing staged-check token still confirms import without sending the file again. The CGI endpoint follows the installed LuCI upload form fields; its upstream implementation authenticates the session, canonicalizes the destination and checks file-write ACLs ([OpenWrt cgi-io](https://github.com/openwrt/cgi-io/blob/master/main.c)). FII grants upload writes only to incoming `*.in` files, never checked results, live state or arbitrary paths. Declared size and upload mode are checked before reading/parsing, files remain private, and success/cancel/error cleanup releases the reservation. Lost HTTP acknowledgements query the reserved file's completion before retrying. Unsupported/rejected native uploads fall back to the existing adaptive RPC transport; upload and download sizes are now remembered independently. `cgi-io` is an explicit LuCI package dependency.

`tools/measure_upload_ui.cjs` measured the same generated file on installed packages through real Chrome/LuCI. It previews and cancels, without importing data:

| Measurement | 0.1.1-17 | 0.1.1-18 |
| --- | ---: | ---: |
| Upload completion | 8,428 ms | 119 ms |
| Click check through confirmation summary | 8,955 ms | 868 ms |
| Append RPCs | 120 | 0 |
| Native file POSTs | 0 | 1 |
| Total RPC POSTs during check | 125 | 4 |

These are QEMU measurements for a generated backup, not the user's physical router or private backup. The final package measurement on 2026-10-20 is in `build/vm/upload-performance-final.json`; initial before/after measurements are in `build/vm/upload-performance-before.json` and `build/vm/upload-performance-after.json`.

The local suite passed **145 tests**, including eight native-upload cases for Unicode/BOM, exact/partial/oversized/missing files, raw-file versus RPC-envelope separation, staged-check equivalence, invalid JSON/backup cleanup, quota/expiry/size limits, the scoped ACL and a concurrent CGI replacement between stat/open. The actual file read is bounded even if that replacement changes its size after the initial check. Both final package structures passed inspection.

The installed 0.1.1-18 workflow passed **14 real LuCI backup checks** and **10 override checks**. Native upload/checking used one file POST and zero append RPCs; token confirmation remained small (802 ms through page reload, 1,457 POST bytes total). An unauthenticated CGI upload was rejected, cancellation released its reservation without changing saved revision, and a deliberately lost native-upload acknowledgement recovered through completion status without retransmitting the file. With native upload deliberately rejected, a 1.86 MiB backup still completed through adaptive RPC under 4 KiB POST limits and a lost chunk acknowledgement. Export also recovered from 4 KiB reply limits. The harness recorded one rejected native upload, one lost native acknowledgement, three rejected RPC POSTs, three rejected RPC replies and one lost chunk acknowledgement, with no unexpected browser errors. Ordering, editor synchronization, automatic globals and preview behavior remained intact. Evidence: `build/vm/backup-ui-results.json` and `build/vm/override-ui-results.json`.

### Override ordering, editor synchronization and preview feedback: 0.1.1-17

Flutter's backup exporter reads templates in database insertion order, while its DAO displays templates by `sortOrder`, then numeric ID. Its renderer selects profile overrides from that sorted list. FII previously used the exported array and raw profile association order. The importer now reconstructs both library order and selected execution order when sorting metadata is present; legacy backups without it retain their explicit array/association order, and FII backups keep their saved order. Remote URLs and refresh diagnostics are preserved, with Flutter refresh timestamps converted from milliseconds to seconds. Older FII imports discarded the sorting metadata, so already imported libraries require reimporting the original Flutter backup once or manual sorting to recover the original order.

The override library now uses a numbered single-column list with up/down and move-to-position controls. Revision-checked reorder patches require an exact permutation of existing IDs and preserve bodies/settings/profile associations. Global overrides follow library order; each output retains an independently editable selected order. Output cards and editors show the deduplicated enabled execution sequence; automatic global checkboxes are checked/read-only, disabled globals are explicitly unchecked, and selected globals are not saved again as manual associations. Preview returns the backend's actual applied-override list for comparison with generated YAML.

Local overrides show their local source and editing action; remote overrides show the last refresh time and a clearly named remote-refresh action. Opening an existing source/output/override editor reads its latest item and matching compact state/revision in one snapshot. A background update before opening no longer triggers a stale-list error. Updates after editing starts still block saves rather than overwriting changed data. Source cache contents remain lazy-loaded, and a conflicting change during cache loading is reported inline.

Preview opens a progress modal immediately, reports subscription parsing / override execution / result download, and stays closed if dismissed during the job. YAML parsing/emission use PyYAML's safe libyaml implementation when installed, with the existing alias-count, unsafe-tag, cycle and expanded-size checks retained and a pure-Python fallback. A generated 1,950,003-byte source with 500 nodes and six executed JS overrides took **1,813 / 1,763 ms** before and **565 / 559 ms** after in two direct VM renderer runs. These timings exclude LuCI transfer and describe the generated fixture, not the user's private configuration. Evidence: `build/vm/preview-performance-before.json` and `build/vm/preview-performance-after.txt`.

The local suite passed **137 tests**, including seven new ordering/synchronization/progress/parser cases. Package-structure checks passed for both 0.1.1-17 IPKs.

The final installed packages passed **10 real LuCI override-workflow checks** and all **8 existing JS checks**. Coverage includes shuffled Flutter export arrays, persisted move-to-position/up/down, unchanged script bodies and profile associations, 1440/390 px single-column layouts and readable titles, local/remote action differences, a background edit before opening, a real save conflict during editing, failed remote-refresh content retention, checked automatic globals, disabled globals, per-output ordering, actual rendered order and closing an in-flight preview. The preview modal appeared in **37 ms** while its request was deliberately delayed by 1,200 ms; total completion was 1,876 ms in that delayed test. Both final browser runs reported no unexpected page/request errors. Chrome was configured to connect directly to the local VM after the host's default browser network path intermittently refused LuCI static-resource connections during initial test setup. Evidence: `build/vm/override-ui-results.json`, `build/vm/js-ui-results.json` and `build/vm/override-*.png`.

The existing 1.86 MiB UI fixture also completed all six editing measurements: initial load 1,295 ms, output rename 865 ms, settings save 553 ms, reload 364 ms, source rename without cache loading 842 ms and saving unchanged loaded cache 637 ms. The last two saves posted only 1,049 / 959 bytes; full cached YAML and six scripts remained exact. Evidence: `build/vm/ui-performance-17.json`. Each test restored the original Home library, and the original stopped/boot-disabled state was preserved.

### Reuse checked backups: 0.1.1-16

The old confirmation handler posted the entire backup again after import preview. The frontend now asks preview to retain the validated, normalized configuration under a random 128-bit token, with a 15-minute expiry and the checked configuration revision. Confirmation sends only the token/revision and commits that exact configuration, without repeating YAML/JS validation. Legacy raw import and non-staged preview calls remain supported.

Checked contents use private atomic files under the existing bounded transfer directory and share its 64 MiB/64-item quota. Cancel/release removes the staged file; expired files are removed when accessed or on subsequent quota cleanup. Generic transfer reads/appends cannot read or replace checked contents. The saved configuration and current running snapshot remain unchanged until confirmation. A configuration change after checking blocks import. Successful confirmation discards the large payload and keeps a short receipt until acknowledgement/release or expiry; retries also recover after a state commit followed by a receipt-write failure without incrementing the revision twice.

The local suite passed **130 tests**, including nine checked-import cases covering exact contents/settings/snapshot preservation, skipped repeat validation, token-only confirmation, cancellation, expiry, quota cleanup, revision conflicts, token misuse and idempotent retries.

The installed 0.1.1-16 packages passed **11 real LuCI backup checks** and **8 JS workflow checks**, with no unexpected browser errors. For a generated 2 MiB SubConverter Flutter backup, clicking confirmation through completion of the compact page reload took **937 ms**: nine RPC POSTs totaling **1,466 bytes**, with a maximum individual POST of **203 bytes**. The confirmation interval contained no upload begin/append/invoke calls. This measures the generated fixture on QEMU, not initial upload/checking time or physical-router performance. Evidence: `build/vm/backup-ui-results.json` and `build/vm/js-ui-results.json`.

Cancellation released staged contents without changing the saved revision; a concurrent configuration edit blocked confirmation without another upload and preserved that edit. Adaptive transport still recovered from three oversized POSTs, three oversized replies and one deliberately lost upload acknowledgement under simulated 4 KiB channel limits. Installed backend/frontend hashes matched the package sources. The original one-source, no-override Home configuration was restored, with the service stopped and boot enablement off; no temporary transfer or job-payload files remained. Both installed packages are 0.1.1-16.

### Preserve LuCI login on installation: 0.1.1-15

The previous package hooks used `rpcd restart` in the core defaults, core postrm and LuCI postinst. That destroys the active LuCI session. These hooks now use `rpcd reload`, matching LuCI's standard postinst behavior. OpenWrt rpcd handles HUP by freezing sessions, re-executing and thawing them while reloading plugins/ACLs. SDK and application-only IPK lifecycle scripts are aligned, and package verification rejects future restart regressions.

`tools/verify_upgrade_session.cjs` kept a real Chrome browser authenticated while running opkg against the supplied VM. The 0.1.1-14 → 0.1.1-15 transition still invalidated the session because the old core's postrm ran first. After authenticating again, forced reinstallation of the installed 0.1.1-15 core and then LuCI package both preserved the exact session ID, RPC authorization, rpcd PID and authenticated page after reload. Configuration and active-snapshot file hashes and boot enablement stayed unchanged. Evidence: `build/vm/upgrade-session-results.json` and `build/vm/upgrade-session-preserved.png`. No session IDs, cookies or passwords are written into the results.

This is a package-lifecycle change; the backend and frontend application behavior from 0.1.1-14 is unchanged. Package contents/hook checks passed. The VM remains on 0.1.1-15 with the original Home output and the service stopped/boot-disabled.

### UI responsiveness: 0.1.1-14

The 0.1.1-13 frontend fetched the full configuration on load and sent/received it again for every save. A generated 1,952,878-byte configuration (500 nodes, six JS scripts) reproduced the reported multi-second waits on the supplied VM. Each source/override was also parsed/validated again on unrelated saves, and jobs waited 750 ms before their first poll.

Ordinary `get` and save replies now support compact views. The existing RPC methods accept single-item reads and revision-checked field/settings/removal patches; full get/save/export remain compatible. Source cache contents load only when expanded, and scripts load individually for editing. Unchanged content skips YAML parsing and JS validation; changed content, new items and imports still validate. Expensive parsing/rendering runs outside the state lock, with optimistic revision validation repeated under lock at commit. Compact-view replacements are rejected to prevent accidental content loss. The first job poll starts at 100 ms, backing off to the previous 750 ms interval for longer operations.

Final installed-package measurements from `tools/measure_ui.cjs` (one before/after run per version, including real browser and LuCI RPC overhead):

| Action | 0.1.1-13 | 0.1.1-14 | RPC POST count before/after |
| --- | ---: | ---: | ---: |
| Login and initial view | 7,900 ms | 1,294 ms | 131 / 12 |
| Rename output | 16,171 ms | 850 ms | 250 / 4 |
| Save settings | 15,757 ms | 555 ms | 250 / 7 |
| Reload view | 6,978 ms | 363 ms | 128 / 9 |

Renaming a source without loading its cache took 857 ms. After explicitly loading the 1.86 MiB cache, changing its prefix and saving took 649 ms; unchanged cache content was not uploaded. Direct fixture comparison verified the cached YAML and all scripts remained exact. Evidence: `build/vm/ui-performance-before.json` and `build/vm/ui-performance-after.json`. These measurements characterize the generated fixture on QEMU, not the user's physical router or remote download/apply latency.

The local suite passed **121 tests**, including compact/full read integrity, incremental content validation, unchanged-content preservation, deletion references, malformed patches, stale revisions and a concurrent save during JS validation.

The final installed 0.1.1-14 packages passed **8 JS UI**, **12 router-settings UI** and **9 backup UI checks**. The asynchronous JS editor clears stale modal controls before loading its item; invalid syntax, execution errors and loop timeouts still preserve saved/running state. Large backup import/export and adaptive 4 KiB request/reply-limit recovery remain covered. All three runs reported no unexpected page/request errors. Original Home settings were restored with the service and boot enablement off.

### GitHub token URLs: 0.1.1-13

The reported URL used `https://TOKEN@raw.githubusercontent.com/...`. The current local Flutter `RemoteTextFetcher` extracts this user-info as a GitHub token and sends a Bearer header; FII previously rejected it before fetching. HTTPS GitHub/GitHubusercontent URLs now accept `TOKEN@host` and `USERNAME:TOKEN@host`. The stored/exported address is preserved, while the actual request URL has user-info removed. Percent-encoded credentials are decoded and checked for header-control characters. Other hosts still reject embedded credentials.

Authorization survives redirects only within the same scheme, hostname and effective port; after crossing origins it is never restored on subsequent redirects. New user-info in redirect targets is rejected. Download errors omit URLs and tokens, close HTTP error responses, and retain the previous script cache.

The local suite passed **114 tests**. All **7 fetch/authentication tests** also passed using the installed VM modules, including a real loopback HTTP redirect chain mapped through a test HTTPS handler (TLS itself is not simulated as verified). The final 0.1.1-13 installation passed **8 real LuCI JS checks**, now covering token-URL backup import and standalone JS editor save in the user's reported URL shape. The tests use dummy tokens; authenticated access to the user's private GitHub file was not performed. The model, fetch module and frontend hashes matched the installed files. Home settings and the original stopped/boot-disabled state were restored.

### Backup URL diagnostics: 0.1.1-12

The previous combined message did not distinguish an empty address, unsupported scheme, missing host or embedded credentials, and did not identify the affected item. URL validation now reports each source/override name and ID with its specific reason, aggregating up to 20 entries. Malformed IPv6/port parser errors are sanitized instead of echoing the address. Embedded credentials with an empty username are also rejected. Import trims leading/trailing URL whitespace without mutating the supplied backup. Empty URLs with cached local sources remain supported; HTTP(S) user-info authentication remains unsupported.

The import modal shows errors inline with preserved line breaks and clears the stale progress text. The local suite passed **107 tests**, including multi-item diagnostics, address/credential redaction, URL normalization, and preservation of saved state and the active snapshot after failed preview/import.

The installed 0.1.1-12 packages passed **9 real LuCI backup checks**, including inline diagnostics, normalized addresses, lossless 2 MiB import/export/save, recovery under 4 KiB request/reply caps and a lost upload acknowledgement. No unexpected browser errors occurred. This diagnostic build was superseded by 0.1.1-13 after the exact GitHub token URL was provided.

### JavaScript overrides: 0.1.1-11

JS overrides follow the current local SubConverter Flutter `js_override_service.dart` and `mihomo_yaml_service.dart` behavior: synchronous `main(config)`, complete-object return, globals first, selected overrides in association order, each override once, and YAML/RULES/JS applied in the mixed list order. Each JS gets a fresh context. Backup migration preserves JS type, source and associations. The LuCI editor supplies a JS type and starter template; remote refresh validates downloaded JS before replacing cached source.

`node` is an explicit dependency in both the SDK Makefile and the IPK builder. The VM installed stock `node 20.20.2-r1` through its 24.10.8 feed. Node runs only during validation/rendering, with no inherited NODE_OPTIONS or proxy/credential environment. Linux root callers drop the child to UID/GID 65534 with no supplementary groups. The context receives JSON data and JS intrinsics, no Node APIs or host callbacks. Dynamic code generation is disabled. Each evaluated stage has a 2-second timeout; Python terminates the child after 6 seconds if necessary. Old-space heap is capped at 128 MiB, and configuration input/output at 8 MiB. Node vm is not a separate security boundary; these restrictions should not be represented as complete isolation from hostile code.

The local suite passed **103 tests**, including 12 new JS cases with real Node execution. The installed Linux runtime passed **8 VM checks**: unprivileged execution, mixed order/deduplication/fresh lexical contexts, actual Mihomo validation, validation without running main, bad/async returns, infinite-loop termination, absent host APIs/clean console protocol, and retention after a bad remote refresh. A generated JS configuration passed the installed Linux Mihomo `-t`.

The final real LuCI workflow passed **8 checks**: Flutter JS backup import; mixed-order preview; JS editor creation; syntax failure retaining revision; actual Mihomo activation; execution error preserving the active snapshot; main-loop timeout preserving the service; and no JavaScript page errors or failed browser requests. Evidence: `build/vm/js-ui-results.json`, `build/vm/js-vm-results.json`, `build/vm/js-override-preview.png`. Validation/preview use background RPC jobs so JS evaluation does not block a long synchronous LuCI request. The existing five Windows Mihomo configuration cases and authenticated-controller test also passed.

The final 0.1.1-11 installation also passed all **12 existing router-settings UI checks**, including save/apply, mixed TUN modes, access rules and three viewport widths. Source hashes matched all seven checked installed backend/frontend/ACL files. The original Home output and router settings were restored; the service and boot enablement remained off. The JS import checks use a generated Flutter-format backup, not the user's original backup file.

### Adaptive backup transport: 0.1.1-10

The user reported the same browser abort with a 1.86 MB file before any upload percentage appeared after the 0.1.1-9 repair. The actual device's HTTP/RPC limits were not available. A browser-side 4 KiB POST cap reproduced the same pre-progress failure against 0.1.1-9: the first 22,051-byte upload request was reset.

The 0.1.1-10 transport starts chunking requests above 1 KiB and keeps ordinary replies at or below 2 KiB. Upload and download chunks start at 16 KiB and halve on transport errors to a 1 KiB minimum, remembering the smaller size for the page. Upload retries verify already-written bytes so a lost acknowledgement cannot duplicate or overwrite content. Read requests carry their desired byte count. A protocol handshake detects mismatched frontend/backend packages, and transport errors include the operation and parameter byte count.

The final installed 0.1.1-10 packages passed **8 real LuCI backup checks** and **12 router-settings checks**. In addition to the normal 2 MiB import/reload/save/export cases, a 1.86 MiB preview completed with POST bodies capped at 4 KiB and one deliberately lost upload acknowledgement. A separate 4 KiB reply cap still allowed a complete 2 MiB export with exact content comparison. The harness rejected **3 oversized POSTs**, **3 oversized replies**, and **1 acknowledgement**; the client recovered from each. There were no unexpected browser errors. These are simulated channel limits on the VM, not a measurement of the user's router. The local suite passed **91 tests**, including smaller retry chunks after lost acknowledgements and negotiated response sizes. Original settings and Home output were restored, service and boot enablement stayed off, and no temporary transfer/payload files remained.

### Backup transport repair: 0.1.1-9

The reported `XHR request aborted by browser` was reproduced in the real LuCI import-preview request using generated SubConverter Flutter backups. A 4 KiB body succeeded, a 64 KiB body produced a missing RPC reply, and 256 KiB through 2 MiB bodies were reset by the HTTP/RPC path. No backup contents or settings were changed by these preview probes.

Requests larger than 16 KiB now use ordered, bounded base64 chunks, and replies larger than 32 KiB are downloaded in chunks. Each binary chunk is at most 16 KiB; the base64 reply fits in one ordinary RPC frame. Temporary files use random IDs, private permissions, a 64 MiB reservation quota, a 64-transfer limit and one-hour expiry. The RPC ACL exposes only the specific FII transfer methods; callers cannot provide filesystem paths or invoke arbitrary commands.

Import checks and confirmation run in background jobs with payloads in temporary files rather than process arguments. Import replies contain counts and warnings instead of repeating the backup; the subsequent configuration reload, save and export use the same bounded transport. Validation and revision checks continue to protect the saved state, router settings and running snapshot.

The final 0.1.1-9 packages passed **6 real LuCI backup checks** using a generated 2 MiB SubConverter Flutter backup: preview without changing saved revision, confirmed import and reload, unchanged router settings and active snapshot, lossless export, saving the large configuration without applying it, and validation failure without changing saved state. All backup RPC POST bodies remained below 64 KiB (maximum **22,059 bytes**); no browser request failures or JavaScript page errors occurred. The test restores the original configuration in `finally`. Evidence: `build/vm/backup-ui-results.json` and `build/vm/backup-preview.png`.

The local suite passed **89 tests**, including 9 new cases for UTF-8 across chunk boundaries, ordered/incomplete uploads, temporary-space reservation and expiry, allowed operations, bounded reply frames, disk-spooled jobs, failed-import preservation and RPC entry points. The existing **12 real LuCI router-settings checks** also passed against the final 0.1.1-9 installation, including save/apply and mixed TUN modes. Both scripts restored the original Home configuration and stopped service; no transfer or job-payload files remained.

### Proxy settings parity: 0.1.1-8

The five sections in Nikki's `view/nikki/proxy.js` were compared item by item against FII. The resulting proxy settings support:

| Nikki proxy section | FII behavior |
| --- | --- |
| Proxy Config | Master switch; independent TCP off / REDIRECT / TPROXY / TUN and UDP off / TPROXY / TUN; independent IPv4/IPv6 proxy and DNS-family switches; Fake-IP Ping |
| Router Proxy | Ordered, enableable rules matching UID, GID or cgroup v2; independent per-rule proxy and DNS decisions |
| LAN Proxy | Device selection with netifd names; ordered, enableable IP/IPv6/subnet/MAC rules with independent proxy and DNS decisions |
| Bypass | Mainland IPv4/IPv6 sets, separate destination TCP/UDP port ranges and common presets, DSCP, FWMark/mask |
| Misc | Editable IPv4/IPv6 reserved networks, TUN readiness timeout/interval; FII also exposes TUN stack and MTU |

Both application IPKs were reinstalled using `opkg` in the supplied VM. TUN uses its own mark `0xf102/0xffff`, table 20231 and priority 30001, with Mihomo auto-route/auto-redirect disabled. TCP and UDP can select different backends. FII's fw4 includes allow input and return forwarding from its own TUN device. The input rule is necessary for the system TCP stack's rewritten local connections; a reject-only test did not establish successful forwarding, so positive transfer checks were added.

The TUN link is checked for UP state and given one configured poll interval to finish its internal listener setup on cold start, before policy routing is installed. Mixed, System and gVisor were each checked using real IPv4/IPv6 TCP and UDP transfers from both the router and namespace client (8 positive checks per stack). IPv6 Fake-IP uses a separately named dummy interface and route. The interface is tagged and ownership-checked; stop removes it only when it belongs to FII. Unsupported TUN devices and missing cgroups are rejected before replacing the running snapshot.

The expanded `tools/verify_router_settings_vm.py` suite passed **135 packet-path checks** and includes both reject and successful forwarding oracles, DNS over TCP/UDP, mixed TUN modes, first-match ACL order, independent proxy/DNS decisions, UID/GID/cgroup matching, mainland IP bypass, port restrictions, DSCP/FWMark, editable reserved networks, Fake-IP exclusions, ping, fw4 reload and cleanup. Results are written to `build/vm/router-settings-results.json` after copying them from the VM.

The real LuCI script performs **12 checks**, including changing modes, applying TUN, editing and moving a UID rule, preserving DNS edits, rejecting an invalid IP without modifying saved state, and persisting mainland/DSCP/FWMark/port choices. It checks 1440/375/844 px layouts and records light/dark, mobile, access-rule and bypass screenshots under `build/vm/`.

China CIDR lists are bundled from the documented Nikki snapshot (7,362 IPv4 and 1,655 IPv6 prefixes). They are validated and used only for their enabled address family. Reserved networks and overlapping port ranges are collapsed; fake IPs remain intercepted even when reserved/DSCP/port filters would otherwise bypass them. Explicit access-control and FWMark decisions still apply.

These are isolated VM software-path checks. Physical switch/Wi-Fi behavior, hardware offload, sustained throughput, actual subscription-node connectivity and reboot/sysupgrade remain outside this pass. The packages are produced by the included builder rather than an OpenWrt SDK.

### Earlier router settings delivery: 0.1.1-7

Both packages were upgraded using real `opkg` in the provided QEMU VM at `127.0.0.1:8080` / SSH port 2222. The new `luci.fii network` RPC reads netifd interface mappings, link state, DHCP leases and system UIDs. Existing settings remain compatible and do not enable router-originated proxying on upgrade.

`tools/verify_router_settings_vm.py` ran inside the VM with dedicated `fii-client` and `fii-server` network namespaces connected through veth pairs. The server exposes a TCP responder and UDP echo service. Mihomo rules reject the test ports, so a successful baseline followed by a rejected connection establishes actual interception, rather than merely checking that nft accepts the rules. Temporary fw4 input/forward allowances isolate the proxy behavior from guest-zone policy.

**41 packet-path checks passed**, covering:

- Router-originated and LAN TCP / UDP through TPROXY.
- TCP / UDP DNS redirection for both origins, with actual Fake-IP DNS answers.
- IP exclusion, MAC inclusion, unmatched inclusion bypass, and matching DNS scope.
- Router UID bypass for TCP / UDP.
- TCP REDIRECT plus UDP TPROXY for both origins.
- UDP disabled while TCP remains intercepted.
- LAN proxy disabled independently of router proxy.
- IPv6 TCP / UDP and DNS for both origins; IPv6 DNS disabled without disabling IPv4 DNS.
- fw4 reload reinstalls interception; stopping FII removes the table and restores both original paths.

Evidence: `build/vm/router-settings-results.json`. The script restores saved settings, active snapshots and boot preference, removes its namespaces, and stops the temporary service. It requires `FII_VM_TEST=1` to avoid accidental execution outside the disposable VM.

The real LuCI page is exercised by `tools/verify_router_ui.cjs`: checkbox state agrees with persistence, save-and-apply changes the active revision, DNS edits survive category changes and reach the backend, invalid device addresses preserve the stored revision, and core-only mode hides transparent-proxy fields. Layout is checked at **1440, 375 and 844 px** (including landscape and reduced motion). Light/dark screenshots are recorded. A LuCI-specific checkbox bug was fixed: presence of `checked="false"` still checks a native checkbox, so checkbox helpers now set the DOM property directly. UI results and screenshots are in `build/vm/router-settings-ui-results.json` and `build/vm/router-settings-*.png`.

Remaining boundaries: this uses an isolated VM with a known direct/reject fixture. It does not establish real subscription-node connectivity, physical switch/Wi-Fi behavior, hardware offload performance, sustained throughput, reboot/autostart or sysupgrade behavior. TUN was added and tested in 0.1.1-8 above. The packages are built with the included IPK builder, not an OpenWrt SDK.

### Earlier service and core-manager validation

The user's actual firmware image `istoreos-24.10.8-2026073111-x86-64-squashfs-combined-efi.img` was booted under QEMU 11.1 (WHPX) on the development PC and the packages were installed through the real `opkg`. This surfaced and led to fixes for three defects that local checks had missed (all fixed in **0.1.1-2/-3** and covered by new checks):

1. The hand-built IPK data tarballs lacked directory members, so `opkg` failed extraction with `wfopen` errors on the router. `tools/build_ipk.py` now emits a member for every parent directory, and `tools/verify_packages.py` asserts directory coverage.
2. `fii rpc-call` only understood a `{"payload": "<json>"}` envelope, while rpcd exec plugins deliver the ubus call params as flat JSON on stdin — every parameterised LuCI/ubus call (core check, channel switch, apply…) silently lost its arguments. Parsing now accepts both forms (`rpc_payload()`, regression-tested).
3. LuCI's `E()` stringifies attribute values, so `disabled: false` still disabled buttons: 重启/停止服务 and the reorder buttons were permanently dead in the real theme. The `button()` helper now only emits the attribute when disabling is intended.
4. iStoreOS Argon ships a decorative `header::after` rule with a bare element selector, so it also painted a 32px primary-colored strip (`pointer-events: auto`) across the FII view's own `<header>` — covering the title and swallowing clicks on the reload button. **0.1.1-5** neutralises it (`.fii-header::after { content: none }`) and scopes the view header (`position: relative`); the earlier top-bar-clearance experiment built on a wrong diagnosis was removed. Verified in the VM: title, badge and reload button fully visible and clickable at 1367/1280/900 px widths.
5. The same theme strip also clipped the top half of the "MIHOMO FOR OPENWRT" eyebrow. **0.1.1-6** measures the strip at render time and pads the view past it (themes without such a strip are left untouched; verified: strip ends at 90px, eyebrow starts at 110px). The per-tab page headings (title + subtitle duplicating the tab name) were removed at the user's request — each tab now opens directly with its content and the primary action button.

Verified on the VM after the fixes (final clean install of 0.1.1-3):

- `opkg install` of both IPKs succeeds with all files extracted and correct executable permissions; dependencies resolve against the stock 24.10.8 feeds (python3, python3-yaml pulled in; kmod-nft-tproxy / kmod-nft-socket / ca-bundle / ip-full already present in iStoreOS).
- `ubus list luci.fii` registers the exec plugin; parameterised calls (`core_channel`, `core_check`, `core_update`, `apply`, `service`, `select`) work end to end.
- **Core manager on-router download**: `core_check`/`core_update` fetched release `v1.19.32` (22.9 MB) from GitHub, verified the official digest, and installed to `/etc/fii/bin/mihomo`; `mihomo -v` reports `Mihomo Meta v1.19.32 linux amd64`.
- Saving a source + output through the RPC and applying it rendered, validated and activated the config: procd-supervised mihomo process runs, `nft list table inet fii` shows the TPROXY + DNS-hijack chains, `ip rule` installs the fwmark 0xf101 → table 20230 policy route, the controller API answers, and proxy-group node switching works.
- Service stop removes the nft table, policy rules and process; start restores them. The same operations performed by clicking the LuCI page (Argon theme) produce identical results — the page shows live status, core version, channel selector and proxy groups.
- Package reinstall preserves `/etc/fii` state (sources, outputs, installed core, channel choice).

That earlier pass did not cover client packet forwarding. The 0.1.1-7 namespace tests above now cover packet forwarding from an isolated VM client; physical client networks, sustained throughput and reboot/sysupgrade remain separate acceptance tasks.

## Local evidence

- `python -m unittest discover -s tests -v`: **151 tests**. Covers source order, regex filtering before prefix, collision-free names, global / selected override ordering, YAML prepend / append / replacement semantics, rule insertion, quota exhaustion / recovery, malformed or unsafe YAML, share-link conversion, revision conflicts, refresh cache retention, concurrent edits, job dispatch, transactional activation and rollback, generated firewall scope / cleanup, rpc-call stdin parsing, legacy router-setting defaults, device/UID validation, access-control generation, UDP/DNS options, network-choice discovery, bounded backup transport, lost-acknowledgement retries, negotiated download chunks, JavaScript overrides, per-item URL diagnostics and GitHub token authentication/redirects, compact reads, partial saves, concurrent validation/commit handling, reusable checked imports with expiry/cancellation/idempotent retries, Flutter sorting reconstruction, exact reorder patches, synchronized editor snapshots, preview execution/progress reporting and native file-upload reservation/validation/cleanup/ACL/race handling.
- Core downloader suite (19 tests): Release / Alpha channel resolution, asset selection (Linux amd64-compatible), digest verification against official release metadata, ELF64 x86_64 and static-link checks without executing the Linux binary, channel selection persistence, unchanged-version update gating, atomic swap with service kept running, and restore of the previous binary / version record on disk, verification or startup failure.
- Official Mihomo **v1.19.32 Windows amd64 compatible**: subscription, TPROXY runtime, REDIRECT runtime, core-only runtime and IPv6 runtime YAML passed `mihomo -t` (5 cases).
- The same real Windows core started with only its authenticated loopback controller for a controller test. `GET /version`, proxy-group discovery and selection from `HK` to `DIRECT` passed. This did not exercise transparent sockets or router packet forwarding.
- `node --check luci-app-fii/htdocs/luci-static/resources/view/fii/app.js`: passed.
- ShellCheck **0.11.0**, POSIX sh mode: init script, defaults, rpcd entrypoint, core wrapper and firewall include passed. Annotations cover rc.common-owned variables and the trap-invoked cleanup function.
- Browser harness used the actual LuCI view JavaScript and actual Python configuration backend. Creating a local YAML source, creating a global RULES override, associating a source with an output, saving, and output preview passed. Generated YAML contained both sample nodes and the global rule. No error notifications appeared during the workflow.
- Layout checked at **1440×1050** and **390×844**: no document horizontal overflow. Inputs inspected in the desktop view had labels.
- IPK inspection checked control/data containers, versions, architectures, hashes, script executable permissions, LF line endings, absence of generated caches and configuration state, and **confirmed the application packages contain no Mihomo binary** (`tools/verify_packages.py`). The 0.1.1 packages no longer ship or depend on a core package; the core is downloaded from the LuCI page after installation.
- Linux and Windows core assets were checked against the SHA-256 digests supplied by the official GitHub release API. `tools/verify_core_downloads.py` live-checked **both** channels on 2026-10-19: Release `v1.19.32` and Alpha `alpha-94b13ae` were downloaded, digest- and ELF-verified without execution (`build/core-download-checks.json`). Release provenance is recorded in `NOTICE.md`.

Generated evidence is in `build/mihomo-verify/results.json`, `build/desktop-preview.png`, `build/mobile-preview.png` and `dist/manifest.json`. The development preview has its own sample database under `build/preview-root`; it is excluded from every IPK.

## Verification boundaries

This development machine is Windows and has no installed WSL distribution or OpenWrt SDK. The QEMU VM supplies the OpenWrt runtime used above. Verification on the physical target router is still required for its actual topology, boot/crash/respawn behavior under load, hardware offload, performance, memory/storage usage and sysupgrade.

The included IPKs were assembled with `tools/build_ipk.py` and structurally verified, rather than built in the OpenWrt SDK. Standard SDK Makefiles are included but were not executed locally.

## Router acceptance procedure

After installing on a reachable test router:

1. Verify `opkg print-architecture`, `ubus -v list luci.fii` and `fii status`. Confirm LuCI **Services → FII** loads and the overview reports "no core installed".
2. In **Overview → Mihomo Core Manager**, check updates on both Release and Alpha channels, download the selected channel, and confirm the installed version appears. Repeat with the service running to verify the running core is replaced only after verification, and that a forced failure (e.g. network cut during download) leaves the previous core and service intact.
3. Add a real subscription, refresh, check traffic information, preview nodes, create an output and check its YAML.
4. Verify actual LAN device names with `ip link`. Apply first with a test client or during an available recovery window.
5. Check `logread -e fii`, `nft list table inet fii`, `ip -4 rule show` and `ip -4 route show table 20230`.
6. From a LAN client, verify TCP, UDP and normal TCP/UDP DNS. Check `.lan` / `.local` resolution against the site's existing DNS policy; add suitable DNS policy / hosts overrides where needed.
7. Switch a proxy group in the overview and verify the selected node is preserved after a service restart.
8. Reload firewall4; verify rules remain present once and client traffic still works.
9. Stop FII; verify `inet fii` and its routing rules are removed and normal network behavior returns.
10. In an isolated test, terminate the Mihomo child and verify cleanup plus procd respawn. Do not terminate unrelated proxy processes.
11. Try an invalid output / occupied listener port; verify FII keeps or restores the previous working snapshot. Test failed subscription downloads and exhausted quota without modifying the actual source record's enabled state.
12. If IPv6 is used, enable its interception and independently check IPv6 TCP, UDP and DNS. If disabled, confirm its original path is intentionally retained.
13. Enable autostart, save and apply; reboot and verify the selected output returns. Inspect Python / Mihomo memory, overlay space and sustained traffic performance on the J4125.

The router acceptance result should be added only after actually performing these checks.


## 2026-10-21：概览网址延迟测试（0.1.1-45）

- 概览加入百度、Google/gstatic generate_204、GitHub 响应卡片，支持点击重测、全部测试及网站名称/URL 的添加、编辑、删除。最多 24 个，列表存入 settings.urlTestSites，旧配置按需补齐默认项；空列表不重建默认项。
- 测试由路由器 Python 独立进程发起 HTTP GET，验证 HTTPS 证书，忽略环境代理变量，使用当前路由/本机透明代理策略。收到首个响应头即结束，不跟随跳转、不读取正文；HTTP 4xx/5xx 保留耗时和状态码，连接/DNS/TLS 错误单独显示。子进程整体 8 秒上限，跨浏览器最多 3 个探测。结果仅保存在页面内存与 /tmp 任务文件中。
- 设置修改仅保存列表，无需应用或重启核心。RPC 已同步 Python、ucode、LuCI 和 ACL 白名单，并走后台任务；旧版元数据缓存缺少测试网站时回落验证器，避免升级后显示空列表。
- Python 全量 **242 项通过**，其中新增 8 项覆盖旧状态/缓存迁移、空列表、URL 校验、真实本地 HTTP 服务状态码与只读响应头、重定向、环境代理隔离、子进程测试、保存与过期 URL、总超时、全局并发、错误脱敏和 RPC 权限。真实 OpenWrt ucode VM **18 项通过**，补充旧缓存默认项的回落断言。
- Chrome 中验证新增、修改、删除、修改 URL 后清除旧耗时、测试中切换页面及返回后显示结果。实际 iStoreOS Argon 主题验证并修正标题操作区被 h3 宽度挤成两行的问题。手机断点和深色样式已实现，本轮未做实体手机交互验证。
- 两个应用 IPK 已构建并通过包结构、权限、依赖、JS/CSS 内容哈希检查；实际路由器已安装 **0.1.1-45**，35 个文件内容与权限核验成功。升级前后 state.json、active.json、运行 YAML、内核及 DHCP 文件哈希一致，保存/运行版本均为 69，内核和管理 API 正常。最终升级备份：/etc/fii/backups/package-upgrade-0.1.1-45-20261021-143705。
- 实际 LuCI 点击“全部测试”返回百度 HTTP 200 / 97 ms，Google HTTP 204 / 139 ms，GitHub HTTP 200 / 219 ms（14:38:56 的单次样本，耗时会随网络变化）。

## 2026-10-22：Mihomo 通用参数默认值（0.1.1-46）

- 参照 wiki.metacubex.one/config/general，Mihomo 页新增「通用参数（默认值）」卡片：运行模式、日志等级、进程匹配、统一延迟、TCP 并发、Keep-Alive 间隔/空闲、GEOIP dat 格式、GEO 自动更新与间隔、ETag 支持，共 11 个 settings 键。默认值与官方一致；进程匹配按官方的路由器建议默认 `off`（唯一有意偏离官方默认的字段）。经 `runtime.overlay()` 的 `prefer()` 注入运行配置，覆写中显式配置的同名字段优先。`render.py` BASE 模板移除了硬编码的 `mode: rule` / `log-level: info`，否则会挡住 settings 默认值。
- 宿主机 Python 全量 **244 项通过**（新增默认值注入/覆写优先/旧状态回填/8 种非法值拒绝）；`node --check` 验证 app.js 语法。
- QEMU iStoreOS 24.10.8 VM（Mihomo v1.19.32，既有 revision 207 状态）同步代码后验证：
  - 旧 state（缺全部 11 键）经 `Store().read()` 内存回填为期望值，磁盘文件未被改写；save 后全部新键随写路径持久化。
  - `activate("p1")` 端到端成功（内核 `-t` 校验通过），`/tmp/fii/config.yaml` 含全部 11 个字段且 `find-process-mode: off`；启动服务后 `/tmp/fii/ready` 存在、内核运行、透明代理就绪。
  - 控制器 `/configs` 实时值与配置一致（内核 v1.19.32 接受 `keep-alive-idle`、`etag-support`、`geo-update-interval` 等较新字段）。
  - 内存构造临时全局覆写（mode/global、log-level/warning、keep-alive-interval/45、unified-delay/true、find-process-mode/always）经真实 render+overlay 链路验证覆写优先，其余字段保持 FII 默认。
  - `cli.dispatch("save")` 保存 logLevel=debug、geoAutoUpdate=true、tcpConcurrent=true 后重新 apply，服务重启且控制器实时值同步变化；`logLevel=trace` 被校验拒绝；随后还原默认并再次 apply，实时值回到默认（`find-process-mode` 保持 `off`）。
  - LuCI 实际服务的 app.js（HTTP 200）包含新卡片文案；`ubus call luci.fii status` 报告 running=true、activeOutputId=p1。
- 本轮 VM 验证采用文件同步而非 IPK 安装；浏览器端三层 JS 缓存未逐层清理，未做实体浏览器点验（新卡片为静态表单，语法与文案已核）。

### 2026-10-22 追加：移除 Mihomo 页动态基础配置卡（0.1.1-46）

- 用户确认 Mihomo 页只保留全局配置默认参数（内核管理 + 连接与端口 + 通用参数），删除按旧版迁移数据动态渲染的「Mihomo 基础配置」卡（`mihomoBasics`）；迁移覆写本身保留并改为在覆写页与普通全局覆写一样管理（原先覆写页按 `origin=legacy-mihomo-settings` 隐藏它，去掉隐藏避免出现"仍生效但无处可见/编辑"的覆写）。后端 `migrate()` 不变，旧状态与旧备份的迁移语义不受影响。
- Python 244 项通过（纯 UI 改动，无后端变更）；`node --check` 通过；app.js 中 `mihomoBasics`/`basicsBody`/`legacy-mihomo-settings` 引用清零。
- VM 复验：同步 app.js 并清 modulecache 后，服务的 JS 无基础配置卡代码、含通用参数卡；重新 apply 成功、running=true、transparent ready；apply 前后 `/tmp/fii/config.yaml` 与 active.json 的 YAML md5 均为 `328c817d422e…`，字节级一致，证明生成的运行配置不受影响；state 中迁移覆写仍在（覆写页可见可编辑，正文为 JSON、按 YAML 编辑兼容）。

### 2026-10-22 追加：GEO 数据独立成卡与布局修正（0.1.1-46）

- 按用户反馈：GEO 参数（自动更新、更新间隔、GEOIP dat 格式）从通用参数卡移出，独立「GEO 数据（默认值）」卡并新增 GeoIP / GeoSite / MMDB / ASN 四个自定义下载地址（`geox-url`，留空使用官方地址；仅写入已填写类型，其余由内核补内置地址）。`geoAutoUpdate` 默认改为开启（第二个有意偏离官方默认的字段，第一个是进程匹配默认 off）。修复设置表单复选框与文字的基线错位（`.fii-check input[type=checkbox]{margin:0}`，浏览器默认 margin 导致）。
- Python 244 项通过（默认值/geox-url 注入与覆写优先/空白地址丢弃/4 组非法值拒绝）；`node --check` 通过。
- VM 验证：保存 `geoxUrls={geoip:…}` 后 apply，`/tmp/fii/config.yaml` 的 `geox-url` 仅含 geoip 一项、`geo-auto-update: true`，控制器 `/configs` 实时一致；清空后 apply 无 `geox-url` 键。Playwright 实测 LuCI Mihomo 页：通用参数卡（5 字段网格 + 3 开关）、GEO 卡（3 项 + 4 地址框）分组正确，复选框与文字垂直居中，GEO 数据自动更新默认勾选；内核运行正常。

### 2026-10-22 追加：控制器独立成卡与外部用户界面（0.1.1-46）

- 控制器端口、监听地址、密钥从「连接与端口」移入新「控制器（默认值）」卡，并补齐外部用户界面三项（`external-ui` 目录、`external-ui-name` 子目录名、`external-ui-url` 下载地址）。三项从 `runtime.overlay()` 的强制移除列表解除，改为 settings 默认值 `prefer()` 注入（仅写非空项；覆写显式配置优先，覆写中的 external-ui 不再被剥离）。校验：目录为相对内核工作目录的路径（禁绝对路径与 `..`）、名称为单段、下载地址须为 http(s) URL、名称/地址依赖目录先填写。
- Python 244 项通过（默认不写、填齐写入、覆写优先且不被剥离、6 组非法值拒绝、旧状态回填）。中途发现空字符串未放行导致全量报错，已修复（空=不启用）。
- VM 验证：保存 `externalUi=ui` 后 apply，`/tmp/fii/config.yaml` 含 `external-ui: ui`（name/url 缺省不写）、服务重启后 running=true；清空后 apply 键消失。Playwright 实测 Mihomo 页四张卡（连接与端口 / 控制器 / 通用参数 / GEO 数据）布局与字段完整；注意 playwright 长会话的浏览器缓存需关掉浏览器重开才会取新 JS（fetch cache:reload + 同页导航不够）。

### 2026-10-22 追加：密钥显隐与三列布局统一（0.1.1-46）

- 控制器密钥收进 3 列网格（与端口/监听地址同行），不再通栏；`field()` 助手为密码框内置「显示/隐藏」切换按钮（`aria-pressed` 跟随，flex 行内布局 `.fii-input-row`）。四张卡的设置网格统一 `fii-cols3`（≥1200px 三列、≤700px 单列，原 `fii-port-grid` 更名），路由器设置页两列布局不变。
- Python 244 项通过；`node --check` 通过。Playwright 实测：控制器卡两行各三字段、密钥框宽度与其他列一致、显示/隐藏按钮切换与 pressed 状态正确。

### 2026-10-22 追加：通用参数增加 IPv6 流量开关（0.1.1-46）

- 按官方 general 页 `ipv6`（允许内核接受 IPv6 流量）在通用参数卡开关组新增「IPv6 流量」（settings `coreIpv6`，与 FII 透明代理模式的 `settings["mode"]`、路由器 IPv6 接管的 `settings["ipv6"]` 区分）。默认关闭——第三个有意偏离官方默认（官方默认 true），与路由器 IPv6 接管默认关闭的姿态一致，升级零行为变化。实现沿用既有坑位知识：先从 `mihomo.py DEFAULTS` 移除写死的 `ipv6: false`（否则挡死 prefer()），再由 `runtime.overlay()` 注入；覆写显式配置优先，`dns.ipv6`（AAAA 解析）仍归覆写。
- Python 244 项通过（默认 False、调参 True、覆写 `ipv6: true` 优先、旧状态回填）。VM 验证：无覆写时 coreIpv6=True 生效为 true；VM 旧迁移覆写携带 `ipv6: false` 时保持覆写优先（运行配置 false）；save→apply→还原闭环服务正常。Playwright 实测开关渲染与帮助文案正常。

### 2026-10-22 追加：卡片顺序调整（0.1.1-46）

- 按用户要求把「通用参数（默认值）」移到「Mihomo 内核管理」正下方；页面顺序为 内核管理 → 通用参数 → 连接与端口 → 控制器 → GEO 数据。纯 UI 顺序调整，无后端变化；`node --check` 通过，Playwright 实测顺序正确、四卡各渲染一次。

### 2026-10-22 追加：Mihomo 页二级页签（0.1.1-46）

- Mihomo 页新增二级页签「内核」/「全局配置」，复用路由器设置页的 `fii-settings-tabs`/`showSection` 模式（`self.mihomoSection` 记忆选中态，跨重绘与页签切换保持）。「内核」放内核管理卡片，「全局配置」放通用参数、连接与端口、控制器、GEO 数据四张卡与保存按钮；默认选中「内核」。纯 UI 变化。
- `node --check` 通过、Python 244 项通过；Playwright 实测：默认内核页签仅显内核卡、切换全局配置显四卡+保存、切概览再返回停留在全局配置。

### 2026-10-22 追加：嗅探二级页签（0.1.1-46）

- Mihomo 页二级页签扩展为 内核 / 全局配置 / 嗅探。「嗅探」卡提供启用、强制嗅探 DNS 映射、嗅探纯 IP、覆盖目标地址四个开关与 HTTP/TLS/QUIC 端口、跳过域名列表（entries 控件），默认值采用用户提供的配置（enable/force-dns-mapping/parse-pure-ip 为 true、override-destination 为 false、HTTP 80/8080/8880、TLS·QUIC 443/8443、skip-domain 含 +.lan/+.local/+.home.arpa/Mijia Cloud）。保存按钮与全局配置共享，一次保存全部默认值；端口在客户端与服务端双重校验为 1–65535 数字。
- `runtime.overlay()` 按字段级 setdefault 注入 sniffer（含 sniff 各协议）：覆写只改单个字段（如仅关 enable 或仅改 TLS 端口）时其余字段仍取 FII 默认值；skip-domain 允许含空格条目（默认含 "Mijia Cloud"）。空端口列表表示不写入该协议。
- Python 245 项通过（默认块整体断言、调参、部分覆写字段级优先、6 组非法值拒绝、回填）。VM：apply 后 `/tmp/fii/config.yaml` 的 sniffer 块与用户配置逐字段一致，真实内核 `-t` 校验通过、服务 ready。本轮 playwright 不可用，前端以服务端 JS 内容（含嗅探卡与页签代码）+ 与已验证模式（tabs/toggle/entries/cols3）一致为证，浏览器点验待后续补做。

### 2026-10-22 追加：TUN 二级页签（0.1.1-47）

- Mihomo 页二级页签扩展为 内核 / 全局配置 / 嗅探 / TUN。「TUN」卡按官方 tun 页与用户截图取常用字段：协议栈（mixed/system/gvisor，默认 mixed，与旧迁移覆写口径一致）、MTU（默认 1500，1280–9000）、GSO 与 GSO 最大长度（默认 65536，1–1048576）、端点无关 NAT（默认关闭）。截图中的网卡名称/自动路由/自动出口/DNS 劫持为 FII 接管固定项不开放；严格路由、排除网段依赖 auto-route（FII 固定关闭）故不开放；截图的「ICMP 转发」在 mihomo 无对应字段。
- 实现第三次踩中 BASE/DEFAULTS 挡位坑：先从 `mihomo.py DEFAULTS` 移除写死的 `tun: {stack, mtu}`，`runtime.overlay()` 再按字段 setdefault 注入（覆写显式配置优先）。settings 键名绕开 LEGACY_KEYS：`coreTunStack`/`coreTunMtu`/`tunGso`/`tunGsoMaxSize`/`tunEndpointIndependentNat`。
- Python 246 项通过（新增 tun 默认/调参/字段级覆写优先/接管字段无视覆写强制；回填与非法值用例扩展——含 `coreTunStack: mips` 拒绝以锁定三选项口径）。
- VM 验证：默认 apply 后 tun 块含 5 个新字段默认值；保存 gvisor/1420/gso/32768/EIM-NAT 并把接管方式切到 TUN 后 `enable: true`、fii-tun 设备 UP；VM 携带的旧迁移覆写声明 stack/mtu 时覆写优先（设备 MTU 实际 1500），从覆写中移除 tun 后 settings 生效（stack: gvisor、设备 MTU 实际 1420）；全部恢复基线后服务 READY。注意点：TUN 接管是路由器设置的 TCP/UDP 代理方式选 TUN，`mode: core` 反而不做任何拦截（modes() 返回 off）；VM 上走真实 save 路径需携带当前 `revision`，`python3 -m fii.cli` 需 `PYTHONPATH=/usr/lib`。前端本轮仍无 playwright，以服务端 JS + 既有同构页签模式为证。

### 2026-10-23 追加：DNS 二级页签（0.1.1-48/49）

- Mihomo 页二级页签扩展为 内核 / 全局配置 / DNS / 嗅探 / TUN。DNS 页三张卡：基础（启用、模式、IPv6 解析、缓存算法 LRU/ARC、遵循分流规则、DoH 优先 HTTP/3、系统 Hosts）、Fake-IP（网段 4/6、过滤域名、过滤模式黑/白/规则）、DNS 服务器与分流（默认/引导/代理节点/直连/Fallback 五组列表 + nameserver-policy 与 proxy-server-nameserver-policy 两个域名策略编辑器，支持 +.example.com、geosite:cn 匹配语法）。`dns.listen` 仍归 FII 接管。DNS 模式默认 **redir-host**（用户指定），非 Fake-IP 模式时整张 Fake-IP 卡隐藏；服务器每组下方带用途说明。
- 注入语义与 sniffer/tun 不同：render 的 `seed()` 先把 `DEFAULTS['dns']` 整块填入，故 `mihomo.seed_dns()` 用「字段缺失或仍等于内置默认值 → 用设置值替换」模型（覆写字段非默认值即胜出；覆写恰等于内置默认值时与未配置不可区分）；proxy-server-nameserver 视「等于 nameserver 镜像」为默认来源；nameserver-policy 条目级深合并（覆写加条目时内置 +.lan/+.local 保留）；空可选列表/空策略不写入。设置键 18 个 `dns*` 前缀、全部避开 LEGACY_KEYS 旧名；引导 DNS 必须纯 IP（`ip_address(223)` 会把整数当地址，先 isinstance(str) 再解析）。
- Python 247 项通过（新增 DNS 默认值/调参/等值替换语义/非法值约 20 组；`test_proxy` 的 Fake-IP ping 豁免用例改为显式传 fake-ip 配置）。真实内核 v1.19.32 `-t`：默认 redir-host 与全量调参（含 geosite:cn 的 proxy-server-nameserver-policy，内核加载 110446 条 geosite 记录）均通过。Playwright（preview.html + 本地 mock）：tab 渲染、策略条目增删、保存差量载荷仅含变更字段、Redir-Host 下 Fake-IP 卡隐藏/切 Fake-IP 显现。
- VM 验证：0.1.1-48→49 升级均自动恢复服务；`fii save/apply` 后 `/tmp/fii/config.yaml` 的 dns 块逐字段生效；redir-host 生效前需注意两件事——①状态文件在首次 save 时会把当时的 settings 默认值整体固化（既有机制），48 之前装过并保存过的机器 dnsEnhancedMode 已固化为 fake-ip，需在 DNS 页改一次；②旧版迁移覆写（origin=legacy-mihomo-settings）若声明了 dns 块会按覆写优先压住设置，测试机已将其 dns 块移除（保留 ipv6/tun）。代理节点 DNS 策略默认空 `{}`，不写入运行配置。

### 2026-10-23 追加：跟随运营商 DNS（0.1.1-50）

- DNS 服务器卡新增「跟随运营商 DNS」开关与「运营商 DNS 位置」（兜底=排在默认 DNS 之后（默认）/ 优先=排最前），并实时显示检测到的上游 DNS（来自 network RPC 的 wanDns）。开启后 `runtime.overlay()` 在**生成运行配置时**经 `network.wan_dns()` 读取 `/tmp/resolv.conf.d/resolv.conf.auto`（netifd 从 DHCP/PPPoE 写入；过滤回环/链路本地/非法与重复地址，最多 4 条）合并进 dns.nameserver——地址变化在下次应用/重启时跟进；未检测到时在应用结果 warnings 里提示。默认 DNS 被覆写显式接管（nameserver ≠ 设置列表）时不合并，覆写保留完整控制权。
- 设置键 `dnsUseWanDns`/`dnsWanDnsPrefer`（均默认关）；ucode 原生 `network` 响应同步补 `wanDns`（提示级正则过滤，严格校验在 Python 注入路径）。
- 坑：rpc.uc 的正则是 POSIX ERE，**不支持 `(?:` 非捕获组**——首版写法导致整个 ucode 插件加载失败、`luci.fii` 未注册（只剩 exec 后备 `luci.fii.worker`），改用普通捕获组后恢复；改 rpc.uc 后必须在 VM 上 rpcd restart 并确认 `ubus list | grep '^luci.fii$'`。ucode 夹具（本地无 ucode 运行时）补 wanDns 断言。
- Python 249 项通过（新增：wan_dns 解析过滤去重、注入兜底/优先/去重/覆写优先/空检测告警、network choices 含 wanDns、非法布尔拒绝）。VM 端到端：slirp 上游 10.0.2.3 兜底合并到 nameserver 末尾、切「优先」后 apply 排最前；ucode 原生 network 返回 wanDns=['10.0.2.3']；Playwright 冒烟：开关/位置/检测提示渲染、保存差量载荷仅含 dnsUseWanDns。

### 2026-10-23 追加：DNS 页分组视觉重构（0.1.1-51）

- 用户反馈「DNS 配置项多、各项之间没有明显边界」。「DNS 服务器与分流」卡重构：①「跟随运营商 DNS」（开关+位置+检测提示）与「默认 DNS」（唯一必填组）放入带边框圆角、软底色的常显盒子（.fii-dns-box）；②其余 6 组（引导 DNS / DNS 分流策略 / 代理节点 DNS / 代理节点 DNS 策略 / 直连 DNS / Fallback DNS，按常用度排序）收进可折叠分组（.fii-dns-group = details/summary，默认收起，summary 显示组名 + 灰色短标签如「解析 DoH 服务器地址 · 只能纯 IP」，展开箭头 ▸/▾）；组内 legend 由 CSS 隐藏（标题上移到 summary，控件与 read() 不变）。页面从约 7 个无边界堆叠的 fieldset 变为 2 个盒子 + 6 条紧凑折叠条。
- CSS 新增 .fii-dns-box/.fii-dns-group/.fii-dns-tag（style.css 追加；fiiStyleHash 由 build_ipk.py 自动同步为内容哈希，本次 020b307a0d242b1e）。保存/差量逻辑不变（控件仍在 DOM，details 收起不影响 read()）。
- Python 249 项通过、node --check 通过；Playwright：6 组默认全收起、标签正确、展开后 legend 隐藏内容可见、保存按钮可达；截图经图像分析核验（外层卡片+内层盒子+折叠条层级清晰、无重叠溢出）。VM 已升级 0.1.1-51，服务运行正常，/www 下 app.js 含分组代码。

### 2026-10-23 追加：嗅探卡同款分组处理（0.1.1-52）

- 用户要求嗅探卡的 HTTP/TLS/QUIC 端口与跳过域名同样处理。CSS 类名由 DNS 专用改为通用（.fii-dns-box/.fii-dns-group/.fii-dns-tag → .fii-form-box/.fii-form-group/.fii-form-tag），helper 改名 formBox/formGroup 并改为接受节点参数。legend 隐藏规则改为 `fieldset:only-of-type>legend`（单 fieldset 组标题并入 summary；多 fieldset 组内各列表标题保留），新增 `.fii-selection+.fii-selection` 顶部分隔线（端口三列表竖排边界）。
- 嗅探卡现为：4 个开关网格 + 折叠组「嗅探端口」（HTTP/TLS/QUIC 三个列表竖排、分隔线、各自标题可见，标签「HTTP / TLS / QUIC 按协议嗅探的端口范围」）+ 折叠组「跳过域名」（标签「这些域名不做嗅探，直接按原目标连接」）；两组默认收起。DNS 卡同步换用新类名，行为不变。
- Python 249 项通过、node --check 通过；Playwright：嗅探卡 2 组默认收起、端口组展开后三列表标题显示且分隔线生效、跳过域名组 legend 隐藏、DNS 卡 8 组无回归（旧类名零残留）；截图 build/sniffer-grouped.png。VM 已升级 0.1.1-52，服务运行正常。

### 2026-10-23 追加：use-hosts 与静态解析表（0.1.1-53）

- 用户指出 `dns.use-hosts` 未暴露。补齐：设置键 `dnsUseHosts`（默认关，mihomo 官方默认一致）与 `dnsHosts`（域名 → IP 列表 map，≤64 项、每域 1–8 个纯 IP，ipaddress 严格校验）。UI：DNS 基础卡新增「使用静态解析表」开关（与「使用系统 Hosts」并列，帮助文案区分两者）；DNS 服务器卡新增折叠组「静态解析 Hosts」（域名 → IP，支持通配与 geosite: 匹配；dnsPolicyEditor 参数化出 itemLabel/itemPlaceholder/itemHint 复用）。
- 注入：`dns.use-hosts` 走 seed_dns 常规字段；`hosts` 是 mihomo 顶层键，overlay 在 dns 处理后 `root.setdefault('hosts', …)`（非空才写，覆写显式接管优先，非 dict 覆写直接报「hosts 覆写必须是对象」）。
- Python 249 项通过（默认无 hosts 键、tuned 写入与覆写优先、4 组非法值：非 dict/域名含空白/值非列表/值非 IP 含整数 223）；真实内核 v1.19.32 `-t` 通过（含 *.corp 双栈值与 geosite:internal）。VM：save dnsUseHosts+dnsHosts 后 apply，`/tmp/fii/config.yaml` 顶层 hosts 表与 dns.use-hosts: true 均生效，服务 running。

### 2026-10-23 追加：安装后 luci.fii 未注册修复（0.1.1-54）

- 用户真机（192.168.100.1）装 0.1.1-53 后 LuCI 报 `luci.fii/status Object not found`；VM 同包全新安装正常——差别在 rpcd 重载行为。此前仅 luci-app-fii postinst 有 `/etc/init.d/rpcd reload`，部分固件的 reload 不会重新加载 ucode 插件/ACL（rpcd 仅启动时读取），导致装完后 `luci.fii` 未注册。
- 修复：两个包的 postinst 统一为「保会话优先的探测回退」——`/etc/init.d/rpcd reload` 后用 `ubus -S list luci.fii` 探测，仍缺才 `/etc/init.d/rpcd restart`（无条件 restart 会杀所有 LuCI 会话，verify_packages.py 原有断言禁止；断言更新为「restart 只能出现在带 ubus 探测的回退里」）。fii 源码包 Makefile postinst 与 build_ipk.py 生成的两份 postinst 同步。
- VM 验证：卸载后全新安装 0.1.1-54（全程不手动碰 rpcd）→ luci.fii 立即注册、status 正常、服务自动恢复。装 53 的存量机器执行 `/etc/init.d/rpcd restart` 即可恢复；若 restart 后仍缺对象，属该固件 ucode 加载 rpc.uc 失败，需 logread + `ucode -e 'print(loadfile(...))'` 定位。

### 2026-10-23 追加：跟随运营商 DNS 的覆写优先修复（0.1.1-55）

- 用户真机场景：覆写 `dns!` 整块替换、nameserver 两条恰好与 DNS 页「默认 DNS」相同，开启跟随后最终输出 6 条（运营商 4 + 覆写 2）。根因：注入原在 overlay（覆写后）执行，用「nameserver == 设置列表」判定是否被覆写接管——覆写值恰好等于设置列表时无法区分，误注入。
- 语义修复：**跟随运营商 DNS 归入默认值层，移到 `render()` 的覆写执行之前合并**（seed 后、overrides 循环前）——覆写显式配置 nameserver（含 `dns!` 整块替换、`+` 追加）自然获胜；无覆写时照常合并；「未检测到上游」warning 改挂 render 的 warnings。overlay 中的注入块删除（hosts 注入保留）。
- 附带修复：postinst 的 rpcd 探测在 reload 后立即执行，而对象注册有秒级延迟——几乎必然误判走 restart（丢 LuCI 会话）。reload 与探测之间加 `sleep 2`（两包 postinst + Makefile 同步；verify_packages 断言不变）。
- Python 249 项通过（跟随用例重写为 render 路径：默认关/兜底/优先/去重/覆写显式获胜/dns! 整块替换获胜/空上游告警）。VM 实测 `dns!` + 跟随：最终 nameserver 恰为覆写 3 条、10.0.2.3 不混入；用户真机升级 55 + 重新 apply：nameserver 恰为覆写 2 条（1.1.1.1/8.8.8.8#节点选择），运营商 4 条不再叠加，服务 transparent 正常。
- 运维注意：经代理链路 SSH 操作用户路由器时，`fii apply`/包升级会重启服务并造成链路瞬断——远程命令需 nohup 脱离会话执行、断后重连验证；paramiko exec_command 传 JSON 参数须用远端单引号包裹（双引号转义会被 shell 剥掉）。
