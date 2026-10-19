include $(TOPDIR)/rules.mk

PKG_NAME:=fii
PKG_VERSION:=0.1.1
PKG_RELEASE:=8
PKG_LICENSE:=GPL-3.0-or-later
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/fii
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Simple Mihomo configuration manager for OpenWrt
  DEPENDS:=+python3 +python3-yaml +ca-bundle +firewall4 +ip-full +kmod-nft-tproxy +kmod-nft-socket +kmod-tun +kmod-dummy
endef

define Package/fii/description
  Subscription sources, YAML/rule overrides and output profiles with procd supervised Mihomo.
endef

define Build/Compile
endef

define Package/fii/install
	$(CP) ./files/* $(1)/
	chmod 0755 $(1)/etc/init.d/fii $(1)/etc/uci-defaults/90-fii $(1)/usr/bin/fii
	chmod 0755 $(1)/usr/libexec/rpcd/luci.fii $(1)/usr/libexec/fii/*
	find $(1)/usr/lib/fii -name '__pycache__' -type d -exec rm -rf {} +
endef

define Package/fii/prerm
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	/etc/init.d/fii stop
	/etc/init.d/fii disable
	sed -i '/# fii-refresh$$$$/d' /etc/crontabs/root
	/etc/init.d/cron reload
fi
exit 0
endef

define Package/fii/postrm
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	uci -q delete firewall.fii
	uci commit firewall
	/etc/init.d/rpcd restart
fi
exit 0
endef

$(eval $(call BuildPackage,fii))
