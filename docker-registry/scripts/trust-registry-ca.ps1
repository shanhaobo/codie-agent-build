<#
.SYNOPSIS
  Make THIS Windows machine trust the Codie TLS registry (registry.codie.lan:5000).

.DESCRIPTION
  Windows counterpart of trust-registry-ca.sh. Run on a LAN *consumer* Bridge
  desktop (not the registry host). Docker on Windows runs inside a WSL2 distro,
  so trust is split across two surfaces and this script does both, idempotently:

    1. WSL distro daemon: installs the CA into the distro's
       /etc/docker/certs.d/registry.codie.lan:5000/ca.crt (per-registry trust,
       read live, no daemon restart) and maps registry.codie.lan -> the registry
       host's LAN IP in the distro /etc/hosts.
    2. Windows host: maps registry.codie.lan -> the same IP in
       C:\Windows\System32\drivers\etc\hosts, so the Bridge app (which probes
       the registry directly, not via the daemon) resolves the name.

  The cert binds the NAME, so on host-IP drift just re-run with the new -RegistryIp;
  the registry, certs, and Bridge registryUrl stay untouched.

.PARAMETER RegistryIp
  LAN IP of the machine running the registry (e.g. 10.10.32.64).

.PARAMETER CaPath
  Path to ca.crt. Defaults to ..\certs\ca.crt next to this script (copy
  docker-registry\certs\ca.crt from the registry host — the CA, never ca.key).

.PARAMETER Distro
  WSL distro name. Defaults to the current default distro (wsl -l -q, first line).

.EXAMPLE
  # From an elevated PowerShell 7+ (needs admin to write the Windows hosts file):
  .\trust-registry-ca.ps1 -RegistryIp 10.10.32.64

.NOTES
  After running: in this machine's Bridge -> Images page -> switch to TLS mode ->
  import the same CA (shield icon) -> set registryUrl = registry.codie.lan:5000.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$RegistryIp,
    [string]$CaPath,
    [string]$Distro
)

$ErrorActionPreference = 'Stop'
$Domain   = 'registry.codie.lan'
$Port     = 5000
$HostPort = "${Domain}:${Port}"
$CertsD   = "/etc/docker/certs.d/$HostPort"

function Log  { param($m) Write-Host "[trust-ca] $m" -ForegroundColor Cyan }
function Warn { param($m) Write-Host "[trust-ca] WARN: $m" -ForegroundColor Yellow }

# --- resolve CA path ---
if (-not $CaPath) {
    $CaPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'certs\ca.crt'
}
if (-not (Test-Path $CaPath)) {
    throw "CA not found: $CaPath (copy docker-registry\certs\ca.crt from the registry host)"
}
Log "CA: $CaPath"
Log "mapping $Domain -> $RegistryIp"

# --- resolve WSL distro ---
if (-not $Distro) {
    $Distro = (& wsl -l -q | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1).Trim()
}
if (-not $Distro) { throw "no WSL distro found (wsl -l -q empty)" }
Log "WSL distro: $Distro"

# --- 1. WSL distro: certs.d + hosts (read CA via stdin to avoid path translation) ---
$caPem = Get-Content -Raw -Path $CaPath
$wslScript = @"
set -e
mkdir -p '$CertsD'
cat > '$CertsD/ca.crt'
tmp=`$(mktemp)
grep -vF ' $Domain' /etc/hosts > "`$tmp" 2>/dev/null || true
cat "`$tmp" > /etc/hosts
rm -f "`$tmp"
printf '%s %s\n' '$RegistryIp' '$Domain' >> /etc/hosts
"@
Log "installing CA + hosts entry inside WSL ($Distro)..."
$caPem | & wsl -d $Distro -u root -- bash -c $wslScript
if ($LASTEXITCODE -ne 0) { throw "WSL trust step failed (exit $LASTEXITCODE)" }

# --- 2. Windows host hosts file (Bridge app resolves the name directly) ---
$winHosts = "$env:WINDIR\System32\drivers\etc\hosts"
Log "mapping name in Windows hosts: $winHosts"
try {
    $lines = @()
    if (Test-Path $winHosts) {
        $lines = Get-Content -Path $winHosts | Where-Object { $_ -notmatch "(^|\s)$([regex]::Escape($Domain))\s*$" }
    }
    $lines += "$RegistryIp $Domain"
    Set-Content -Path $winHosts -Value $lines -Encoding ascii
} catch {
    Warn "could not write $winHosts ($($_.Exception.Message)). Re-run PowerShell as Administrator, or add this line manually:"
    Warn "    $RegistryIp $Domain"
}

# --- verify (non-fatal): TLS + CA trust against the real IP ---
Log "verifying TLS + CA trust against ${RegistryIp}:${Port} ..."
$verify = & wsl -d $Distro -u root -- bash -c "command -v curl >/dev/null 2>&1 && curl -fsS --cacert '$CertsD/ca.crt' --resolve '${HostPort}:${RegistryIp}' 'https://${HostPort}/v2/' >/dev/null 2>&1 && echo OK || echo FAIL"
if ($verify -match 'OK') {
    Log "OK — daemon will trust https://$HostPort (pull no longer x509-fails)"
} else {
    Warn "could not reach https://$HostPort at $RegistryIp (registry down / wrong IP / firewall). CA + hosts are still installed."
}

Write-Host ""
Log "done. Next, in this machine's Bridge:"
Log "  Images page -> switch to TLS mode -> import this CA (shield icon)"
Log "              -> set registryUrl = $HostPort"
