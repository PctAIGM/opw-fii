# FII

OpenWrt 上的 Mihomo 配置管理工具，围绕 **订阅源 → 覆写 → 输出配置** 组织日常使用。

面向 OpenWrt 24.10 / x86_64（如 Intel J4125），当前版本 **0.1.1-43**，处于试用阶段。安装包仅包含应用，Mihomo 内核在页面中下载，支持 Release / Alpha 渠道。

## 功能

- **订阅管理**：URL、本地 YAML、分享链接，支持节点筛选与自动刷新。
- **配置覆写**：YAML / RULES / JavaScript，支持全局与输出专属覆写。
- **输出配置**：聚合订阅、预览、下载、校验并更新运行配置。
- **控制面板**：代理组切换，GEO 数据与 ruleset 查看、更新。
- **路由器接管**：REDIRECT / TPROXY / TUN、IPv4 / IPv6、DNS 与访问控制。
- **日常维护**：内核管理、日志筛选、备份与迁移，支持手机和深色模式。

## 安装与使用

推送代码或创建 PR 后，GitHub Actions 会自动构建；在 [Build IPK](https://github.com/PctAIGM/opw-fii/actions/workflows/build-ipk.yml) 的成功运行页面下载 Artifacts，也可手动触发。

将构建得到的两个 IPK 上传至路由器 `/tmp/`，执行：

```sh
opkg update
opkg install /tmp/fii_0.1.1-43_all.ipk /tmp/luci-app-fii_0.1.1-43_all.ipk
```

打开 **LuCI → 服务 → FII**：

1. 在 **Mihomo** 页面下载内核。
2. 添加订阅和覆写，创建输出配置。
3. 设置路由器接管方式，更新运行配置，再启动服务。

使用前请停止其他透明代理插件的接管。更新运行配置会保留服务原有的运行或停止状态。

## 本地开发

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/build_ipk.py
python tools/verify_packages.py
```

安装包生成在 `dist/`。运行 `python tools/preview.py`，可在 `http://127.0.0.1:8765` 预览界面。

## 文档

- [使用与开发指南](docs/GUIDE.md)：配置语义、迁移、SDK 构建、排障与卸载。
- [验证记录](docs/VALIDATION.md)：测试结果与路由器验收范围。
- [配置示例](examples/) · [项目来源与许可说明](NOTICE.md)

GPL-3.0-or-later
