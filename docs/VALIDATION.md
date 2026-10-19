# FII 0.1.1 validation

Target requested by the user: **OpenWrt 24.10.8 / Intel J4125 / x86_64**.

## Router-level validation (iStoreOS 24.10.8 VM, 2026-10-19)

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

- `python -m unittest discover -s tests -v`: **103 tests**. Covers source order, regex filtering before prefix, collision-free names, global / selected override ordering, YAML prepend / append / replacement semantics, rule insertion, quota exhaustion / recovery, malformed or unsafe YAML, share-link conversion, revision conflicts, refresh cache retention, concurrent edits, job dispatch, transactional activation and rollback, generated firewall scope / cleanup, rpc-call stdin parsing, legacy router-setting defaults, device/UID validation, access-control generation, UDP/DNS options, network-choice discovery, bounded backup transport, lost-acknowledgement retries, negotiated download chunks and JavaScript overrides.
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
