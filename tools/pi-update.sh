#!/bin/bash
# Updates the repository on the Pi straight from GitHub (site sources + mkrepo.py) and rebuilds it - for when the
# PC cannot reach the Pi over the LAN (run it in the browser SSH, https://ssh.samcejko.eu):
#   curl -sL https://raw.githubusercontent.com/samcejko/cydia-repo/main/tools/pi-update.sh | bash
# A dry run on a copy of the repository comes first: every deb mkrepo repacks must keep its files byte for byte
# and say the Depends the index wants, else nothing is touched. The log can be read at
# http://repo.samcejko.eu/zz-update.log for a quarter of an hour, then it deletes itself.
set -u -o pipefail
SRC=/home/samuel/cydia-repo
OUT=/home/samuel/nginx/html/repo
BASE=http://repo.samcejko.eu
LOG="$OUT/zz-update.log"
T=$(mktemp -d)
TEST=$(mktemp -d)

run() {
    echo "== $(date)"
    curl -fsSL https://codeload.github.com/samcejko/cydia-repo/tar.gz/main | tar xz -C "$T" --strip-components=1 \
        || { echo "FAILED: download from GitHub"; return 1; }
    echo "downloaded $(ls "$T")"

    echo "== dry run on a copy"
    cp -a "$OUT/." "$TEST/" || { echo "FAILED: copy"; return 1; }
    python3 "$T/tools/mkrepo.py" --src "$T/site" --out "$TEST" --base-url "$BASE" --keep 2 || { echo "FAILED: dry run"; return 1; }
    local bad=0 n=0
    for f in "$TEST"/debs/*-d??????.deb; do
        [ -e "$f" ] || continue
        n=$((n + 1))
        local name orig base
        name=$(basename "$f")
        base=$(echo "$name" | sed -E 's/-d[0-9a-f]{6}\.deb$/.deb/')
        orig="$TEST/debs/original/$base"
        [ -e "$orig" ] || orig="$OUT/debs/$base"
        [ -e "$orig" ] || orig="$OUT/debs/original/$base"
        if [ ! -e "$orig" ]; then echo "  ? $name: original $base not found (repacked earlier)"; continue; fi
        local a b dep
        a=$(dpkg-deb --fsys-tarfile "$orig" | md5sum | cut -d' ' -f1)
        b=$(dpkg-deb --fsys-tarfile "$f" | md5sum | cut -d' ' -f1)
        dep=$(dpkg-deb -f "$f" Depends)
        if [ -n "$a" ] && [ "$a" = "$b" ] && dpkg-deb -I "$f" >/dev/null; then
            echo "  ok $name  files identical, Depends: $dep"
        else
            echo "  BAD $name  files $a vs $b"; bad=1
        fi
    done
    echo "repacked in the dry run: $n"
    [ "$bad" = 0 ] || { echo "FAILED: a repacked deb differs, the real repository was not touched"; return 1; }

    echo "== real run"
    mkdir -p "$SRC/tools"
    rm -rf "$SRC/site.new" && cp -r "$T/site" "$SRC/site.new" && rm -rf "$SRC/site" && mv "$SRC/site.new" "$SRC/site"
    cp "$T/tools/mkrepo.py" "$SRC/tools/mkrepo.py"
    python3 "$SRC/tools/mkrepo.py" --src "$SRC/site" --out "$OUT" --base-url "$BASE" --keep 2 || { echo "FAILED: mkrepo"; return 1; }
    chmod -R o+rX "$OUT"
    echo "== DONE $(date)"
}

run > "$LOG" 2>&1
chmod o+r "$LOG"
rm -rf "$T" "$TEST"
tail -n 3 "$LOG"
nohup sh -c "sleep 900; rm -f '$LOG'" >/dev/null 2>&1 &
