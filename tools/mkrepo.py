#!/usr/bin/env python3
"""mkrepo.py - builds the Cydia (APT) repository index and the depiction pages.

Standard library only, so it runs on the Raspberry Pi as it is:

  python3 mkrepo.py --src /home/samuel/cydia-repo/site \
                    --out /home/samuel/nginx/html/repo \
                    --base-url http://samcejko.eu/repo [--keep 2 --prune]

Input  (--src): data.json, templates/, style.css, CydiaIcon.png, icons/, screenshots/
Debs:          <out>/debs/*.deb (uploaded there by tools/repo-publish.ps1); a package may come in two architectures,
               iphoneos-arm (iOS 6, Cydia) and iphoneos-arm64 (rootless jailbreaks, Sileo/Zebra), which share
               the version, the depiction and the icon - APT picks the one for the device
Output (--out): Packages, Packages.gz, Packages.bz2, Release, index.html, index.cs.html,
               depictions/<package>/index.html + index.cs.html (web depictions: Cydia, Installer, Zebra),
               sileo/<package>.json + <package>.cs.json (native depictions: Sileo, Zebra), sileo-featured.json,
               feedback/index.html + index.cs.html (the bug/idea form, lists every published app; it posts to
               feedback/api/, which nginx hands to feedback/feedback_server.py),
               icons/, banners/, screenshots/, style.css, CydiaIcon.png
"""
import argparse
import bz2
import gzip
import hashlib
import html
import io
import json
import lzma
import os
import re
import shutil
import sys
import tarfile
from datetime import datetime
from urllib.parse import quote

LANGS = ("en", "cs")

# What a build of each architecture needs (the depictions list the architectures a package comes in)
ARCH_NAMES = {
    "iphoneos-arm": {"en": "iOS 6.x only, armv7 (32-bit)", "cs": "jen iOS 6.x, armv7 (32bit)"},
    "iphoneos-arm64": {"en": "iOS 15 or later with a rootless jailbreak (arm64)", "cs": "iOS 15 nebo novější s rootless jailbreakem (arm64)"},
}

