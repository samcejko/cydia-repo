# Publishes debs and the site sources to the Pi and rebuilds the Cydia repository there.
#
# Usage:
#   .\tools\repo-publish.ps1 -Deb ..\twitch-ios6\packages\run-123\com.samcejko.twitcher_0.1.2_iphoneos-arm.deb
#   .\tools\repo-publish.ps1 -Site            # upload site\ (data.json, templates, style, icons, screenshots) + tools\mkrepo.py, rebuild
#   .\tools\repo-publish.ps1 -Site -Deb a.deb,b.deb -Prune
#   .\tools\repo-publish.ps1                  # just rebuild the index from what is on the Pi
#
# Settings (optional) in tools\local.json, which is not committed:
#   { "host": "192.168.1.154", "user": "samuel", "key": "~/.ssh/rpi",
#     "remoteSrc": "/home/samuel/cydia-repo", "remoteOut": "/home/samuel/nginx/html/repo",
#     "baseUrl": "http://repo.samcejko.eu" }
param(
    [string[]]$Deb = @(),
    [switch]$Site,
    [switch]$Prune,
    [int]$Keep = 2,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

$cfg = @{
    host      = '192.168.1.154'
    user      = 'samuel'
    key       = '~/.ssh/rpi'
    remoteSrc = '/home/samuel/cydia-repo'
    remoteOut = '/home/samuel/nginx/html/repo'
    baseUrl   = 'http://repo.samcejko.eu'
}
$cfgPath = Join-Path $PSScriptRoot 'local.json'
if (Test-Path $cfgPath) {
    $j = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $j.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
}
$target = "$($cfg.user)@$($cfg.host)"
$sshOpts = @('-i', $cfg.key, '-o', 'ConnectTimeout=20', '-o', 'BatchMode=yes')

# Windows ssh.exe drops inner double quotes: remote commands use single quotes only.
function Invoke-Remote([string]$Command) {
    & ssh @sshOpts $target $Command
    if ($LASTEXITCODE) { throw "ssh failed ($LASTEXITCODE): $Command" }
}
function Copy-Remote([string[]]$Paths, [string]$Dest) {
    & scp @sshOpts -r @Paths "${target}:$Dest"
    if ($LASTEXITCODE) { throw "scp failed ($LASTEXITCODE): $Dest" }
}

Invoke-Remote "mkdir -p '$($cfg.remoteSrc)/tools' '$($cfg.remoteOut)/debs'"

# -Deb "a,b" arrives as one string: split it
$debs = @()
foreach ($d in $Deb) {
    foreach ($p in ($d -split ',')) {
        if ($p.Trim()) { $debs += (Resolve-Path $p.Trim()).Path }
    }
}
if ($debs.Count) {
    Copy-Remote $debs "$($cfg.remoteOut)/debs/"
    Write-Host "uploaded $($debs.Count) deb(s)"
}

if ($Site) {
    Copy-Remote @((Join-Path $root 'site')) "$($cfg.remoteSrc)/"
    Copy-Remote @((Join-Path $root 'tools\mkrepo.py')) "$($cfg.remoteSrc)/tools/"
    # files written on Windows may carry CR line ends
    Invoke-Remote "sed -i 's/\r`$//' '$($cfg.remoteSrc)/tools/mkrepo.py'"
    Write-Host 'uploaded site sources'
}

if (-not $NoBuild) {
    $pruneArg = ''
    if ($Prune) { $pruneArg = ' --prune' }
    Invoke-Remote "python3 '$($cfg.remoteSrc)/tools/mkrepo.py' --src '$($cfg.remoteSrc)/site' --out '$($cfg.remoteOut)' --base-url '$($cfg.baseUrl)' --keep $Keep$pruneArg"
    # nginx in its container reads as another user: everything world-readable
    Invoke-Remote "chmod -R o+rX '$($cfg.remoteOut)'"
    Write-Host "repository rebuilt: $($cfg.baseUrl)/"
}
