# Push the bot to the Oracle server and restart it. Run from the PC:
#   powershell -File deploy\deploy.ps1                    # code update + restart
#   powershell -File deploy\deploy.ps1 -Layout preview    # also show layout changes
#   powershell -File deploy\deploy.ps1 -Layout apply      # also apply them
#   powershell -File deploy\deploy.ps1 -FirstTime         # first install on a new server
# Needs DEPLOY_HOST (ubuntu@<ip>) and DEPLOY_KEY (path to the .key file) in .env.
param([ValidateSet("", "preview", "apply")][string]$Layout = "", [switch]$ArchiveExtras, [switch]$FirstTime)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$' } | ForEach-Object {
    $envVars[$Matches[1]] = $Matches[2].Trim()
}
$target = $envVars["DEPLOY_HOST"]; $key = $envVars["DEPLOY_KEY"]
if (-not $target -or -not $key) { throw "Set DEPLOY_HOST and DEPLOY_KEY in .env" }

$tgz = Join-Path $env:TEMP "asbot.tgz"
tar -czf $tgz --exclude=.venv --exclude=.env --exclude=logs --exclude=state --exclude=__pycache__ --exclude=.git --exclude=*.key -C $root .
if ($LASTEXITCODE -ne 0) { throw "packing failed" }
scp -i $key -o StrictHostKeyChecking=accept-new $tgz "${target}:/tmp/asbot.tgz"
if ($LASTEXITCODE -ne 0) { throw "upload failed" }

if ($FirstTime) {
    # fresh VM: unpack, send .env, run the one-time setup (installs Python, the service, starts it)
    scp -i $key "$root\.env" "${target}:/tmp/asbot.env"
    ssh -i $key $target ("rm -rf /tmp/asbot-src && mkdir /tmp/asbot-src && tar -xzf /tmp/asbot.tgz -C /tmp/asbot-src && " +
        "mv /tmp/asbot.env /tmp/asbot-src/.env && cd /tmp/asbot-src && sudo bash deploy/server_setup.sh && rm -rf /tmp/asbot-src")
    if ($LASTEXITCODE -ne 0) { throw "first-time setup failed" }
    return
}

$remote = "sudo tar -xzf /tmp/asbot.tgz -C /opt/asbot && sudo chown -R asbot:asbot /opt/asbot && " +
          "sudo -u asbot /opt/asbot/.venv/bin/pip install -q -r /opt/asbot/requirements.txt && " +
          "sudo systemctl restart asbot && sleep 4 && systemctl is-active asbot"
if ($Layout) {
    $flag = if ($ArchiveExtras) { " --archive-extras" } else { "" }
    $remote += " && cd /opt/asbot && sudo -u asbot .venv/bin/python layout.py $Layout$flag"
}
ssh -i $key $target $remote
if ($LASTEXITCODE -ne 0) { throw "remote step failed" }
