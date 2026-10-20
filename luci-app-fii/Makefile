include $(TOPDIR)/rules.mk

LUCI_TITLE:=FII - Simple Mihomo Manager
LUCI_DEPENDS:=+fii +rpcd-mod-file +cgi-io
LUCI_PKGARCH:=all
PKG_VERSION:=0.1.1
PKG_RELEASE:=39
PKG_LICENSE:=GPL-3.0-or-later

define Package/luci-app-fii/postinst
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
	rm -rf /tmp/luci-modulecache/
	/etc/init.d/rpcd reload
fi
exit 0
endef

include $(TOPDIR)/feeds/luci/luci.mk

# LuCI's build rules install htdocs/ and root/ automatically.
