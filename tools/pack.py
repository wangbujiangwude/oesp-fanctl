#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OESP风扇助手 FPK 打包脚本（本机 Windows 侧使用）

流程：
  1) 从 build/app 重新打 app.tgz（顶层即应用文件，与 fnOS 约定一致）
  2) 计算 app.tgz 的 md5，写回 build/manifest 的 checksum
  3) 组装 build/oesp-fanctl_<version>_arm.fpk（gzip tar）

用法：
  python pack.py             # 使用 manifest 中的 version
  python pack.py 1.14        # 指定版本（同时写回 manifest）
"""
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import time

BUILD = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BUILD, "build")
APP = os.path.join(ROOT, "app")
MANIFEST = os.path.join(ROOT, "manifest")
APP_TGZ = os.path.join(ROOT, "app.tgz")

EXEC = {
    "oesp-fanctl", "fanweb.py", "fanlog",
    "cmd/main", "cmd/install_callback", "cmd/uninstall_callback",
    "cmd/upgrade_callback", "cmd/config_callback", "cmd/config_init",
    "cmd/install_init", "cmd/uninstall_init", "cmd/upgrade_init",
    "wizard/uninstall",
}


def mode_for(rel, isdir):
    if isdir:
        return 0o755
    return 0o755 if rel.replace("\\", "/") in EXEC else 0o644


EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
EXCLUDE_EXT = {".pyc", ".pyo", ".tmp", ".bak", ".swp", ".orig"}


def _skip(rel):
    parts = rel.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if os.path.splitext(parts[-1])[1].lower() in EXCLUDE_EXT:
        return True
    return parts[-1].startswith(".")


def add_tree(tf, base, prefix=""):
    """把 base 目录下的内容按排序加入 tar（相对路径，无前导 ./）"""
    entries = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for d in dirs:
            full = os.path.join(root, d)
            rel = os.path.relpath(full, base)
            if not _skip(rel):
                entries.append((rel, True))
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            if not _skip(rel):
                entries.append((rel, False))
    entries.sort(key=lambda x: (x[0].count(os.sep), x[0]))
    for rel, isdir in entries:
        name = os.path.join(prefix, rel) if prefix else rel
        name = name.replace("\\", "/")
        ti = tarfile.TarInfo(name)
        ti.mtime = int(time.time())
        ti.mode = mode_for(name, isdir)
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        if isdir:
            ti.type = tarfile.DIRTYPE
            tf.addfile(ti)
        else:
            full = os.path.join(base, rel)
            data = open(full, "rb").read()
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))


def read_version():
    for line in open(MANIFEST, encoding="utf-8"):
        m = re.match(r"^version\s*=\s*(\S+)", line)
        if m:
            return m.group(1)
    raise SystemExit("manifest 中找不到 version")


def set_version(v):
    txt = open(MANIFEST, encoding="utf-8").read()
    txt = re.sub(r"^version\s*=\s*\S+", "version               = " + v, txt, count=1, flags=re.M)
    open(MANIFEST, "w", encoding="utf-8", newline="\n").write(txt)


def set_checksum(md5):
    txt = open(MANIFEST, encoding="utf-8").read()
    if re.search(r"^checksum\s*=", txt, flags=re.M):
        txt = re.sub(r"^checksum\s*=\s*\S*", "checksum              = " + md5, txt, count=1, flags=re.M)
    else:
        txt = txt.rstrip("\n") + "\nchecksum              = %s\n" % md5
    open(MANIFEST, "w", encoding="utf-8", newline="\n").write(txt)


def main():
    if len(sys.argv) > 1:
        set_version(sys.argv[1])
    ver = read_version()

    # 1) app.tgz
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        add_tree(tf, APP)
    raw = buf.getvalue()
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(APP_TGZ, "wb"), mtime=0) as gz:
        gz.write(raw)
    md5 = hashlib.md5(open(APP_TGZ, "rb").read()).hexdigest()
    set_checksum(md5)
    print("app.tgz  %d bytes  md5=%s" % (os.path.getsize(APP_TGZ), md5))

    # 2) fpk
    fpk = os.path.join(ROOT, "oesp-fanctl_%s_arm.fpk" % ver)
    if os.path.exists(fpk):
        os.remove(fpk)
    with tarfile.open(fpk, "w:gz") as tf:
        # app.tgz 必须最先
        ti = tarfile.TarInfo("app.tgz")
        data = open(APP_TGZ, "rb").read()
        ti.size = len(data)
        ti.mode = 0o644
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        ti.mtime = int(time.time())
        tf.addfile(ti, io.BytesIO(data))
        for item in ("LICENSE", "cmd", "config", "ICON.PNG", "ICON_256.PNG", "manifest", "wizard"):
            p = os.path.join(ROOT, item)
            if os.path.isdir(p):
                add_tree(tf, p, prefix=item)
            else:
                ti = tarfile.TarInfo(item)
                data = open(p, "rb").read()
                ti.size = len(data)
                ti.mode = mode_for(item, False)
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                ti.mtime = int(time.time())
                tf.addfile(ti, io.BytesIO(data))
    print("FPK      %s  %d bytes" % (fpk, os.path.getsize(fpk)))
    print("version  %s" % ver)


if __name__ == "__main__":
    main()
