param(
  [string[]]$ShortcutNames = @('Finder.lnk')
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
$launcherVbs = Join-Path $scriptDir 'Open-PolymarketWeather.vbs'
$iconPath = Join-Path $projectRoot 'frontend\public\polymarket-desktop.ico'
$targetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$normalShortcutName = (
  -join ([char[]](0x6253, 0x5F00))
) + ' Polymarket ' + (
  -join ([char[]](0x5929, 0x6C14, 0x5206, 0x6790, 0x5DE5, 0x5177))
) + '.lnk'

$desktopCandidates = @(
  [pscustomobject]@{Path = (Join-Path $env:USERPROFILE 'OneDrive\Desktop'); Required = $true},
  [pscustomobject]@{Path = (Join-Path $env:USERPROFILE 'Desktop'); Required = $true},
  [pscustomobject]@{Path = 'C:\Users\Public\Desktop'; Required = $false}
) | Where-Object { Test-Path -LiteralPath $_.Path }

if (-not (Test-Path -LiteralPath $launcherVbs)) {
  throw "Launcher script not found: $launcherVbs"
}

if (-not (Test-Path -LiteralPath $iconPath)) {
  throw "Desktop icon not found: $iconPath"
}

if (-not $desktopCandidates) {
  throw 'No desktop folder was found for the current Windows profile.'
}

$shell = New-Object -ComObject WScript.Shell
$updated = @()

foreach ($desktopDir in $desktopCandidates) {
  $existingPolymarketShortcuts = @(
    Get-ChildItem -LiteralPath $desktopDir.Path -Filter '*.lnk' -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like '*Polymarket*' } |
      ForEach-Object { $_.Name }
  )
  $desktopShortcutNames = @($ShortcutNames + $normalShortcutName + $existingPolymarketShortcuts) |
    Where-Object { $_ } |
    Select-Object -Unique

  foreach ($shortcutName in $desktopShortcutNames) {
    $shortcutPath = Join-Path $desktopDir.Path $shortcutName
    try {
      $shortcut = $shell.CreateShortcut($shortcutPath)
      $shortcut.TargetPath = $targetPath
      $shortcut.Arguments = '"' + $launcherVbs + '"'
      $shortcut.WorkingDirectory = $projectRoot
      $shortcut.IconLocation = $iconPath + ',0'
      $shortcut.Description = 'Open the latest local Polymarket weather analysis console.'
      $shortcut.Save()

      $updated += [pscustomobject]@{
        Shortcut = $shortcutPath
        Target = $targetPath
        Arguments = $shortcut.Arguments
        Icon = $shortcut.IconLocation
        Status = 'updated'
      }
    }
    catch {
      if ($desktopDir.Required) {
        throw
      }
      $updated += [pscustomobject]@{
        Shortcut = $shortcutPath
        Target = $targetPath
        Arguments = '"' + $launcherVbs + '"'
        Icon = $iconPath + ',0'
        Status = 'skipped-no-permission'
      }
    }
  }
}

$updated | Format-Table -AutoSize
