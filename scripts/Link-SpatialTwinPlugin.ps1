param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$PluginSource
)
$ErrorActionPreference = 'Stop'
$projectFile = (Get-Item -LiteralPath $Project).FullName
if ([IO.Path]::GetExtension($projectFile) -ne '.uproject') { throw 'Project must be a .uproject file' }
$source = (Get-Item -LiteralPath $PluginSource).FullName
if (-not (Test-Path -LiteralPath (Join-Path $source 'UnrealSpatialTwin.uplugin') -PathType Leaf)) {
    throw 'PluginSource must contain UnrealSpatialTwin.uplugin'
}
$plugins = Join-Path ([IO.Path]::GetDirectoryName($projectFile)) 'Plugins'
$destination = Join-Path $plugins 'UnrealSpatialTwin'
if (Test-Path -LiteralPath $destination) {
    $existing = Get-Item -LiteralPath $destination -Force
    if ($existing.FullName -ne $source -and
        ($existing.LinkType -ne 'Junction' -or [IO.Path]::GetFullPath([string]$existing.Target) -ne $source)) {
        throw 'Existing plugin destination differs; preserve it and choose the correct source'
    }
} else {
    New-Item -ItemType Directory -Path $plugins -Force | Out-Null
    New-Item -ItemType Junction -Path $destination -Target $source | Out-Null
}
if ((Get-FileHash -LiteralPath (Join-Path $destination 'UnrealSpatialTwin.uplugin')).Hash -ne
    (Get-FileHash -LiteralPath (Join-Path $source 'UnrealSpatialTwin.uplugin')).Hash) { throw 'Descriptor mismatch' }
@{ project = $projectFile; plugin = $destination; source = $source; state = 'LINKED' } | ConvertTo-Json -Compress
