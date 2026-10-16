#!/usr/bin/env python3
"""feedback_server.py - stores the reports of the feedback form (feedback/) as JSON on the Pi.

Standard library only. nginx passes http://repo.samcejko.eu/feedback/api/<path> to this service (nginx/repo.conf),
systemd keeps it running (repo-feedback.service):

  python3 feedback_server.py --bind 0.0.0.0 --port 8732 --data /home/samuel/feedback/data \
                             [--data-json /home/samuel/cydia-repo/site/data.json]
  python3 feedback_server.py --selftest        # starts a copy on a free port, sends test reports, exits 0 when fine

Paths (with or without the /feedback/api prefix):
  POST /submit   the form (application/x-www-form-urlencoded, or JSON): type, app, app_other, device, device_other,
                 ios, app_version, installed_via, text, proposal, name, contact, lang, website (a honeypot: must
                 stay empty). Answers with an HTML page in the form's language, or JSON for an "Accept: application/json"
                 or JSON request.
  GET  /list?token=<token>[&format=json]   the stored reports, newest first (HTML), or the raw JSON file
  GET  /health   {"ok": true, "count": N}

Files in --data: feedback.json (all reports, an array of objects, rewritten atomically), feedback.jsonl (one line
appended per report, a safety copy), token (the secret of /list, created at the first start, mode 600).
"""
import argparse
import html
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import fcntl
except ImportError:  # not Linux: the thread lock alone
    fcntl = None

TYPES = ("bug", "idea", "newapp", "other")
MAX_BODY = 64 * 1024
LIMITS = {"type": 10, "text": 6000, "proposal": 6000, "app_other": 200, "device_other": 120, "ios": 40, "app_version": 40,
          "name": 120, "contact": 200, "app": 120, "device": 60, "installed_via": 30, "lang": 5}

STRINGS = {
    "en": {
        "thanks": "Thanks!",
        "feedback": "Feedback",
        "stored": "Your report has the number {id}. I will have a look.",
        "back": "Back to the repository",
        "another": "Send another",
        "error": "Sorry, that did not work",
        "retry": "Back to the form",
        "need_text": "Please write a description.",
        "too_many": "Too many reports from your address, please try again later.",
        "duplicate": "This report has already been received, thanks.",
        "bad_request": "The form arrived incomplete, please try again.",
        "type_bug": "Bug", "type_idea": "Idea", "type_newapp": "App request", "type_other": "Other",
    },
    "cs": {
        "thanks": "Díky!",
        "feedback": "Zpětná vazba",
        "stored": "Tvoje hlášení má číslo {id}. Podívám se na to.",
        "back": "Zpět na repozitář",
        "another": "Poslat další",
        "error": "Tohle se bohužel nepovedlo",
        "retry": "Zpět na formulář",
        "need_text": "Napiš prosím popis.",
        "too_many": "Příliš mnoho hlášení z tvé adresy, zkus to prosím později.",
        "duplicate": "Tohle hlášení už dorazilo, díky.",
        "bad_request": "Formulář dorazil neúplný, zkus to prosím znovu.",
        "type_bug": "Chyba", "type_idea": "Nápad", "type_newapp": "Návrh aplikace", "type_other": "Jiné",
    },
}


def esc(s):
    return html.escape(str(s), quote=True)


