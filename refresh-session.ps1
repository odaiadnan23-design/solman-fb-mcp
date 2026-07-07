<#
.SYNOPSIS
  Refresh the SolMan Focused Build SSO cookie (SAML/IAS) via a browser.

.DESCRIPTION
  Thin wrapper over refresh_session.py. Opens a persistent Edge profile, completes
  SAML/IAS SSO (silent once warm; interactive on first login), and writes
  %USERPROFILE%\.solman-mcp\cookies.txt. The MCP server is cookie-only and never
  opens a browser; run this when it reports the session expired. ADT-based SSO
  cookie tools can't be reused here because a Focused Build system usually has
  its ADT node closed.

.EXAMPLE
  ./refresh-session.ps1
.EXAMPLE
  ./refresh-session.ps1 -Timeout 120 -Headless
#>
[CmdletBinding()]
param(
  [int]$Timeout = 300,
  [switch]$Headless
)
$ErrorActionPreference = 'Stop'
$py = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$script = Join-Path $PSScriptRoot 'refresh_session.py'
$args = @($script, '--timeout', $Timeout)
if ($Headless) { $args += '--headless' }
& $py @args
exit $LASTEXITCODE
