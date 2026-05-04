param(
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
$frontendRoot = Join-Path $projectRoot 'frontend'
$runtimeDir = Join-Path $projectRoot '.cache\runtime'
$logDir = Join-Path $runtimeDir 'logs'
$runtimeStatePath = Join-Path $runtimeDir 'launcher.json'
$apiPort = 41874
$appUrl = "http://127.0.0.1:$apiPort"
$healthUrl = "$appUrl/api/health"
$systemStatusUrl = "$appUrl/api/system/status"
$systemIdentityUrl = "$appUrl/api/system/identity"
$devPort = 41873
$rootHashBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant()))
$rootHash = ([BitConverter]::ToString($rootHashBytes)).Replace('-', '').Substring(0, 16)
$launchMutex = New-Object System.Threading.Mutex($false, "Local\PolymarketWeatherTool-$rootHash")
$launchLockTaken = $false

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null

function Test-HttpOk {
  param([Parameter(Mandatory = $true)][string]$Url)

  try {
    $payload = Invoke-RestMethod -Uri $Url -TimeoutSec 1
    return $payload.ok -eq $true
  }
  catch {
    return $false
  }
}

function Test-ManagedApi {
  try {
    $payload = Invoke-RestMethod -Uri $systemIdentityUrl -TimeoutSec 1
    return $payload.ok -eq $true -and $payload.root -eq $projectRoot
  }
  catch {
    return $false
  }
}

function Get-PortProcessIds {
  param([Parameter(Mandatory = $true)][int]$Port)

  try {
    $pattern = "127.0.0.1:$Port"
    @(
      cmd.exe /c "netstat -ano -p tcp" |
        Select-String -SimpleMatch $pattern |
        ForEach-Object {
          $columns = ($_.Line -split '\s+') | Where-Object { $_ }
          if ($columns.Count -ge 5 -and $columns[3] -eq 'LISTENING') {
            [int]$columns[4]
          }
        } |
        Select-Object -Unique
    )
  }
  catch {
    @()
  }
}