def clean(value, limit):
    """A field as stored: a string, control characters except line breaks and tabs removed, trimmed, cut to the limit."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if value is None:
        value = ""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and c != "\x7f"))
    return value.strip()[:limit]


class Store:
    """feedback.json (the array everything reads) + feedback.jsonl (append-only copy), written under a lock."""

    def __init__(self, data_dir):
        self.dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "feedback.json")
        self.log_path = os.path.join(data_dir, "feedback.jsonl")
        self.lock_path = os.path.join(data_dir, ".lock")
        self.lock = threading.Lock()

    def _locked(self):
        return _FileLock(self.lock_path) if fcntl else _NoLock()

    def load(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("%s is not a JSON array" % self.path)
        return data

    def add(self, entry):
        with self.lock, self._locked():
            data = self.load()
            entry["id"] = max([e.get("id", 0) for e in data if isinstance(e.get("id"), int)] + [0]) + 1
            data.append(entry)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.write("\n")
            os.replace(tmp, self.path)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry["id"]

    def count(self):
        with self.lock:
            try:
                return len(self.load())
            except Exception:
                return -1


class _FileLock:
    def __init__(self, path):
        self.path = path
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class RateLimit:
    """Per address: a pause between two reports, a limit per hour and per day, and one for everybody per day."""

    def __init__(self, min_gap=15, per_hour=10, per_day=40, total_per_day=400):
        self.min_gap, self.per_hour, self.per_day, self.total_per_day = min_gap, per_hour, per_day, total_per_day
        self.hits = {}
        self.lock = threading.Lock()

    def check(self, ip, now=None):
        """True when another report from ip may be stored now (and counts it)."""
        now = now or time.time()
        with self.lock:
            for key in list(self.hits):  # forget yesterday
                self.hits[key] = [t for t in self.hits[key] if now - t < 86400]
                if not self.hits[key]:
                    del self.hits[key]
            mine = self.hits.get(ip, [])
            total = sum(len(v) for v in self.hits.values())
            if total >= self.total_per_day:
                return False
            if mine and now - mine[-1] < self.min_gap:
                return False
            if len([t for t in mine if now - t < 3600]) >= self.per_hour or len(mine) >= self.per_day:
                return False
            self.hits.setdefault(ip, []).append(now)
            return True


class Names:
    """Package id -> app name out of site/data.json, re-read when the file changes."""

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.names = {}

    def get(self, pkg):
        if not self.path or not pkg:
            return ""
        try:
            mtime = os.stat(self.path).st_mtime
            if mtime != self.mtime:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self.names = {k: (v.get("name") or k) for k, v in data.get("packages", {}).items()}
                self.mtime = mtime
        except Exception as e:
            print("data.json not readable: %s" % e, file=sys.stderr)
        return self.names.get(pkg, "")


def page(lang, title, body, css="../../style.css", icon="../../CydiaIcon.png"):
    """A page in the repository's look (the form posts to /feedback/api/submit, so ../../ is the repository root)."""
    s = STRINGS[lang]
    return ("<!DOCTYPE html>\n<html lang=\"%s\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, maximum-scale=1\">\n"
            "<title>%s</title>\n<link rel=\"stylesheet\" href=\"%s\">\n</head>\n<body>\n"
            "<div class=\"hdr\"><img class=\"appicon\" src=\"%s\" alt=\"\"><div class=\"hdrt\"><h1>%s</h1><p>%s</p></div></div>\n"
            "%s\n</body>\n</html>\n") % (lang, esc(title), esc(css), esc(icon), esc(title), esc(s["feedback"]), body)


def index_href(lang):
    return "index.html" if lang == "en" else "index.%s.html" % lang


def thanks_page(lang, rid):
    s = STRINGS[lang]
    body = ("<div class=\"box\"><p>%s</p></div>\n<div class=\"box list\">\n"
            "<a class=\"row link\" href=\"../../%s\"><span class=\"k\">%s</span><span class=\"chev\">&#8250;</span></a>\n"
            "<a class=\"row link\" href=\"../%s\"><span class=\"k\">%s</span><span class=\"chev\">&#8250;</span></a>\n</div>"
            ) % (esc(s["stored"]).replace("{id}", "<b>#%d</b>" % rid), index_href(lang), esc(s["back"]), index_href(lang), esc(s["another"]))
    return page(lang, s["thanks"], body)


def error_page(lang, message):
    s = STRINGS[lang]
    body = ("<div class=\"box\"><p>%s</p></div>\n<div class=\"box list\">\n"
            "<a class=\"row link\" href=\"javascript:history.back()\"><span class=\"k\">%s</span><span class=\"chev\">&#8250;</span></a>\n"
            "<a class=\"row link\" href=\"../../%s\"><span class=\"k\">%s</span><span class=\"chev\">&#8250;</span></a>\n</div>"
            ) % (esc(message), esc(s["retry"]), index_href(lang), esc(s["back"]))
    return page(lang, s["error"], body)


