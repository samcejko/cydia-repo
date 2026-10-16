# cydia-repo

The repository of samcejko's iOS 6 apps: `http://repo.samcejko.eu/` for Cydia, Sileo, Zebra and Installer. It is a
plain, flat APT repository (`Packages`, `Packages.bz2`, `Release`, `debs/`) served by the nginx on the Raspberry Pi
through the Cloudflare tunnel, plus a web depiction page per app in English and Czech (Cydia, Installer, Zebra), a
native Sileo depiction (`sileo/<package>.json`, Sileo and Zebra), the featured banners (`sileo-featured.json`),
"add this repository" links for all four package managers on the landing page, and for the apps that have an arm64
build an "Install with TrollStore" link (`apple-magnifier://install?url=…`) to the `-arm64.ipa` of the GitHub release
of the same version (`"ipa"` in data.json is the IPA's base name).

- `tools/mkrepo.py` builds everything on the Pi (Python 3, standard library only): it reads the debs, writes the
  index files and renders the depiction pages and the landing page from `site/`.
- `tools/repo-publish.ps1` uploads debs and the site sources from Windows and runs the build (settings in
  `tools/local.json`, see `local.json.example`).
- `site/data.json` holds the texts of every package (tagline, about, features, notes, what's new) in both
  languages, `site/templates/` the two page templates, `site/style.css` the iOS 6 look, `site/icons/` the app icons
  (the 144 px `Icon-72@2x.png` out of each app), `site/screenshots/<package>/` the screenshots shown on a page.
- `site/banners/<package>.png` are the featured banners (526x296, drawn from the icons by a GDI+ script) with
  `colors.json` for the native depictions' tint; `site/screenshots/<package>/` the screenshots shown on a page.
- `nginx/repo.conf` is the server configuration (the `repo.samcejko.eu` server block, no cache on the index, plain
  http for Cydia on iOS 6, Czech pages by Accept-Language). The hostname is a public hostname of the Cloudflare tunnel.
- `feedback/`: the bug-report / idea / app-request form at `http://repo.samcejko.eu/feedback/` (linked from the
  landing page and every depiction, which preselects the app). The page itself is static: `site/templates/feedback.html`
  rendered by mkrepo in both languages with every published app in its picker, so the list is complete after each
  rebuild; it works in Safari on iOS 6 (ES5, no HTML5 validation). It posts to `feedback/api/submit`, which nginx
  passes to `feedback/feedback_server.py`, a Python 3 service on the Pi (port 8732, systemd unit
  `repo-feedback.service`, user samuel) that stores every report in `/home/samuel/feedback/data/feedback.json` (an
  array; `feedback.jsonl` is an append-only copy) and answers with a thank-you page. The reports can be read at
  `https://repo.samcejko.eu/feedback/api/list?token=…` (`&format=json` for the raw file); the token is in
  `/home/samuel/feedback/data/token`, created at the first start. Each report on that page has a "Smazat" button
  (`POST feedback/api/delete` with token + id): the report leaves `feedback.json` for `deleted.jsonl`, and its number
  is never given out again. Limits: a honeypot field, 15 s between reports and 10 per hour from one address.
  `python3 feedback/feedback_server.py --selftest` exercises it on a free port.
- `tools/pi-update.sh` updates the Pi from GitHub without the LAN (run in the browser SSH): it fetches the debs of
  `tools/fetch-debs.txt`, rebuilds the repository (dry run first), then installs or updates the feedback service and
  `nginx/repo.conf` when they changed.

Publishing a new version of an app:

```powershell
.\tools\repo-publish.ps1 -Deb ..\twitch-ios6\packages\run-<id>\com.samcejko.twitcher_<version>_iphoneos-arm.deb
```

Then update that package's `changes` in `site/data.json` and run `.\tools\repo-publish.ps1 -Site`. The two newest
versions of each package stay in the repository (`-Keep`), `-Prune` deletes the older debs on the Pi.

Cydia on iOS 6 refuses today's TLS certificates, so the repository is `http://`. Cloudflare caches `.gz` and `.bz2`
by default, hence the `Cache-Control: no-cache` on the index files in `nginx/repo.conf`.
