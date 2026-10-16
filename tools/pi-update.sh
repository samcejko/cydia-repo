#!/bin/bash
# Updates the repository on the Pi straight from GitHub (site sources + mkrepo.py) and rebuilds it - for when the
# PC cannot reach the Pi over the LAN (run it in the browser SSH, https://ssh.samcejko.eu):
#   curl -sL https://raw.githubusercontent.com/samcejko/cydia-repo/main/tools/pi-update.sh | bash
# New debs listed in tools/fetch-debs.txt ("<file name> <url> <sha256>" per line, e.g. a GitHub release asset) are
# downloaded first and must match their SHA-256. A dry run on a copy of the repository comes next: every deb mkrepo
# repacks must keep its files byte for byte and say the Depends the index wants, else nothing is touched. Then the
# feedback service (feedback/feedback_server.py, systemd unit repo-feedback.service) and the nginx configuration
# (nginx/repo.conf) are installed or updated when they changed; the service's self-test must pass first and the new
# nginx configuration must pass "nginx -t", else the old one stays. The log can be read at
# http://repo.samcejko.eu/zz-update.log for a quarter of an hour, then it deletes itself.
set -u -o pipefail
SRC=/home/samuel/cydia-repo
OUT=/home/samuel/nginx/html/repo
BASE=http://repo.samcejko.eu
NGINX=/home/samuel/nginx
FB=/home/samuel/feedback
LOG="$OUT/zz-update.log"
T=$(mktemp -d)
TEST=$(mktemp -d)
NEW=$(mktemp -d)

# the feedback service: its program, its systemd unit and the nginx location that leads to it
install_feedback() {
    echo "== feedback service"
    [ -e "$T/feedback/feedback_server.py" ] || { echo "no feedback/ in the download, skipped"; return 0; }
    python3 "$T/feedback/feedback_server.py" --selftest || { echo "FAILED: the feedback self-test"; return 1; }
    mkdir -p "$FB/data" || { echo "FAILED: mkdir $FB"; return 1; }
    local restart=0
    if ! cmp -s "$T/feedback/feedback_server.py" "$FB/feedback_server.py"; then
        cp "$T/feedback/feedback_server.py" "$FB/feedback_server.py" && restart=1 || { echo "FAILED: copy of feedback_server.py"; return 1; }
        echo "feedback_server.py updated"
    fi
    if ! cmp -s "$T/feedback/repo-feedback.service" /etc/systemd/system/repo-feedback.service; then
        sudo cp "$T/feedback/repo-feedback.service" /etc/systemd/system/repo-feedback.service || { echo "FAILED: install of the unit"; return 1; }
        sudo systemctl daemon-reload
        sudo systemctl enable repo-feedback.service >/dev/null 2>&1
        restart=1
        echo "repo-feedback.service updated"
    fi
    if [ "$restart" = 1 ] || ! systemctl is-active --quiet repo-feedback.service; then
        sudo systemctl restart repo-feedback.service || { echo "FAILED: restart of repo-feedback.service"; return 1; }
        sleep 2
    fi
    curl -fsS --max-time 5 http://127.0.0.1:8732/health || { echo "FAILED: the service does not answer on 8732"; sudo systemctl status repo-feedback.service --no-pager | tail -n 12; return 1; }
    echo

    echo "== nginx"
    # the nginx container reaches the Pi at the gateway of its Docker network (or the Pi's LAN address)
    local gw
    gw=$(docker inspect nginx -f '{{range .NetworkSettings.Networks}}{{.Gateway}} {{end}}' 2>/dev/null | awk '{print $1}')
    [ -n "$gw" ] || gw=192.168.1.154
    sed -e "s#ROOT#/usr/share/nginx/html/repo#" -e "s#FEEDBACK_UPSTREAM#$gw#" "$T/nginx/repo.conf" > "$T/repo.conf.new"
    if cmp -s "$T/repo.conf.new" "$NGINX/conf.d/repo.conf"; then
        echo "conf.d/repo.conf unchanged (upstream $gw)"
    else
        echo "conf.d/repo.conf changes (upstream $gw):"
        diff -u "$NGINX/conf.d/repo.conf" "$T/repo.conf.new"
        cp "$NGINX/conf.d/repo.conf" "$NGINX/repo.conf.bak" 2>/dev/null
        cp "$T/repo.conf.new" "$NGINX/conf.d/repo.conf" || { echo "FAILED: write of conf.d/repo.conf"; return 1; }
        if docker exec nginx nginx -t; then
            docker exec nginx nginx -s reload && echo "nginx reloaded"
        else
            echo "FAILED: nginx -t refused the new configuration, the old one is back"
            [ -e "$NGINX/repo.conf.bak" ] && cp "$NGINX/repo.conf.bak" "$NGINX/conf.d/repo.conf"
            return 1
        fi
    fi
    sleep 1
    # through nginx, at the Pi's own LAN address (the container's published port is a NAT rule, 127.0.0.1 may miss it)
    local ip health
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -n "$ip" ] || ip=192.168.1.154
    health=$(curl -fsS --max-time 5 -H 'Host: repo.samcejko.eu' "http://$ip/feedback/api/health") \
        && echo "through nginx: $health" \
        || { echo "FAILED: nginx cannot reach the feedback service at $gw:8732"; return 1; }
    echo "the reports: $FB/data/feedback.json; the list: $BASE/feedback/api/list?token=<the content of $FB/data/token>"
}

# the debs of tools/fetch-debs.txt the repository does not have yet, into $NEW (checked against their SHA-256)
fetch_debs() {
    local list="$T/tools/fetch-debs.txt" name url sum
    [ -e "$list" ] || return 0
    while read -r name url sum; do
        case "$name" in ''|'#'*) continue ;; esac
        case "$name" in */*|.*) echo "FAILED: bad deb name $name"; return 1 ;; esac
        [ -e "$OUT/debs/$name" ] || [ -e "$OUT/debs/original/$name" ] && continue
        curl -fsSL -o "$NEW/$name" "$url" || { echo "FAILED: download $name"; return 1; }
        echo "$sum  $NEW/$name" | sha256sum -c --quiet - || { echo "FAILED: $name does not match its SHA-256"; return 1; }
        dpkg-deb -I "$NEW/$name" >/dev/null || { echo "FAILED: $name is no deb"; return 1; }
        echo "fetched $name"
    done < "$list"
}

run() {
    echo "== $(date)"
    curl -fsSL https://codeload.github.com/samcejko/cydia-repo/tar.gz/main | tar xz -C "$T" --strip-components=1 \
        || { echo "FAILED: download from GitHub"; return 1; }
    echo "downloaded $(ls "$T")"
    fetch_debs || return 1

    echo "== dry run on a copy"
    cp -a "$OUT/." "$TEST/" || { echo "FAILED: copy"; return 1; }
    cp -a "$NEW/." "$TEST/debs/" || { echo "FAILED: copy of the new debs"; return 1; }
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
    cp -a "$NEW/." "$OUT/debs/" || { echo "FAILED: copy of the new debs"; return 1; }
    python3 "$SRC/tools/mkrepo.py" --src "$SRC/site" --out "$OUT" --base-url "$BASE" --keep 2 || { echo "FAILED: mkrepo"; return 1; }
    chmod -R o+rX "$OUT"
    install_feedback || return 1
    echo "== DONE $(date)"
}

run > "$LOG" 2>&1
chmod o+r "$LOG"
rm -rf "$T" "$TEST" "$NEW"
tail -n 3 "$LOG"
nohup sh -c "sleep 900; rm -f '$LOG'" >/dev/null 2>&1 &