function Get-ProcessCommandLine {
  param([Parameter(Mandatory = $true)][int]$ProcessId)

  try {
    return (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
  }
  catch {
    return ''
  }
}

function Test-ProcessRunning {
  param([Parameter(Mandatory = $true)][int]$ProcessId)

  try {
    $null = Get-Process -Id $ProcessId -ErrorAction Stop
    return $true
  }
  catch {
    return $false
  }
}

function Get-ProcessAgeSeconds {
  param([Parameter(Mandatory = $true)][int]$ProcessId)

  try {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    return [int][Math]::Floor(((Get-Date) - $process.StartTime).TotalSeconds)
  }
  catch {
    return $null
  }
}

function Read-RuntimeState {
  if (-not (Test-Path -LiteralPath $runtimeStatePath)) {
    return $null
  }

  try {
    return Get-Content -Raw -LiteralPath $runtimeStatePath -Encoding UTF8 | ConvertFrom-Json
  }
  catch {
    return $null
  }
}

function Get-LatestWriteTimeUtc {
  param([Parameter(Mandatory = $true)][string[]]$Paths)

  $latest = [datetime]::MinValue
  foreach ($path in $Paths) {
    if (-not (Test-Path -LiteralPath $path)) {
      continue
    }

    $entry = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
    if (-not $entry) {
      continue
    }

    $items = if ($entry.PSIsContainer) {
      @(Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue)
    }
    else {
      @($entry)
    }

    foreach ($item in $items) {
      if ($item.LastWriteTimeUtc -gt $latest) {
        $latest = $item.LastWriteTimeUtc
      }
    }
  }

  return $latest
}

function Reclaim-StaleLauncher {
  param([int]$MaxAgeSeconds = 90)

  $runtime = Read-RuntimeState
  if (-not $runtime -or -not $runtime.launcher_pid) {
    return $false
  }

  $launcherPid = [int]$runtime.launcher_pid
  if ($launcherPid -le 0 -or $launcherPid -eq $PID) {
    return $false
  }

  if (-not (Test-ProcessRunning -ProcessId $launcherPid)) {
    return $false
  }

  $ageSeconds = Get-ProcessAgeSeconds -ProcessId $launcherPid
  if ($null -eq $ageSeconds -or $ageSeconds -lt $MaxAgeSeconds) {
    return $false
  }

  if (-not ((Test-ManagedApi) -or (Test-HttpOk -Url $healthUrl))) {
    return $false
  }

  Stop-Process -Id $launcherPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  return -not (Test-ProcessRunning -ProcessId $launcherPid)
}

function Get-ProjectServiceProcesses {
  $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  foreach ($process in $processes) {
    if (-not $process.ProcessId -or $process.ProcessId -eq $PID -or -not $process.CommandLine) {
      continue
    }

    $commandLine = [string]$process.CommandLine
    $inProject = $commandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $isBackend = $commandLine -match 'polymarket_weather_tool\.server'
    $isOldFrontend = $inProject -and (
      $commandLine -match 'npm(\.cmd)?(.+)?\srun\s+dev' -or
      (
        $commandLine -match '\bvite(\.cmd)?\b' -and
        ($commandLine -match '--port(=|\s+)41873' -or $commandLine -match '\sdev(\s|$)')
      )
    )

    if ($isBackend -or $isOldFrontend) {
      $process
    }
  }
}

function Stop-StaleProjectServiceProcesses {
  param([int[]]$KeepProcessIds = @())

  $keep = @($KeepProcessIds | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique)
  foreach ($process in Get-ProjectServiceProcesses) {
    if ($keep -contains [int]$process.ProcessId) {
      continue
    }
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Stop-RuntimeStateProcesses {
  $runtime = Read-RuntimeState
  if (-not $runtime) {
    return
  }

  $keys = @('api_process_pid', 'api_port_pid', 'frontend_process_pid', 'frontend_port_pid')
  foreach ($key in $keys) {
    try {
      $processId = [int]$runtime.$key
    }
    catch {
      continue
    }
    if ($processId -gt 0 -and $processId -ne $PID) {
      Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
  }
}

function Wait-PortsReleased {
  param(
    [Parameter(Mandatory = $true)][int[]]$Ports,
    [int]$TimeoutSeconds = 8
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $busy = @()
    foreach ($port in $Ports) {
      $busy += @(Get-PortProcessIds -Port $port)
    }
    if (-not $busy) {
      return
    }
    Start-Sleep -Milliseconds 300
  }
}

function Stop-ProjectPortListeners {
  param(
    [Parameter(Mandatory = $true)][int[]]$Ports,
    [switch]$IgnoreForeign
  )

  foreach ($port in $Ports) {
    foreach ($processId in Get-PortProcessIds -Port $port) {
      if ($processId -and $processId -ne $PID) {
        $commandLine = Get-ProcessCommandLine -ProcessId $processId
        if ($commandLine -like "*$projectRoot*" -or $commandLine -like '*polymarket_weather_tool.server*') {
          Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
        elseif ($IgnoreForeign) {
          continue
        }
        else {
          throw "Port $port is already used by another program. Please close that program first, then reopen Polymarket Weather Tool."
        }
      }
    }
  }
}

function Wait-HttpOk {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$TimeoutSeconds = 25
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk -Url $Url) {
      return $true
    }
    Start-Sleep -Milliseconds 150
  }

  throw "$Name did not respond within $TimeoutSeconds seconds: $Url"
}

function Start-HiddenPowerShell {
  param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string]$LogName
  )

  $escapedTitle = $Title.Replace("'", "''")
  $escapedWorkingDirectory = $WorkingDirectory.Replace("'", "''")
  $outputLog = (Join-Path $logDir "$LogName.out.log").Replace("'", "''")
  $errorLog = (Join-Path $logDir "$LogName.err.log").Replace("'", "''")
  $fullCommand = @"
`$Host.UI.RawUI.WindowTitle = '$escapedTitle'
Set-Location -LiteralPath '$escapedWorkingDirectory'
try {
  & {
    $Command
  } *> '$outputLog'
}
catch {
  `$_.Exception.ToString() | Out-File -LiteralPath '$errorLog' -Append -Encoding UTF8
  throw
}
"@
  $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($fullCommand))

  Start-Process -FilePath 'powershell.exe' `
    -WindowStyle Hidden `
    -WorkingDirectory $WorkingDirectory `
    -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encodedCommand) `
    -PassThru
}

function Start-HiddenPythonServer {
  $pythonCommand = Get-Command 'pythonw.exe' -ErrorAction SilentlyContinue
  $pythonPath = if ($pythonCommand) { $pythonCommand.Source } else { 'python.exe' }
  $previousPythonPath = $env:PYTHONPATH
  $env:PYTHONPATH = Join-Path $projectRoot 'src'
  try {
    return Start-Process -FilePath $pythonPath `
      -WindowStyle Hidden `
      -WorkingDirectory $projectRoot `
      -ArgumentList @('-m', 'polymarket_weather_tool.server', '--host', '127.0.0.1', '--port', "$apiPort") `
      -PassThru
  }
  finally {
    $env:PYTHONPATH = $previousPythonPath
  }
}

function Find-EdgePath {
  $candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    'msedge.exe'
  )

  foreach ($candidate in $candidates) {
    if ($candidate -eq 'msedge.exe' -or (Test-Path -LiteralPath $candidate)) {
      return $candidate
    }
  }
}

function Start-ManagerWindow {
  if ($NoBrowser) {
    return $null
  }

  $existingBrowserPid = Focus-ExistingManagerWindow
  if ($existingBrowserPid) {
    return [pscustomobject]@{Id = $existingBrowserPid}
  }

  $edgePath = Find-EdgePath
  if ($edgePath) {
    $profileDir = Join-Path $runtimeDir 'edge-profile'
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    return Start-Process -FilePath $edgePath -ArgumentList @(
      "--user-data-dir=$profileDir",
      "--app=$appUrl",
      '--no-first-run',
      '--disable-features=Translate'
    ) -PassThru
  }

  Start-Process $appUrl | Out-Null
  return $null
}