STRINGS = {
    "en": {
        "features": "Features",
        "notes": "Good to know",
        "screens": "Screenshots (iPad, iOS 6)",
        "screens_phone": "Screenshots (iPhone, iOS 16)",
        "changes": "What's new in {v}",
        "info": "Package",
        "version": "Version",
        "size": "Size",
        "updated": "Updated",
        "requires": "Requires",
        "requires_v": "iOS 6.x only, armv7 (32-bit)",
        "source": "Source code on GitHub",
        "support": "Support the development",
        "other": "English",
        "switch": "Česky",
        "switch_href": "index.cs.html",
        "packages": "Apps in this repository",
        "add_title": "Add this repository",
        "add_steps": "Cydia: Sources → Edit → Add. Sileo, Zebra, Installer: Sources → + . Enter the address below, or tap the button of your package manager.",
        "add_cydia": "Add to Cydia",
        "add_sileo": "Add to Sileo",
        "add_zebra": "Add to Zebra",
        "add_installer": "Add to Installer",
        "about_repo": "About",
        "details": "Details",
        "changelog": "Changelog",
        "latest": "latest",
        "trollstore": "Install with TrollStore (iOS 14–17, no jailbreak)",
        "ipa": "Download the IPA for TrollStore",
        "trollstore_note": "No jailbreak? With TrollStore on iOS 14–17 open an app below and tap \"Install with TrollStore\".",
        # the feedback form (feedback/) and the links to it
        "feedback_title": "Bugs and ideas",
        "feedback": "Report a bug or send an idea",
        "feedback_newapp": "Request a new app",
        "fb_title": "Feedback",
        "fb_tagline": "Bugs, ideas and app requests",
        "fb_intro": "Something does not work, or you have an idea? Pick the app and write it down, it goes straight to me. Requests for new apps are welcome too. English or Czech, as you like.",
        "fb_what": "What is it about",
        "fb_type": "Type",
        "fb_type_bug": "Bug",
        "fb_type_idea": "Idea / improvement",
        "fb_type_newapp": "New app request",
        "fb_type_other": "Something else",
        "fb_app": "App",
        "fb_app_other": "Other / not in the list",
        "fb_app_name": "Which app or service",
        "fb_device_title": "Your device",
        "fb_device": "Device",
        "fb_device_other_opt": "Other",
        "fb_device_other": "Device model",
        "fb_ios": "iOS version",
        "fb_app_version": "App version",
        "fb_installed": "Installed with",
        "fb_installed_ipa": "IPA / other",
        "fb_desc_title": "Description",
        "fb_text_hint_bug": "What happens, what you did before it and what you expected. The more detail the better.",
        "fb_text_hint_idea": "What you would like the app to do, and why.",
        "fb_text_hint_newapp": "Which app or service you miss on iOS 6 and what it should do.",
        "fb_text_hint_other": "Your message.",
        "fb_proposal": "How would you solve it? (optional)",
        "fb_proposal_hint": "If you have an idea how it should work or look, write it here.",
        "fb_contact_title": "Contact (optional)",
        "fb_name": "Name or nickname",
        "fb_contact": "E-mail or Discord",
        "fb_send": "Send",
        "fb_sending": "Sending…",
        "fb_need_text": "Please write a description first.",
        "fb_need_app": "Please pick the app.",
        "fb_foot": "Reports are stored on my server, no account needed. Thanks for helping!",
        "fb_back": "Back to the repository",
        "fb_ph_optional": "optional",
        "fb_ph_app_other": "e.g. Reddit",
        "fb_ph_device_other": "e.g. iPhone 5c",
    },
    "cs": {
        "features": "Funkce",
        "notes": "Dobré vědět",
        "screens": "Snímky obrazovky (iPad, iOS 6)",
        "screens_phone": "Snímky obrazovky (iPhone, iOS 16)",
        "changes": "Co je nové ve verzi {v}",
        "info": "Balíček",
        "version": "Verze",
        "size": "Velikost",
        "updated": "Aktualizováno",
        "requires": "Vyžaduje",
        "requires_v": "jen iOS 6.x, armv7 (32bit)",
        "source": "Zdrojový kód na GitHubu",
        "support": "Podpořit vývoj",
        "other": "Česky",
        "switch": "English",
        "switch_href": "index.html",
        "packages": "Aplikace v tomto repozitáři",
        "add_title": "Přidat tento repozitář",
        "add_steps": "Cydia: Sources (Zdroje) → Edit → Add. Sileo, Zebra, Installer: Sources → + . Zadej adresu níže, nebo klepni na tlačítko svého správce balíčků.",
        "add_cydia": "Přidat do Cydie",
        "add_sileo": "Přidat do Sileo",
        "add_zebra": "Přidat do Zebry",
        "add_installer": "Přidat do Installeru",
        "about_repo": "O repozitáři",
        "details": "Podrobnosti",
        "changelog": "Změny",
        "latest": "nejnovější",
        "trollstore": "Nainstalovat přes TrollStore (iOS 14–17, bez jailbreaku)",
        "ipa": "Stáhnout IPA pro TrollStore",
        "trollstore_note": "Bez jailbreaku? S TrollStore na iOS 14–17 otevři appku níže a klepni na „Nainstalovat přes TrollStore“.",
        # the feedback form (feedback/) and the links to it
        "feedback_title": "Chyby a nápady",
        "feedback": "Nahlásit chybu nebo poslat nápad",
        "feedback_newapp": "Navrhnout novou aplikaci",
        "fb_title": "Zpětná vazba",
        "fb_tagline": "Chyby, nápady a návrhy na aplikace",
        "fb_intro": "Něco nefunguje, nebo máš nápad? Vyber aplikaci a napiš mi to, jde to rovnou ke mně. Návrhy na nové aplikace jsou taky vítané. Česky nebo anglicky, jak chceš.",
        "fb_what": "O co jde",
        "fb_type": "Typ",
        "fb_type_bug": "Chyba",
        "fb_type_idea": "Nápad na vylepšení",
        "fb_type_newapp": "Návrh nové aplikace",
        "fb_type_other": "Něco jiného",
        "fb_app": "Aplikace",
        "fb_app_other": "Jiná / není v seznamu",
        "fb_app_name": "Jaká aplikace nebo služba",
        "fb_device_title": "Tvoje zařízení",
        "fb_device": "Zařízení",
        "fb_device_other_opt": "Jiné",
        "fb_device_other": "Model zařízení",
        "fb_ios": "Verze iOS",
        "fb_app_version": "Verze aplikace",
        "fb_installed": "Nainstalováno přes",
        "fb_installed_ipa": "IPA / jinak",
        "fb_desc_title": "Popis",
        "fb_text_hint_bug": "Co se děje, co jsi udělal předtím a co jsi čekal. Čím víc detailů, tím líp.",
        "fb_text_hint_idea": "Co by měla aplikace umět a proč.",
        "fb_text_hint_newapp": "Jaká aplikace nebo služba ti na iOS 6 chybí a co by měla umět.",
        "fb_text_hint_other": "Tvoje zpráva.",
        "fb_proposal": "Jak bys to řešil ty? (nepovinné)",
        "fb_proposal_hint": "Když máš představu, jak by to mělo fungovat nebo vypadat, napiš ji sem.",
        "fb_contact_title": "Kontakt (nepovinné)",
        "fb_name": "Jméno nebo přezdívka",
        "fb_contact": "E-mail nebo Discord",
        "fb_send": "Odeslat",
        "fb_sending": "Odesílám…",
        "fb_need_text": "Napiš prosím nejdřív popis.",
        "fb_need_app": "Vyber prosím aplikaci.",
        "fb_foot": "Hlášení se ukládají na můj server, bez účtu. Díky za pomoc!",
        "fb_back": "Zpět na repozitář",
        "fb_ph_optional": "nepovinné",
        "fb_ph_app_other": "např. Reddit",
        "fb_ph_device_other": "např. iPhone 5c",
    },
}

# The device picker of the feedback form: value (stored as it is), label per language when it differs
DEVICES = [
    ("iPhone 3GS", None), ("iPhone 4", None), ("iPhone 4S", None), ("iPhone 5", None),
    ("iPad 2", None), ("iPad 3", None), ("iPad 4", None), ("iPad mini", None),
    ("iPod touch 4", None), ("iPod touch 5", None),
    ("iPhone 6s-8 / SE", None), ("iPhone X-11", None),
    ("iPhone 12+", {"en": "iPhone 12 or newer", "cs": "iPhone 12 nebo novější"}),
    ("iPad 64-bit", {"en": "iPad (64-bit, iOS 15+)", "cs": "iPad (64bit, iOS 15+)"}),
]
INSTALLERS = [("cydia", "Cydia"), ("sileo", "Sileo"), ("zebra", "Zebra"), ("installer", "Installer"), ("trollstore", "TrollStore")]


