<#
.SYNOPSIS
  Refresh the PM1 SolMan Focused Build SSO cookie (SAML/IAS) via a browser.

.DESCRIPTION
  Thin wrapper over pm1_refresh.py. Opens a persistent Edge profile, completes
  SAML/IAS SSO (silent once warm; interactive on first login), and writes
  %USERPROFILE%\.vsp\cookies-pm1.txt. The MCP server (solman-fb-pm1) is
  cookie-only and never opens a browser; run this when it reports the session
  expired. Analogous to vsp-refresh.ps1 (but PM1 uses SAML/IAS, not SPNego, so
  vsp.exe cannot be used here).

.EXAMPLE
  ./pm1-refresh.ps1
.EXAMPLE
  ./pm1-refresh.ps1 -Timeout 120 -Headless
#>
[CmdletBinding()]
param(
  [int]$Timeout = 300,
  [switch]$Headless
)
$ErrorActionPreference = 'Stop'
$py = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$script = Join-Path $PSScriptRoot 'pm1_refresh.py'
$args = @($script, '--timeout', $Timeout)
if ($Headless) { $args += '--headless' }
& $py @args
exit $LASTEXITCODE