function Focus-ExistingManagerWindow {
  $runtime = Read-RuntimeState
  $candidateIds = @()

  if ($runtime -and $runtime.browser_pid) {
    $candidateIds += [int]$runtime.browser_pid
  }

  $shell = New-Object -ComObject WScript.Shell
  foreach ($candidateId in @($candidateIds | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique)) {
    if (-not (Test-ProcessRunning -ProcessId $candidateId)) {
      continue
    }
    try {
      if ($shell.AppActivate([int]$candidateId)) {
        return [int]$candidateId
      }
    }
    catch {
      # Fall back to discovering profile-bound Edge processes.
    }
  }

  return 0
}

function Save-RuntimeState {
  param(
    [int]$ApiProcessId,
    [int]$BrowserProcessId
  )

  $runtime = Read-RuntimeState
  $apiPortPid = if ($ApiProcessId -gt 0) {
    $ApiProcessId
  }
  elseif ($runtime -and $runtime.api_port_pid) {
    [int]$runtime.api_port_pid
  }
  else {
    0
  }

  $state = [ordered]@{
    app = 'polymarket-weather-tool'
    launched_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    project_root = $projectRoot
    frontend_url = $appUrl
    launcher_pid = $PID
    api_process_pid = $ApiProcessId
    api_port_pid = $apiPortPid
    browser_pid = $BrowserProcessId
  }

  $json = $state | ConvertTo-Json -Depth 4
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($runtimeStatePath, $json, $utf8NoBom)
}

function Stop-RuntimeBrowserWindow {
  $runtime = Read-RuntimeState
  if (-not $runtime -or -not $runtime.browser_pid) {
    return
  }

  try {
    $browserProcessId = [int]$runtime.browser_pid
  }
  catch {
    return
  }

  if ($browserProcessId -gt 0 -and $browserProcessId -ne $PID) {
    Stop-Process -Id $browserProcessId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
  }
}

function Test-FrontendBuildStale {
  $indexPath = Join-Path $frontendRoot 'dist\index.html'
  if (-not (Test-Path -LiteralPath $indexPath)) {
    return $true
  }

  $buildTime = (Get-Item -LiteralPath $indexPath).LastWriteTimeUtc
  $sourceTime = Get-LatestWriteTimeUtc -Paths @(
    (Join-Path $frontendRoot 'src'),
    (Join-Path $frontendRoot 'public'),
    (Join-Path $frontendRoot 'index.html'),
    (Join-Path $frontendRoot 'package.json'),
    (Join-Path $frontendRoot 'vite.config.ts')
  )

  return $sourceTime -gt $buildTime
}

function Ensure-FrontendBuild {
  $needsBuild = Test-FrontendBuildStale
  if (-not $needsBuild) {
    return $false
  }

  Push-Location $frontendRoot
  try {
    if (-not (Test-Path -LiteralPath '.\node_modules')) {
      npm ci *> (Join-Path $logDir 'npm-ci.out.log')
    }
    npm run build *> (Join-Path $logDir 'frontend-build.out.log')
  }
  finally {
    Pop-Location
  }

  return $true
}

try {
  $launchLockTaken = $launchMutex.WaitOne([TimeSpan]::FromSeconds(5))
  if (-not $launchLockTaken -and (Reclaim-StaleLauncher -MaxAgeSeconds 90)) {
    $launchLockTaken = $launchMutex.WaitOne([TimeSpan]::FromSeconds(5))
  }
  if (-not $launchLockTaken) {
    throw 'Polymarket Weather Tool is already starting. Please wait a few seconds and try again.'
  }

  $frontendRebuilt = Ensure-FrontendBuild

  if ($frontendRebuilt) {
    Stop-RuntimeBrowserWindow
  }

  Stop-RuntimeStateProcesses
  Stop-ProjectPortListeners -Ports @($apiPort)
  Stop-ProjectPortListeners -Ports @($devPort) -IgnoreForeign
  Wait-PortsReleased -Ports @($apiPort) -TimeoutSeconds 8

  $apiProcess = Start-HiddenPythonServer

  Save-RuntimeState -ApiProcessId $(if ($apiProcess) { $apiProcess.Id } else { 0 }) -BrowserProcessId 0

  Wait-HttpOk -Url $healthUrl -Name 'Polymarket Weather Tool' -TimeoutSeconds 25 | Out-Null

  $browserProcess = Start-ManagerWindow

  Save-RuntimeState `
    -ApiProcessId $(if ($apiProcess) { $apiProcess.Id } else { 0 }) `
    -BrowserProcessId $(if ($browserProcess) { $browserProcess.Id } else { 0 })
}
catch {
  $message = "$(Get-Date -Format o) $($_.Exception.Message)"
  Add-Content -LiteralPath (Join-Path $logDir 'launcher.err.log') -Value $message
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
      "$($_.Exception.Message)`n`nLog folder:`n$logDir",
      'Polymarket Weather Tool failed to start',
      'OK',
      'Error'
    ) | Out-Null
  }
  catch {
    # The launcher can still fail safely even if the desktop message box is unavailable.
  }
}
finally {
  if ($launchLockTaken) {
    $launchMutex.ReleaseMutex() | Out-Null
  }
  $launchMutex.Dispose()
}
