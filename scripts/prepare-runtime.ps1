param(
    [Parameter(Mandatory=$true)][string]$PythonRoot,
    [Parameter(Mandatory=$true)][string]$FfmpegDirectory,
    [Parameter(Mandatory=$true)][string]$Mkvmerge
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runtime/python'
$sitePackages = Join-Path $PythonRoot 'Lib/site-packages'
foreach ($dependency in @((Join-Path $PythonRoot 'python.exe'), (Join-Path $sitePackages 'numpy'), (Join-Path $FfmpegDirectory 'ffmpeg.exe'), (Join-Path $FfmpegDirectory 'ffprobe.exe'), $Mkvmerge)) {
    if (!(Test-Path -LiteralPath $dependency)) { throw "Dependency is missing: $dependency" }
}
if (Test-Path -LiteralPath $runtimeRoot) { throw 'Runtime already exists. Use the existing copy; do not overwrite a running runtime.' }
New-Item -ItemType Directory -Force (Join-Path $runtimeRoot 'Lib/site-packages') | Out-Null
Get-ChildItem -LiteralPath $PythonRoot -File | Where-Object { $_.Name -match '^(python(3|312)?\.dll|python\.exe|vcruntime.*\.dll|LICENSE\.txt)$' } | Copy-Item -Destination $runtimeRoot
Copy-Item -LiteralPath (Join-Path $PythonRoot 'DLLs') -Destination (Join-Path $runtimeRoot 'DLLs') -Recurse
Get-ChildItem -LiteralPath (Join-Path $PythonRoot 'Lib') | Where-Object { $_.Name -notin @('site-packages','test','tkinter','idlelib','ensurepip','__pycache__') } | Copy-Item -Destination (Join-Path $runtimeRoot 'Lib') -Recurse
foreach ($package in @('numpy', 'numpy.libs')) { Copy-Item -LiteralPath (Join-Path $sitePackages $package) -Destination (Join-Path $runtimeRoot "Lib/site-packages/$package") -Recurse }
Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'numpy-*.dist-info' | Copy-Item -Destination (Join-Path $runtimeRoot 'Lib/site-packages') -Recurse
New-Item -ItemType Directory -Force (Join-Path $projectRoot '.local') | Out-Null
$configuration = @{ python=(Join-Path $runtimeRoot 'python.exe'); ffmpeg=(Join-Path $FfmpegDirectory 'ffmpeg.exe'); ffprobe=(Join-Path $FfmpegDirectory 'ffprobe.exe'); mkvmerge=$Mkvmerge } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $projectRoot '.local/tools.json'), $configuration, [System.Text.UTF8Encoding]::new($false))
& (Join-Path $runtimeRoot 'python.exe') -I -c 'import sys,numpy; print(sys.executable); print(numpy.__version__)'
if ($LASTEXITCODE) { throw 'Local Python runtime verification failed.' }
