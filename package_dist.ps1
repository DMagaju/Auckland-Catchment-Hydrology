<#
.SYNOPSIS
    Builds the clean, minimal distribution ZIP for colleagues/external users
    (Distribution 3 in ../plugindistribution.md).

.DESCRIPTION
    Copies only the plugin runtime files (algorithms, UI, metadata.txt,
    icons, top-level .py modules) into a single top-level folder named
    AucklandCatchmentHydrology, then zips it. No scripts, no .md files,
    no .git/.gitignore, no tests, no existing zips.

    Run from the plugin source folder:
        .\package_dist.ps1
#>

$ErrorActionPreference = 'Stop'

$PluginDir  = $PSScriptRoot
$PluginName = 'AucklandCatchmentHydrology'

# Read version= from metadata.txt so the zip name always matches the release.
$metaLine = Get-Content (Join-Path $PluginDir 'metadata.txt') | Where-Object { $_ -match '^version=' }
$Version  = ($metaLine -split '=', 2)[1].Trim()
if (-not $Version) { throw "Could not read version= from metadata.txt" }

$ZipName   = "$PluginName-$Version.zip"
$ZipPath   = Join-Path $PluginDir $ZipName
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("dist_${PluginName}_" + [System.Guid]::NewGuid().ToString('N'))
$StageDir  = Join-Path $StageRoot $PluginName

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

# Excluded (relative-path) prefixes / patterns, per plugindistribution.md
# Distribution 3 rules plus this release's own exclude list.
$ExcludeDirNames  = @('.git', '__pycache__', 'tests')
$ExcludeExtras    = @('.gitignore')
$ExcludeFilePatterns = @('*.pyc', '*.pyo', '*.pyd', '*.zip', '*.md', '*.bat', '*.ps1')

Write-Host "Staging $PluginName $Version into $StageDir ..."

Get-ChildItem -Path $PluginDir -Recurse -Force | ForEach-Object {
    $item = $_
    $rel  = $item.FullName.Substring($PluginDir.Length).TrimStart('\', '/')
    if ($rel -eq '') { return }

    $relParts = $rel -split '[\\/]'
    if (($relParts | Where-Object { $_ -in $ExcludeDirNames }).Count -gt 0) { return }
    if ($relParts[0] -in $ExcludeExtras) { return }

    if (-not $item.PSIsContainer) {
        foreach ($pattern in $ExcludeFilePatterns) {
            if ($item.Name -like $pattern) { return }
        }
    }

    $destPath = Join-Path $StageDir $rel
    if ($item.PSIsContainer) {
        New-Item -ItemType Directory -Path $destPath -Force | Out-Null
    } else {
        $destParent = Split-Path $destPath -Parent
        if (-not (Test-Path $destParent)) {
            New-Item -ItemType Directory -Path $destParent -Force | Out-Null
        }
        Copy-Item -Path $item.FullName -Destination $destPath -Force
    }
}

Write-Host "Zipping to $ZipPath ..."
Compress-Archive -Path $StageDir -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item $StageRoot -Recurse -Force

Write-Host "Done: $ZipPath"
