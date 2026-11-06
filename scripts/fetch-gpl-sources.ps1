param([string]$Output = 'dist-portable/sources')
# Downloads the source archives of the GPL programs shipped in the portable package, so that they
# can be hosted next to the zip. Versions are read from the programs themselves.
# Writes SHA256SUMS.txt. Run it only when you intend to publish the package.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$tools = Get-Content -Raw -LiteralPath '.local/tools.json' | ConvertFrom-Json
$ffmpegVersion = (& $tools.ffmpeg -hide_banner -version | Select-Object -First 1)
$commit = [regex]::Match($ffmpegVersion, 'git-([0-9a-f]+)').Groups[1].Value
$mkv = [regex]::Match((& $tools.mkvmerge --version | Select-Object -First 1), 'v([0-9.]+)').Groups[1].Value
$legacy = if ($tools.mkvmergeLegacy) { [regex]::Match((& $tools.mkvmergeLegacy --version | Select-Object -First 1), 'v([0-9.]+)').Groups[1].Value } else { '' }
if (!$commit -or !$mkv) { throw 'Cannot determine FFmpeg commit or mkvmerge version.' }
New-Item -ItemType Directory -Force $Output | Out-Null
$files = @(
    @{ Name = "FFmpeg-$commit.tar.gz"; Url = "https://github.com/FFmpeg/FFmpeg/archive/$commit.tar.gz" },
    @{ Name = "mkvtoolnix-$mkv.tar.xz"; Url = "https://mkvtoolnix.download/sources/mkvtoolnix-$mkv.tar.xz" }
)
if ($legacy) { $files += @{ Name = "mkvtoolnix-$legacy.tar.xz"; Url = "https://mkvtoolnix.download/sources/mkvtoolnix-$legacy.tar.xz" } }
$sums = foreach ($file in $files) {
    $path = Join-Path $Output $file.Name
    if (!(Test-Path -LiteralPath $path)) { Invoke-WebRequest -Uri $file.Url -OutFile $path -UseBasicParsing }
    '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower(), $file.Name
    Write-Output ('{0} ({1:N1} MB) from {2}' -f $file.Name, ((Get-Item -LiteralPath $path).Length / 1MB), $file.Url) | Out-Host
}
Set-Content -LiteralPath (Join-Path $Output 'SHA256SUMS.txt') -Value $sums -Encoding ASCII
