# FII 0.1.1 validation

Target requested by the user: **OpenWrt 24.10.8 / Intel J4125 / x86_64**.

## Router-level validation (iStoreOS 24.10.8 VM, 2026-10-19)

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

- `python -m unittest discover -s tests -v`: **137 tests**. Covers source order, regex filtering before prefix, collision-free names, global / selected override ordering, YAML prepend / append / replacement semantics, rule insertion, quota exhaustion / recovery, malformed or unsafe YAML, share-link conversion, revision conflicts, refresh cache retention, concurrent edits, job dispatch, transactional activation and rollback, generated firewall scope / cleanup, rpc-call stdin parsing, legacy router-setting defaults, device/UID validation, access-control generation, UDP/DNS options, network-choice discovery, bounded backup transport, lost-acknowledgement retries, negotiated download chunks, JavaScript overrides, per-item URL diagnostics and GitHub token authentication/redirects, compact reads, partial saves, concurrent validation/commit handling, reusable checked imports with expiry/cancellation/idempotent retries, Flutter sorting reconstruction, exact reorder patches, synchronized editor snapshots and preview execution/progress reporting.
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