def list_page(entries, token):
    """All reports, newest first, in the repository's look (for the phone; the raw file via &format=json)."""
    s = STRINGS["cs"]
    parts = []
    for e in reversed(entries):
        head = "#%s · %s" % (e.get("id", "?"), s.get("type_" + str(e.get("type")), e.get("type", "?")))
        app = e.get("app_name") or e.get("app") or e.get("app_other") or ""
        if e.get("app") and e.get("app") != "other" and e.get("app_other"):
            app += " (%s)" % e["app_other"]
        elif e.get("app") == "other" and e.get("app_other"):
            app = e["app_other"]
        if app:
            head += " · " + app
        if e.get("app_version"):
            head += " " + e["app_version"]
        meta = [str(e.get("time", ""))[:16].replace("T", " ")]
        device = e.get("device_other") if e.get("device") == "other" else e.get("device")
        if device:
            meta.append(device)
        if e.get("ios"):
            meta.append("iOS " + e["ios"])
        if e.get("installed_via"):
            meta.append(e["installed_via"])
        if e.get("lang"):
            meta.append(e["lang"])
        who = " · ".join(x for x in (e.get("name"), e.get("contact"), e.get("ip")) if x)
        box = ["<div class=\"box\">", "<p><b>%s</b></p>" % esc(head), "<p class=\"meta\">%s</p>" % esc(" · ".join(meta)),
               "<p class=\"txt\">%s</p>" % esc(e.get("text", ""))]
        if e.get("proposal"):
            box.append("<p class=\"txt\"><b>Návrh:</b> %s</p>" % esc(e["proposal"]))
        if who:
            box.append("<p class=\"meta\">%s</p>" % esc(who))
        if e.get("ua"):
            box.append("<p class=\"meta\">%s</p>" % esc(e["ua"]))
        box.append("</div>")
        parts.append("\n".join(box))
    body = ("<div class=\"box\"><p>%d hlášení · <a href=\"list?token=%s&amp;format=json\">feedback.json</a></p></div>\n%s"
            % (len(entries), esc(urllib.parse.quote(token, safe="")), "\n".join(parts) if parts else ""))
    return page("cs", "Feedback (%d)" % len(entries), body)