def feedback_href(lang, pkg=None, query=None):
    """The feedback form in the page's language, with the app (or another query) preselected."""
    page = "index.html" if lang == "en" else "index.%s.html" % lang
    q = ("?app=" + quote(pkg, safe="")) if pkg else (("?" + query) if query else "")
    return page + q


def trollstore_urls(meta, by_pkg, pkg):
    """The arm64 IPA of the newest arm64 build on GitHub, and the TrollStore link that installs it."""
    items = by_pkg.get((pkg, "iphoneos-arm64"))
    if not items or not meta.get("ipa") or not meta.get("github"):
        return None, None
    version = items[0]["info"]["Version"]
    ipa = "%s/releases/download/v%s/%s-%s-arm64.ipa" % (meta["github"].rstrip("/"), version, meta["ipa"], version)
    return ipa, "apple-magnifier://install?url=" + quote(ipa, safe="")


def sileo_depiction(pkg, meta, d, lang, shots, base, support, repo, tint, banner_url, requires, ipa_url=None, phone_shots=()):
    """Sileo's native depiction (JSON); Zebra renders it too, Installer and Cydia use the web page."""
    s = STRINGS[lang]
    version = d["info"]["Version"] if d else "-"
    views = [
        {"class": "DepictionSubheaderView", "title": pick(meta.get("tagline"), lang), "useBoldText": True},
        {"class": "DepictionMarkdownView", "markdown": pick(meta.get("about"), lang), "useSpacing": True},
        {"class": "DepictionHeaderView", "title": s["features"]},
        {"class": "DepictionMarkdownView", "markdown": "\n".join("- " + i for i in pick_list(meta.get("features"), lang))},
    ]
    notes = pick_list(meta.get("notes"), lang)
    if notes:
        views.append({"class": "DepictionHeaderView", "title": s["notes"]})
        views.append({"class": "DepictionMarkdownView", "markdown": "\n".join("- " + i for i in notes)})
    if shots:
        views.append({"class": "DepictionHeaderView", "title": s["screens"]})
        views.append({
            "class": "DepictionScreenshotsView",
            "itemCornerRadius": 6,
            "itemSize": "{150, 200}" if meta.get("shots_portrait") else "{200, 150}",  # data.json: iPad pictures in portrait
            "screenshots": [{"url": "%s/screenshots/%s/%s" % (base, pkg, n), "accessibilityText": meta.get("name", pkg)} for n in shots],
        })
    if phone_shots:
        views.append({"class": "DepictionHeaderView", "title": s["screens_phone"]})
        views.append({
            "class": "DepictionScreenshotsView",
            "itemCornerRadius": 8,
            "itemSize": "{120, 260}",
            "screenshots": [{"url": "%s/screenshots/%s/iphone/%s" % (base, pkg, n), "accessibilityText": meta.get("name", pkg)} for n in phone_shots],
        })
    views.append({"class": "DepictionHeaderView", "title": s["info"]})
    views.append({"class": "DepictionTableTextView", "title": s["version"], "text": version})
    views.append({"class": "DepictionTableTextView", "title": s["size"], "text": human_size(d["size"]) if d else "-"})
    views.append({"class": "DepictionTableTextView", "title": s["updated"], "text": datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d") if d else "-"})
    views.append({"class": "DepictionTableTextView", "title": s["requires"], "text": requires})
    if meta.get("github"):
        views.append({"class": "DepictionTableButtonView", "title": s["source"], "action": meta["github"], "openExternal": True})
    if support:
        views.append({"class": "DepictionTableButtonView", "title": s["support"], "action": support, "openExternal": True})
    if ipa_url:
        views.append({"class": "DepictionTableButtonView", "title": s["ipa"], "action": ipa_url, "openExternal": True})
    views.append({"class": "DepictionTableButtonView", "title": s["feedback"], "action": "%s/feedback/%s" % (base, feedback_href(lang, pkg)), "openExternal": True})
    views.append({"class": "DepictionSpacerView", "spacing": 8})
    views.append({"class": "DepictionLabelView", "text": pick(repo.get("footer"), lang), "fontSize": 12, "textColor": "#8e8e93", "alignment": 1})
    tabs = [{"tabname": s["details"], "class": "DepictionStackView", "views": views}]
    changes = pick_list(meta.get("changes"), lang)
    if changes:
        tabs.append({"tabname": s["changelog"], "class": "DepictionStackView", "views": [
            {"class": "DepictionSubheaderView", "title": version, "useBoldText": True},
            {"class": "DepictionMarkdownView", "markdown": "\n".join("- " + i for i in changes)},
        ]})
    dep = {"minVersion": "0.1", "class": "DepictionTabView", "tabs": tabs}
    if banner_url:
        dep["headerImage"] = banner_url
    if tint:
        dep["tintColor"] = tint
    return dep


# ----------------------------------------------------------------------------- deb reading

def read_ar(path):
    """Yields (member name, bytes) of an ar archive (a .deb is one)."""
    with open(path, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            raise ValueError("not an ar archive: %s" % path)
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                break
            name = hdr[0:16].decode("ascii", "replace").strip()
            size = int(hdr[48:58].decode("ascii").strip())
            data = f.read(size)
            if size % 2:
                f.read(1)
            if name.endswith("/"):
                name = name[:-1]
            yield name, data


def open_tar(name, data):
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    elif name.endswith(".bz2"):
        data = bz2.decompress(data)
    elif name.endswith(".xz") or name.endswith(".lzma"):
        data = lzma.decompress(data)  # FORMAT_AUTO: .xz and legacy .lzma
    elif name.endswith(".zst"):
        raise ValueError("zstd-compressed deb members are not supported: %s" % name)
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:")


def parse_control(text):
    """Returns an ordered list of (field, value); continuation lines are kept in the value."""
    fields = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and fields:
            fields[-1] = (fields[-1][0], fields[-1][1] + "\n" + line)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields.append((key.strip(), value.strip()))
    return fields


def effective_depends(meta, repo, arch, own):
    """What a stanza's Depends says: the package's own override in data.json ("depends": a string for every
    architecture, or {arch: string}), else the repository's default for the architecture ("depends_by_arch", e.g.
    the 32-bit builds on iOS 6.x only), else what the deb itself declares.
    Architectures explicitly listed in depends_from_package preserve their original package
    dependencies instead. This keeps older releases' limits when new releases widen support."""
    preserve = (meta or {}).get("depends_from_package", [])
    if not isinstance(preserve, list) or not all(isinstance(value, str) for value in preserve):
        raise ValueError("depends_from_package must be a list of architecture names")
    if arch in preserve:
        if not own:
            raise ValueError("Cannot preserve missing package dependencies")
        return own
    dep = (meta or {}).get("depends")
    if isinstance(dep, dict):
        dep = dep.get(arch)
    if dep:
        return dep
    return (repo.get("depends_by_arch") or {}).get(arch) or own


def _set_control_depends(text, value):
    """The control text with its Depends field (continuation lines included) replaced, or added."""
    out, replaced, skipping = [], False, False
    for line in text.split("\n"):
        if skipping and line[:1] in (" ", "\t"):
            continue
        skipping = False
        if line.lower().startswith("depends:"):
            out.append("Depends: " + value)
            replaced, skipping = True, True
            continue
        out.append(line)
    if not replaced:
        while out and out[-1] == "":
            out.pop()
        out.append("Depends: " + value)
        out.append("")
    result = "\n".join(out)
    return result if result.endswith("\n") else result + "\n"


def repack_deb_depends(path, value):
    """Writes a copy of a deb whose control file says `Depends: value` (everything else, the app included,
    byte for byte the same) and returns its path. Needed whenever the index narrows a deb's Depends: APT tells
    versions apart by a hash that covers Depends, so an installed deb whose own Depends differs from the index's
    would be offered as an "upgrade" to the very same version forever (Cydia, 2026-10-15). The copy gets a new
    name (Cloudflare caches debs by name) and is deterministic, so a second run writes the identical file."""
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive: %s" % path)
    members, pos = [], 8
    while pos + 60 <= len(blob):
        hdr = blob[pos:pos + 60]
        size = int(hdr[48:58].decode("ascii").strip())
        data = blob[pos + 60:pos + 60 + size]
        members.append((hdr, data))
        pos += 60 + size + (size % 2)
    out = bytearray(b"!<arch>\n")
    for hdr, data in members:
        name = hdr[0:16].decode("ascii", "replace").strip().rstrip("/")
        if name.startswith("control.tar"):
            with open_tar(name, data) as tar:
                items = []
                for member in tar.getmembers():
                    content = tar.extractfile(member).read() if member.isfile() else None
                    if member.isfile() and os.path.basename(member.name) == "control":
                        content = _set_control_depends(content.decode("utf-8"), value).encode("utf-8")
                        member.size = len(content)
                    items.append((member, content))
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
                for member, content in items:
                    tar.addfile(member, io.BytesIO(content) if content is not None else None)
            raw = buf.getvalue()
            if name.endswith(".gz"):
                data = gzip.compress(raw, compresslevel=9, mtime=0)
            elif name.endswith(".xz"):
                data = lzma.compress(raw, format=lzma.FORMAT_XZ)
            elif name.endswith(".lzma"):
                data = lzma.compress(raw, format=lzma.FORMAT_ALONE)
            elif name.endswith(".bz2"):
                data = bz2.compress(raw)
            else:
                data = raw
            hdr = hdr[:48] + ("%-10d" % len(data)).encode("ascii") + hdr[58:]
        out += hdr + data
        if len(data) % 2:
            out += b"\n"
    stem = os.path.basename(path)[:-4]
    stem = re.sub(r"-d[0-9a-f]{6}$", "", stem)
    stem = re.sub(r"-r[0-9a-f]{64}$", "", stem)
    # A rebuild of the same version can contain different bytes. Never reuse
    # a published URL based only on Depends: caches would serve the old deb.
    new_path = os.path.join(os.path.dirname(path), "%s-r%s.deb" % (stem, hashlib.sha256(out).hexdigest()))
    with open(new_path, "wb") as f:
        f.write(bytes(out))
    return new_path


def read_deb(path):
    control = None
    for name, data in read_ar(path):
        if name.startswith("control.tar"):
            with open_tar(name, data) as tar:
                for member in tar.getmembers():
                    if os.path.basename(member.name) == "control":
                        control = tar.extractfile(member).read().decode("utf-8", "replace")
                        break
    if control is None:
        raise ValueError("no control file in %s" % path)
    fields = parse_control(control)
    info = dict(fields)
    if "Package" not in info or "Version" not in info:
        raise ValueError("control of %s lacks Package/Version" % path)
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    st = os.stat(path)
    return {
        "path": path,
        "file": os.path.basename(path),
        "fields": fields,
        "info": info,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
    }


# ----------------------------------------------------------------------------- dpkg version order

def _order(c):
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a, b):
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) else 0
            bc = _order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(v):
    epoch = 0
    if ":" in v:
        e, v = v.split(":", 1)
        epoch = int(e) if e.isdigit() else 0
    if "-" in v:
        upstream, revision = v.rsplit("-", 1)
    else:
        upstream, revision = v, ""
    return epoch, upstream, revision


def version_cmp(a, b):
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return ea - eb
    c = _verrevcmp(ua, ub)
    if c:
        return c
    return _verrevcmp(ra, rb)


class _VersionKey:
    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return version_cmp(self.v, other.v) < 0


# ----------------------------------------------------------------------------- helpers

def human_size(n):
    if n < 1024 * 1024:
        return "%d KB" % max(1, round(n / 1024))
    return "%.1f MB" % (n / (1024 * 1024))


def esc(s):
    return html.escape(str(s), quote=True)


def pick(value, lang):
    """A string, or {"en": ..., "cs": ...} with English as the fallback."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("en") or ""
    return value or ""


def pick_list(value, lang):
    if isinstance(value, dict):
        return value.get(lang) or value.get("en") or []
    return value or []


def render_list(items):
    return "\n".join("<li>%s</li>" % esc(i) for i in items)


def render(template, values):
    out = template
    for key, value in values.items():
        out = out.replace("{{%s}}" % key, value)
    return out


def section(template, name, keep):
    """Keeps or removes a {{#name}} ... {{/name}} block."""
    start, end = "{{#%s}}" % name, "{{/%s}}" % name
    while start in template:
        a = template.index(start)
        b = template.index(end, a) + len(end)
        inner = template[a + len(start):b - len(end)]
        template = template[:a] + (inner if keep else "") + template[b:]
    return template


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="site sources: data.json, templates/, icons/, ...")
    ap.add_argument("--out", required=True, help="web root of the repository (contains debs/)")
    ap.add_argument("--base-url", default=None, help="public URL of the repository (default: data.json repo.base_url)")
    ap.add_argument("--keep", type=int, default=2, help="versions kept per package (default 2)")
    ap.add_argument("--prune", action="store_true", help="delete the debs of older versions beyond --keep")
    args = ap.parse_args()

    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    with open(os.path.join(src, "data.json"), encoding="utf-8") as f:
        data = json.load(f)
    repo = data["repo"]
    base = (args.base_url or repo["base_url"]).rstrip("/")
    packages_meta = data.get("packages", {})
    order = data.get("order") or list(packages_meta.keys())

    debs_dir = os.path.join(out, "debs")
    os.makedirs(debs_dir, exist_ok=True)
    debs = []
    for name in sorted(os.listdir(debs_dir)):
        if name.endswith(".deb"):
            try:
                debs.append(read_deb(os.path.join(debs_dir, name)))
            except Exception as e:  # a broken upload must not take the whole repo down
                print("skipping %s: %s" % (name, e), file=sys.stderr)

    # A deb whose Depends the index narrows (iOS 6 only...) is repacked so that the deb and the index agree, else
    # Cydia offers the installed version as an upgrade to itself forever (see repack_deb_depends). The original
    # goes to debs/original/; the repacked copy is the one listed.
    original_dir = os.path.join(debs_dir, "original")
    repacked = []
    for d in debs:
        arch = d["info"].get("Architecture", "iphoneos-arm")
        own = d["info"].get("Depends")
        want = effective_depends(packages_meta.get(d["info"]["Package"]), repo, arch, own)
        if want and want != own:
            try:
                new_path = repack_deb_depends(d["path"], want)
                d2 = read_deb(new_path)
                if d2["info"].get("Depends") != want:
                    raise ValueError("the repacked control says %r" % d2["info"].get("Depends"))
            except Exception as e:
                print("could not repack %s (listed as it is): %s" % (d["file"], e), file=sys.stderr)
                repacked.append(d)
                continue
            os.makedirs(original_dir, exist_ok=True)
            os.replace(d["path"], os.path.join(original_dir, d["file"]))
            print("repacked %s -> %s (Depends: %s)" % (d["file"], d2["file"], want))
            d = d2
        repacked.append(d)
    seen = set()
    debs = []
    for d in repacked:
        if d["path"] not in seen:
            seen.add(d["path"])
            # Repacking earlier in this pass may have replaced this path.
            # Always index the final bytes, not the initial directory snapshot.
            debs.append(read_deb(d["path"]))

    # newest first per package and architecture, prune the rest
    by_pkg = {}
    for d in debs:
        key = (d["info"]["Package"], d["info"].get("Architecture", "iphoneos-arm"))
        by_pkg.setdefault(key, []).append(d)
    kept = []
    for key, items in by_pkg.items():
        # A rebuild may keep the version number. Publish only the newest
        # bytes for that version; old immutable URLs remain downloadable.
        items.sort(key=lambda d: d["mtime"], reverse=True)
        versions = set()
        unique = []
        for d in items:
            version = d["info"]["Version"]
            if version not in versions:
                versions.add(version)
                unique.append(d)
        items[:] = unique
        items.sort(key=lambda d: _VersionKey(d["info"]["Version"]), reverse=True)
        for d in items[args.keep:]:
            if args.prune:
                os.remove(d["path"])
                print("pruned %s" % d["file"])
            else:
                print("older version not listed (use --prune to delete): %s" % d["file"])
        del items[args.keep:]
        kept.extend(items)
    # the newest deb per package (the iOS 6 build when there is one: the depictions show its version and size),
    # and the architectures each package comes in
    newest = {}
    archs = {}
    for (pkg, arch), items in sorted(by_pkg.items()):
        archs.setdefault(pkg, set()).add(arch)
        if pkg not in newest or arch == "iphoneos-arm":
            newest[pkg] = items[0]

    def depends_for(pkg, arch, own):
        return effective_depends(packages_meta.get(pkg), repo, arch, own)

    def requires_text(pkg, lang):
        meta = packages_meta.get(pkg, {})
        preserve = meta.get("depends_from_package", [])
        if preserve:
            names = []
            for arch in sorted(archs.get(pkg, ())):
                label = ARCH_NAMES.get(arch, {}).get(lang, arch)
                if arch in preserve:
                    own = by_pkg[(pkg, arch)][0]["info"].get("Depends", "")
                    lower = re.search(r"firmware\s*\(>=\s*([\d.]+)\)", own)
                    upper = re.search(r"firmware\s*\(<<\s*([\d.]+)\)", own)
                    if lower:
                        label = ("iOS %s nebo novější" if lang == "cs" else "iOS %s or later") % lower.group(1)
                        if upper:
                            label += (", před iOS %s" if lang == "cs" else ", below iOS %s") % upper.group(1)
                        bits = {"iphoneos-arm": 32, "iphoneos-arm64": 64}.get(arch)
                        if bits:
                            label += (" (%dbit)" if lang == "cs" else " (%d-bit)") % bits
                names.append(label)
            return "; ".join(names)
        custom = meta.get("requires")  # data.json: {"en": ..., "cs": ...} for a special case
        if custom:
            return custom.get(lang) or custom.get("en") or ""
        names = [ARCH_NAMES.get(a, {}).get(lang, a) for a in sorted(archs.get(pkg, ()))]
        return "; ".join(names) if names else STRINGS[lang]["requires_v"]

    # static files; the pages link style.css with a hash of its content, so that a cached copy (Cloudflare keeps
    # .css for hours, Safari too) is never used after a change
    css_v = ""
    for name in ("style.css", "CydiaIcon.png"):
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(out, name))
            if name == "style.css":
                with open(p, "rb") as f:
                    css_v = hashlib.md5(f.read()).hexdigest()[:8]
    for sub in ("icons", "screenshots", "banners"):
        sp = os.path.join(src, sub)
        if os.path.isdir(sp):
            dp = os.path.join(out, sub)
            if os.path.isdir(dp):
                shutil.rmtree(dp)
            shutil.copytree(sp, dp)

    def icon_url(pkg):
        meta = packages_meta.get(pkg, {})
        name = meta.get("icon") or (pkg + ".png")
        return base + "/icons/" + name if os.path.exists(os.path.join(src, "icons", name)) else None

    def banner_url(pkg):
        return base + "/banners/" + pkg + ".png" if os.path.exists(os.path.join(src, "banners", pkg + ".png")) else None

    tints = {}
    colors_path = os.path.join(src, "banners", "colors.json")
    if os.path.exists(colors_path):
        with open(colors_path, encoding="utf-8") as f:
            tints = json.load(f)

    # Packages
    skip_fields = {"Filename", "Size", "MD5sum", "SHA1", "SHA256", "Depiction", "SileoDepiction", "Icon"}
    stanzas = []
    for d in sorted(kept, key=lambda d: (order.index(d["info"]["Package"]) if d["info"]["Package"] in order else 999, d["file"])):
        pkg = d["info"]["Package"]
        meta = packages_meta.get(pkg, {})
        arch = d["info"].get("Architecture", "iphoneos-arm")
        lines = []
        for key, value in d["fields"]:
            if key in skip_fields:
                continue
            if key == "Homepage" and "github" in meta:
                if not meta["github"]:
                    continue  # e.g. a private repository: no public home page
                value = meta["github"]
            if key == "Depends":
                value = depends_for(pkg, arch, value)  # data.json narrows what the deb declares (e.g. iOS 6 only)
            lines.append("%s: %s" % (key, value))
        if "Homepage" not in d["info"] and meta.get("github"):
            lines.append("Homepage: %s" % meta["github"])
        if "Depends" not in d["info"]:
            dep = depends_for(pkg, arch, None)
            if dep:
                lines.append("Depends: %s" % dep)
        if pkg in packages_meta:
            lines.append("Depiction: %s/depictions/%s/" % (base, pkg))
            lines.append("SileoDepiction: %s/sileo/%s.json" % (base, pkg))
        icon = icon_url(pkg)
        if icon:
            lines.append("Icon: %s" % icon)
        lines.append("Filename: debs/%s" % d["file"])
        lines.append("Size: %d" % d["size"])
        lines.append("MD5sum: %s" % d["md5"])
        lines.append("SHA1: %s" % d["sha1"])
        lines.append("SHA256: %s" % d["sha256"])
        stanzas.append("\n".join(lines) + "\n")
    packages_text = "\n".join(stanzas)
    packages_bytes = packages_text.encode("utf-8")
    with open(os.path.join(out, "Packages"), "wb") as f:
        f.write(packages_bytes)
    with open(os.path.join(out, "Packages.gz"), "wb") as f:
        f.write(gzip.compress(packages_bytes, mtime=0))
    with open(os.path.join(out, "Packages.bz2"), "wb") as f:
        f.write(bz2.compress(packages_bytes))

    # Release
    release = [
        "Origin: %s" % repo.get("origin", repo["label"]),
        "Label: %s" % repo["label"],
        "Suite: stable",
        "Version: 1.0",
        "Codename: ios",
        "Architectures: iphoneos-arm iphoneos-arm64",
        "Components: main",
        "Description: %s" % pick(repo.get("description"), "en"),
    ]
    for header, algo in (("MD5Sum", hashlib.md5), ("SHA1", hashlib.sha1), ("SHA256", hashlib.sha256)):
        release.append(header + ":")
        for name in ("Packages", "Packages.gz", "Packages.bz2"):
            p = os.path.join(out, name)
            with open(p, "rb") as f:
                digest = algo(f.read()).hexdigest()
            release.append(" %s %d %s" % (digest, os.path.getsize(p), name))
    write_text(os.path.join(out, "Release"), "\n".join(release) + "\n")

    # depictions
    with open(os.path.join(src, "templates", "depiction.html"), encoding="utf-8") as f:
        dep_tpl = f.read()
    with open(os.path.join(src, "templates", "index.html"), encoding="utf-8") as f:
        idx_tpl = f.read()
    support = repo.get("support_url", "")

    for pkg, meta in packages_meta.items():
        d = newest.get(pkg)
        if not d:
            continue  # (an entry being prepared: no page until its deb is on the server)
        version = d["info"]["Version"] if d else "-"
        name = meta.get("name") or (d["info"].get("Name") if d else pkg)
        shots, phone_shots = [], []
        shots_dir = os.path.join(src, "screenshots", pkg)
        if os.path.isdir(shots_dir):
            shots = sorted(n for n in os.listdir(shots_dir) if n.lower().endswith((".png", ".jpg", ".jpeg")))
        phone_dir = os.path.join(shots_dir, "iphone")
        if os.path.isdir(phone_dir):
            phone_shots = sorted(n for n in os.listdir(phone_dir) if n.lower().endswith((".png", ".jpg", ".jpeg")))
        ipa_url, trollstore_link = trollstore_urls(meta, by_pkg, pkg)
        for lang in LANGS:
            s = STRINGS[lang]
            t = section(dep_tpl, "notes", bool(pick_list(meta.get("notes"), lang)))
            t = section(t, "screens", bool(shots))
            t = section(t, "screens_phone", bool(phone_shots))
            t = section(t, "changes", bool(pick_list(meta.get("changes"), lang)))
            t = section(t, "github", bool(meta.get("github")))
            t = section(t, "support", bool(support))
            t = section(t, "trollstore", bool(ipa_url))
            values = {
                "ipa_url": esc(ipa_url or ""),
                "trollstore_link": esc(trollstore_link or ""),
                "lang": lang,
                "pkg": esc(pkg),
                "name": esc(name),
                "tagline": esc(pick(meta.get("tagline"), lang)),
                "about": esc(pick(meta.get("about"), lang)),
                "features": render_list(pick_list(meta.get("features"), lang)),
                "notes": render_list(pick_list(meta.get("notes"), lang)),
                "changes": render_list(pick_list(meta.get("changes"), lang)),
                "screens": "\n".join('<img src="../../screenshots/%s/%s" alt="">' % (esc(pkg), esc(n)) for n in shots),
                "shots_class": " portrait" if meta.get("shots_portrait") else "",
                "screens_phone": "\n".join('<img src="../../screenshots/%s/iphone/%s" alt="">' % (esc(pkg), esc(n)) for n in phone_shots),
                "version": esc(version),
                "size": esc(human_size(d["size"])) if d else "-",
                "date": datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d") if d else "-",
                "github": esc(meta.get("github", "")),
                "support": esc(support),
                "icon": esc(meta.get("icon") or (pkg + ".png")),
                "footer": esc(pick(repo.get("footer"), lang)),
                "repo_url": esc(base + "/"),
                "repo_label": esc(repo["label"]),
                "feedback_href": esc("../../feedback/" + feedback_href(lang, pkg)),
                "css_v": css_v,
            }
            for key, value in s.items():
                values["t_" + key] = esc(value.replace("{v}", version))
            values["t_requires_v"] = esc(requires_text(pkg, lang))
            fname = "index.html" if lang == "en" else "index.%s.html" % lang
            write_text(os.path.join(out, "depictions", pkg, fname), render(t, values))
            dep = sileo_depiction(pkg, meta, d, lang, shots, base, support, repo, tints.get(pkg), banner_url(pkg), requires_text(pkg, lang), ipa_url, phone_shots)
            jname = "%s.json" % pkg if lang == "en" else "%s.%s.json" % (pkg, lang)
            write_text(os.path.join(out, "sileo", jname), json.dumps(dep, ensure_ascii=False, indent=1))

    # featured banners (Sileo and Zebra read sileo-featured.json)
    banners = []
    for pkg in order:
        meta = packages_meta.get(pkg)
        url = banner_url(pkg)
        if meta and url and pkg in newest:
            banners.append({"url": url, "title": meta.get("name") or pkg, "package": pkg, "hideShadow": False})
    if banners:
        featured = {"class": "FeaturedBannersView", "itemSize": "{263, 148}", "itemCornerRadius": 10, "banners": banners}
        write_text(os.path.join(out, "sileo-featured.json"), json.dumps(featured, ensure_ascii=False, indent=1))

    # landing page
    for lang in LANGS:
        s = STRINGS[lang]
        rows = []
        for pkg in order:
            meta = packages_meta.get(pkg)
            d = newest.get(pkg)
            if not meta or not d:
                continue  # (no deb on the server yet: not listed)
            name = meta.get("name") or (d["info"].get("Name") if d else pkg)
            version = d["info"]["Version"] if d else ""
            page = "depictions/%s/%s" % (pkg, "index.html" if lang == "en" else "index.%s.html" % lang)
            icon = meta.get("icon") or (pkg + ".png")
            rows.append(
                '<a class="row app" href="%s"><img src="icons/%s" alt=""><span class="t"><span class="n">%s</span>'
                '<span class="d">%s</span></span><span class="v">%s</span><span class="chev">&#8250;</span></a>'
                % (esc(page), esc(icon), esc(name), esc(pick(meta.get("tagline"), lang)), esc(version))
            )
        t = section(idx_tpl, "support", bool(support))
        values = {
            "lang": lang,
            "rows": "\n".join(rows),
            "repo_url": esc(base + "/"),
            "repo_label": esc(repo["label"]),
            "title": esc(pick(repo.get("title"), lang) or repo["label"]),
            "intro": esc(pick(repo.get("intro"), lang)),
            "about_repo_text": esc(pick(repo.get("about"), lang)),
            "footer": esc(pick(repo.get("footer"), lang)),
            "support": esc(support),
            "github": esc(repo.get("github_url", "")),
            "cydia_link": esc("cydia://url/https://cydia.saurik.com/api/share#?source=" + base + "/"),
            "sileo_link": esc("sileo://source/" + base + "/"),
            "zebra_link": esc("zbra://sources/add/" + base + "/"),
            "installer_link": esc("installer://add/repo=" + base + "/"),
            "count": str(len(rows)),
            "feedback_href": esc("feedback/" + feedback_href(lang)),
            "feedback_newapp_href": esc("feedback/" + feedback_href(lang, query="type=newapp")),
            "css_v": css_v,
        }
        for key, value in s.items():
            values["t_" + key] = esc(value)
        write_text(os.path.join(out, "index.html" if lang == "en" else "index.%s.html" % lang), render(t, values))

    # the feedback form: every published app in its picker (the page is rebuilt with the repository, so the list is
    # always complete); it posts to feedback/api/submit, which nginx passes to feedback/feedback_server.py
    fb_path = os.path.join(src, "templates", "feedback.html")
    if os.path.exists(fb_path):
        with open(fb_path, encoding="utf-8") as f:
            fb_tpl = f.read()
        for lang in LANGS:
            s = STRINGS[lang]
            apps = []
            for pkg in order:
                meta = packages_meta.get(pkg)
                if meta and pkg in newest:
                    apps.append('<option value="%s">%s</option>' % (esc(pkg), esc(meta.get("name") or pkg)))
            devices = []
            for value, label in DEVICES:
                devices.append('<option value="%s">%s</option>' % (esc(value), esc(pick(label, lang) if label else value)))
            installers = ['<option value="%s">%s</option>' % (esc(v), esc(n)) for v, n in INSTALLERS]
            installers.append('<option value="ipa">%s</option>' % esc(s["fb_installed_ipa"]))
            js = {
                "hint_bug": s["fb_text_hint_bug"], "hint_idea": s["fb_text_hint_idea"],
                "hint_newapp": s["fb_text_hint_newapp"], "hint_other": s["fb_text_hint_other"],
                "need_text": s["fb_need_text"], "need_app": s["fb_need_app"], "sending": s["fb_sending"],
            }
            values = {
                "lang": lang,
                "apps": "\n".join(apps),
                "devices": "\n".join(devices),
                "installers": "\n".join(installers),
                "repo_url": esc(base + "/"),
                "repo_label": esc(repo["label"]),
                "index_href": esc("../" + ("index.html" if lang == "en" else "index.%s.html" % lang)),
                "js_strings": json.dumps(js, ensure_ascii=False).replace("</", "<\\/"),
                "css_v": css_v,
            }
            for key, value in s.items():
                values["t_" + key] = esc(value)
            write_text(os.path.join(out, "feedback", "index.html" if lang == "en" else "index.%s.html" % lang), render(fb_tpl, values))

    print("%d package(s), %d deb(s) listed in %s" % (len(archs), len(kept), out))
    for pkg in order:
        if pkg in newest:
            print("  %-28s %-8s %s" % (pkg, newest[pkg]["info"]["Version"], " + ".join(sorted(archs[pkg]))))
    for pkg in archs:
        if pkg not in packages_meta:
            print("  note: %s has a deb but no entry in data.json (listed without a depiction)" % pkg)


if __name__ == "__main__":
    main()
