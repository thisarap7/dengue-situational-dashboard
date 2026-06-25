# watch_and_push.ps1
# ------------------------------------------------------------------
# Watches this folder and auto-commits + pushes new dengue PDFs to
# GitHub (which auto-redeploys the Streamlit app). Run with -Once for a
# single manual sync; otherwise it runs continuously as a folder watcher.
#
# Safety:
#   * Stages ONLY *.pdf  (never the posters/screenshots/working docs).
#   * Pulls --rebase before pushing, so it never clashes with the
#     auto-fetch bot's commits.
#   * Logs to auto_sync.log (git-ignored).
# ------------------------------------------------------------------
param([switch]$Once)

$ErrorActionPreference = 'Continue'
$repo = $PSScriptRoot
$log  = Join-Path $repo 'auto_sync.log'

function Write-Log($msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $log -Value $line
}

function Sync-Repo {
    Set-Location -LiteralPath $repo
    git add -- "*.pdf" 2>&1 | Out-Null
    $staged = (git diff --cached --name-only) -join ', '
    if ([string]::IsNullOrWhiteSpace($staged)) { return }   # nothing new

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
    git commit -m "Auto-sync: dengue PDF update ($stamp)" 2>&1 | Out-Null

    # Incorporate any auto-fetch-bot commits first, then push.
    git pull --rebase origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        git rebase --abort 2>&1 | Out-Null
        Write-Log "ERROR: pull --rebase failed; aborted, will retry on next change."
        return
    }
    $out = git push origin main 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Log "Pushed: $staged" }
    else { Write-Log "ERROR pushing: $out" }
}

if ($Once) {
    Write-Log 'Manual one-off sync (-Once)'
    Sync-Repo
    return
}

# Single-instance guard: if a watcher is already running, exit quietly.
$createdNew = $false
$script:mutex = New-Object System.Threading.Mutex($true, 'DengueAutoSyncWatcher', [ref]$createdNew)
if (-not $createdNew) { Write-Log 'Another watcher already running; exiting.'; return }

Write-Log "Watcher started on $repo"
Sync-Repo            # catch up anything added while the watcher was off

$fsw = New-Object System.IO.FileSystemWatcher
$fsw.Path = $repo
$fsw.Filter = '*.pdf'
$fsw.IncludeSubdirectories = $false
$fsw.NotifyFilter = [System.IO.NotifyFilters]::FileName -bor [System.IO.NotifyFilters]::LastWrite -bor [System.IO.NotifyFilters]::Size

$types = [System.IO.WatcherChangeTypes]::Created -bor [System.IO.WatcherChangeTypes]::Changed -bor [System.IO.WatcherChangeTypes]::Renamed

while ($true) {
    $r = $fsw.WaitForChanged($types, 60000)
    if ($r.TimedOut) { continue }
    Start-Sleep -Seconds 8       # let the copy / OneDrive write finish and coalesce bursts
    try { Sync-Repo } catch { Write-Log "ERROR in Sync-Repo: $_" }
}