class Handler(BaseHTTPRequestHandler):
    server_version = "repo-feedback/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection: nothing is ever left unread in a kept-alive socket

    # ----------------------------------------------------------------- plumbing
    def route(self):
        path = urllib.parse.urlsplit(self.path)
        p = path.path
        for prefix in ("/feedback/api", "/api"):
            if p.startswith(prefix + "/"):
                p = p[len(prefix):]
        return p.rstrip("/") or "/", urllib.parse.parse_qs(path.query, keep_blank_values=True)

    def client_ip(self):
        headers = getattr(self, "headers", None)  # not parsed yet when a malformed request line is logged
        if headers is not None:
            for header in ("CF-Connecting-IP", "X-Real-IP"):
                v = headers.get(header)
                if v:
                    return v.strip()
            v = headers.get("X-Forwarded-For")
            if v:
                return v.split(",")[0].strip()
        return self.client_address[0]

    def reply(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def reply_json(self, status, obj):
        self.reply(status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.client_ip(), fmt % args))

    # ----------------------------------------------------------------- GET
    def do_GET(self):
        path, query = self.route()
        if path == "/health":
            return self.reply_json(200, {"ok": True, "count": self.server.store.count()})
        if path == "/list":
            token = (query.get("token") or [""])[0]
            if not token or not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
                return self.reply(403, "forbidden\n", "text/plain; charset=utf-8")
            try:
                entries = self.server.store.load()
            except Exception as e:
                return self.reply(500, "cannot read the reports: %s\n" % e, "text/plain; charset=utf-8")
            if (query.get("format") or [""])[0] == "json":
                return self.reply(200, json.dumps(entries, ensure_ascii=False, indent=1) + "\n", "application/json; charset=utf-8")
            return self.reply(200, list_page(entries, token))
        self.reply(404, "not found\n", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    # ----------------------------------------------------------------- POST
    def read_form(self):
        """The fields of the request as {name: string}, and whether the client talks JSON."""
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("body too large")
        body = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body is not an object")
            return {k: v for k, v in data.items() if isinstance(k, str)}, True
        if "multipart/form-data" in ctype:
            raise ValueError("multipart forms are not accepted")
        fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        wants_json = "application/json" in (self.headers.get("Accept") or "")
        return {k: v[0] if v else "" for k, v in fields.items()}, wants_json

    def do_POST(self):
        path, _ = self.route()
        if path != "/submit":
            return self.reply(404, "not found\n", "text/plain; charset=utf-8")
        lang = "cs" if (self.headers.get("Accept-Language") or "").lower().startswith("cs") else "en"
        try:
            fields, wants_json = self.read_form()
        except Exception as e:
            self.log_message("bad request: %s", e)
            return self.fail(400, lang, STRINGS[lang]["bad_request"], False, "bad_request")
        lang = clean(fields.get("lang"), 5)
        if lang not in STRINGS:
            lang = "cs" if (self.headers.get("Accept-Language") or "").lower().startswith("cs") else "en"
        s = STRINGS[lang]

        entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds")}
        for key, limit in LIMITS.items():
            if key != "lang":
                entry[key] = clean(fields.get(key), limit)
        entry["lang"] = lang
        if entry["type"] not in TYPES:
            entry["type"] = "other"
        entry["app_name"] = self.server.names.get(entry["app"]) if entry["app"] not in ("", "other") else ""
        entry["ip"] = self.client_ip()
        entry["ua"] = clean(self.headers.get("User-Agent"), 300)
        entry["status"] = "new"

        if clean(fields.get("website"), 50):  # the hidden field only robots fill in
            self.log_message("honeypot hit, nothing stored")
            return self.ok(lang, 0, wants_json)
        if len(entry["text"]) < 3:
            return self.fail(400, lang, s["need_text"], wants_json, "need_text")
        key = (entry["ip"], entry["type"], entry["app"], entry["text"])
        if self.server.is_recent(key):
            return self.fail(409, lang, s["duplicate"], wants_json, "duplicate")
        if not self.server.rate.check(entry["ip"]):
            self.log_message("rate limit")
            return self.fail(429, lang, s["too_many"], wants_json, "too_many")
        try:
            rid = self.server.store.add(entry)
        except Exception as e:
            print("cannot store a report: %s" % e, file=sys.stderr)
            return self.fail(500, lang, s["error"], wants_json, "error")
        self.server.note_recent(key)
        self.log_message("stored #%d %s %s %s", rid, entry["type"], entry["app"] or entry["app_other"], entry["device"] or "")
        return self.ok(lang, rid, wants_json)

    def ok(self, lang, rid, wants_json):
        if wants_json:
            return self.reply_json(200, {"ok": True, "id": rid})
        return self.reply(200, thanks_page(lang, rid))

    def fail(self, status, lang, message, wants_json, code):
        if wants_json:
            return self.reply_json(status, {"ok": False, "error": code, "message": message})
        return self.reply(status, error_page(lang, message))


class FeedbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, data_dir, data_json=None, rate=None):
        super().__init__(address, Handler)
        self.store = Store(data_dir)
        self.names = Names(data_json)
        self.rate = rate or RateLimit()
        self.token = self.load_token(os.path.join(data_dir, "token"))
        self.recent = {}
        self.recent_lock = threading.Lock()

    @staticmethod
    def load_token(path):
        try:
            with open(path, encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except FileNotFoundError:
            pass
        token = secrets.token_urlsafe(24)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        print("created the secret of /list in %s" % path)
        return token

    def is_recent(self, key):
        """True when the very same report was stored within the last hour (a double tap on Send)."""
        now = time.time()
        with self.recent_lock:
            for k in list(self.recent):
                if now - self.recent[k] > 3600:
                    del self.recent[k]
            return key in self.recent

    def note_recent(self, key):
        with self.recent_lock:
            self.recent[key] = time.time()


# ----------------------------------------------------------------------------- self-test

def selftest():
    import shutil
    import tempfile
    import urllib.request

    tmp = tempfile.mkdtemp(prefix="feedback-selftest-")
    srv = FeedbackServer(("127.0.0.1", 0), tmp, rate=RateLimit(min_gap=0, per_hour=100, per_day=100))
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % port

    def call(path, data=None, headers=None):
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8"), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8"), e.headers.get("Content-Type", "")

    def check(cond, what):
        if not cond:
            raise AssertionError(what)

    try:
        form = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0 (iPad; CPU OS 6_1_3 like Mac OS X)",
                "CF-Connecting-IP": "203.0.113.5"}
        body = urllib.parse.urlencode({"lang": "cs", "type": "bug", "app": "com.samcejko.twitcher", "device": "iPad 2", "ios": "6.1.3",
                                       "text": "Testovací hlášení ěščřžýáíé\nDruhý řádek", "proposal": "Návrh", "website": ""}).encode("utf-8")
        status, text, ctype = call("/feedback/api/submit", body, form)
        check(status == 200 and "#1" in text and "Díky" in text and "text/html" in ctype, "form post: %s %s" % (status, text[:200]))

        body = json.dumps({"lang": "en", "type": "newapp", "app_other": "Reddit", "text": "A Reddit client please"}).encode("utf-8")
        status, text, _ = call("/submit", body, {"Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.6"})
        check(status == 200 and json.loads(text) == {"ok": True, "id": 2}, "json post: %s %s" % (status, text[:200]))

        body = urllib.parse.urlencode({"lang": "en", "type": "bug", "text": "robot", "website": "http://spam"}).encode("utf-8")
        status, text, _ = call("/submit", body, dict(form, Accept="application/json"))
        check(status == 200 and json.loads(text)["id"] == 0, "honeypot: %s %s" % (status, text[:200]))

        body = urllib.parse.urlencode({"lang": "en", "type": "bug", "text": ""}).encode("utf-8")
        status, text, _ = call("/submit", body, form)
        check(status == 400 and "description" in text, "empty text: %s %s" % (status, text[:200]))

        body = urllib.parse.urlencode({"lang": "cs", "type": "bug", "app": "com.samcejko.twitcher", "text": "Testovací hlášení ěščřžýáíé\nDruhý řádek"}).encode("utf-8")
        status, text, _ = call("/submit", body, form)
        check(status == 409, "duplicate: %s" % status)

        status, text, _ = call("/list")
        check(status == 403, "list without a token: %s" % status)
        status, text, _ = call("/list?token=wrong")
        check(status == 403, "list with a wrong token: %s" % status)
        status, text, _ = call("/feedback/api/list?token=%s&format=json" % urllib.parse.quote(srv.token, safe=""))
        entries = json.loads(text)
        check(status == 200 and [e["id"] for e in entries] == [1, 2], "list json: %s %s" % (status, text[:200]))
        check(entries[0]["text"] == "Testovací hlášení ěščřžýáíé\nDruhý řádek" and entries[0]["ip"] == "203.0.113.5"
              and entries[0]["device"] == "iPad 2" and entries[1]["app_other"] == "Reddit", "stored fields: %r" % entries)
        status, text, _ = call("/list?token=%s" % urllib.parse.quote(srv.token, safe=""))
        check(status == 200 and "Reddit" in text and "#2" in text, "list html: %s" % status)

        status, text, _ = call("/health")
        check(status == 200 and json.loads(text) == {"ok": True, "count": 2}, "health: %s %s" % (status, text))

        with open(os.path.join(tmp, "feedback.json"), encoding="utf-8") as f:
            check(len(json.load(f)) == 2, "feedback.json")
        with open(os.path.join(tmp, "feedback.jsonl"), encoding="utf-8") as f:
            check(len(f.read().strip().split("\n")) == 2, "feedback.jsonl")

        limited = RateLimit(min_gap=0, per_hour=2, per_day=3)
        check(limited.check("a") and limited.check("a") and not limited.check("a"), "rate per hour")
        check(limited.check("b"), "rate other address")
        print("selftest ok (%d reports in %s)" % (len(entries), tmp))
        return 0
    except AssertionError as e:
        print("selftest FAILED: %s" % e, file=sys.stderr)
        return 1
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8732)
    ap.add_argument("--data", default="data", help="folder of feedback.json, feedback.jsonl and token")
    ap.add_argument("--data-json", default=None, help="the repository's site/data.json (app names)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    srv = FeedbackServer((args.bind, args.port), os.path.abspath(args.data), args.data_json)
    print("feedback server on %s:%d, reports in %s" % (args.bind, args.port, srv.store.path))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
