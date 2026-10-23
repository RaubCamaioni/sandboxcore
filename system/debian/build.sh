#!/bin/sh
# Build dist/sandboxcore-host_<version>_all.deb (needs podman; dpkg-deb runs in a Debian container).
set -e
cd "$(dirname "$0")/../.."
version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
mkdir -p dist
podman run --rm -v "$PWD":/src:ro -v "$PWD/dist":/out docker.io/library/debian:bookworm sh -c "
  set -e
  cp -r /src/system/debian /tmp/pkg && cd /tmp/pkg
  cp -r DEBIAN root/DEBIAN && mkdir -p root/usr/bin
  sed -i 's/^Version: .*/Version: $version/' root/DEBIAN/control
  { echo '#!/usr/bin/python3'; cat /src/src/sandboxcore/doctor.py; } > root/usr/bin/sandboxcore-doctor
  chmod 755 root/usr/bin/sandboxcore-doctor root/usr/sbin/sandboxcore-adduser root/DEBIAN/postinst root/DEBIAN/postrm
  dpkg-deb --build --root-owner-group root /out/sandboxcore-host_${version}_all.deb
"
