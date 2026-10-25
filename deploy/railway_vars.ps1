# Copies the bot settings from .env into Railway without printing any values.
# Run from the repo folder after `railway link`:  & .\deploy\railway_vars.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$keys = @("DISCORD_TOKEN", "GUILD_ID", "ROBLOX_OPENCLOUD_KEY")
$vals = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$' } | ForEach-Object {
    $vals[$Matches[1]] = $Matches[2].Trim()
}
foreach ($k in $keys) {
    if ($vals[$k]) {
        # value goes as an argument: piping into stdin from Windows PowerShell adds a BOM
        railway variable set "$k=$($vals[$k])" --skip-deploys | Out-Null
        Write-Host "set $k"
    }
}
railway variable set ASBOT_DATA=/data --skip-deploys | Out-Null
Write-Host "set ASBOT_DATA=/data"
railway variable list --json | ConvertFrom-Json | Get-Member -MemberType NoteProperty | ForEach-Object { "on Railway: " + $_.Name }
