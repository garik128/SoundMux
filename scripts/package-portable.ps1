param([string]$Output = 'dist-portable', [switch]$NoZip, [string]$SourcesUrl = '', [string]$Contact = 'garik123@perevodman.com')
# -SourcesUrl: page of your site where copies of the GPL source archives are hosted.
# -Contact: e-mail or page where users can request the source. Give at least one before publishing.
# Builds a release SoundMux.exe and assembles a self-contained folder (and zip) for another PC.
# Layout is what src-tauri/src/main.rs expects in release mode: resources next to the EXE.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:CARGO_HOME = Join-Path $projectRoot '.tools/cargo'
$env:RUSTUP_HOME = Join-Path $projectRoot '.tools/rustup'
$env:PATH = (Join-Path $env:CARGO_HOME 'bin') + ';' + $env:PATH
$cargo = Join-Path $env:CARGO_HOME 'bin/cargo.exe'
if (!(Test-Path -LiteralPath $cargo)) { throw 'Local Rust toolchain is missing. See docs/DEVELOPMENT.md.' }
$tools = Get-Content -Raw -LiteralPath (Join-Path $projectRoot '.local/tools.json') | ConvertFrom-Json
foreach ($name in 'ffmpeg', 'ffprobe', 'mkvmerge') { if (!(Test-Path -LiteralPath $tools.$name)) { throw "Missing tool: $($tools.$name)" } }
if (!(Test-Path -LiteralPath (Join-Path $projectRoot '.runtime/python/python.exe'))) { throw 'Local Python runtime is missing. See scripts/prepare-runtime.ps1.' }

& npm.cmd run build
if ($LASTEXITCODE) { throw 'Frontend build failed.' }
# Static CRT: the EXE then needs no Visual C++ Redistributable on the other PC.
# remap-path-prefix keeps the build machine's folder names out of panic messages inside the EXE.
$env:RUSTFLAGS = "-C target-feature=+crt-static --remap-path-prefix=$projectRoot=SoundMux"
& $cargo build --release --manifest-path src-tauri/Cargo.toml --features custom-protocol
if ($LASTEXITCODE) { throw 'Release build failed.' }

$root = Join-Path $projectRoot $Output
$folder = Join-Path $root 'SoundMux'
if (Test-Path -LiteralPath $folder) { Remove-Item -LiteralPath $folder -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $folder 'bin'), (Join-Path $folder 'backend/vendor') | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'src-tauri/target/release/SoundMux.exe') -Destination $folder
Copy-Item -LiteralPath (Join-Path $projectRoot '.runtime/python') -Destination (Join-Path $folder 'runtime/python') -Recurse
foreach ($name in 'ffmpeg', 'ffprobe', 'mkvmerge') { Copy-Item -LiteralPath $tools.$name -Destination (Join-Path $folder 'bin') }
if ($tools.mkvmergeLegacy) {
    New-Item -ItemType Directory -Force (Join-Path $folder 'bin/legacy') | Out-Null
    Copy-Item -LiteralPath $tools.mkvmergeLegacy -Destination (Join-Path $folder 'bin/legacy')
}
# Every backend module, so a new one cannot be forgotten; tests and caches stay out.
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'backend') -File -Filter '*.py' | Copy-Item -Destination (Join-Path $folder 'backend')
Copy-Item -Path (Join-Path $projectRoot 'backend/vendor/*') -Destination (Join-Path $folder 'backend/vendor') -Recurse -Exclude '__pycache__', 'README.md'  # README.md names a local developer path
Get-ChildItem -LiteralPath $folder -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
$licenses = Join-Path $folder 'licenses'
New-Item -ItemType Directory -Force $licenses | Out-Null
$ffmpegHome = Split-Path (Split-Path $tools.ffmpeg)
$mkvHome = Split-Path $tools.mkvmerge
$notices = @{
    'FFmpeg-LICENSE.txt' = Join-Path $ffmpegHome 'LICENSE'
    'FFmpeg-build-info.txt' = Join-Path $ffmpegHome 'README.txt'
    'MKVToolNix-COPYING.txt' = Join-Path $mkvHome 'doc/COPYING.txt'
}
foreach ($name in $notices.Keys) {
    if (!(Test-Path -LiteralPath $notices[$name])) { throw "License file is missing: $($notices[$name])" }
    Copy-Item -LiteralPath $notices[$name] -Destination (Join-Path $licenses $name)
}
Copy-Item -LiteralPath (Join-Path $projectRoot '.runtime/python/LICENSE.txt') -Destination (Join-Path $licenses 'Python-LICENSE.txt')

# Notices for the Rust crates and JavaScript packages built into SoundMux (fails on a GPL dependency).
$metadata = Join-Path ([IO.Path]::GetTempPath()) 'soundmux-cargo-metadata.json'
& $cargo metadata --offline --format-version 1 --manifest-path src-tauri/Cargo.toml --filter-platform x86_64-pc-windows-msvc 2>$null | Out-File -Encoding utf8 -LiteralPath $metadata
& (Join-Path $projectRoot '.runtime/python/python.exe') -X utf8 -I (Join-Path $projectRoot 'scripts/collect-licenses.py') $metadata $projectRoot (Join-Path $licenses 'THIRD-PARTY-NOTICES.txt')
if ($LASTEXITCODE) { throw 'License collection failed.' }
Remove-Item -LiteralPath $metadata -Force

# Where to get the source of the GPL programs; versions come from the programs themselves.
$ffmpegVersion = ((& $tools.ffmpeg -hide_banner -version | Select-Object -First 1) -replace '^ffmpeg version ', '' -replace ' Copyright.*$', '')
$ffmpegCommit = [regex]::Match($ffmpegVersion, 'git-([0-9a-f]+)').Groups[1].Value
$mkvVersion = [regex]::Match((& $tools.mkvmerge --version | Select-Object -First 1), 'v([0-9.]+)').Groups[1].Value
$legacyVersion = if ($tools.mkvmergeLegacy) { [regex]::Match((& $tools.mkvmergeLegacy --version | Select-Object -First 1), 'v([0-9.]+)').Groups[1].Value } else { '' }
if (!$ffmpegCommit -or !$mkvVersion) { throw 'Cannot determine FFmpeg commit or mkvmerge version.' }
$sourceText = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot 'scripts/portable-source-code.txt')
$sourceText = $sourceText.Replace('{FFMPEG_VERSION}', $ffmpegVersion).Replace('{FFMPEG_COMMIT}', $ffmpegCommit).Replace('{MKV_VERSION}', $mkvVersion).Replace('{MKV_LEGACY_LINES}', $(if ($legacyVersion) { "   Запасная сборка для ранних версий Windows 10 (bin\legacy\mkvmerge.exe), версия $legacyVersion, лицензия GNU GPL v2:`r`n   Исходный код: https://mkvtoolnix.download/sources/mkvtoolnix-$legacyVersion.tar.xz`r`n" } else { '' }))
$sourceText = $sourceText.Replace('{SOURCES_URL_LINE}', $(if ($SourcesUrl) { "   Копии исходного кода, соответствующие этому комплекту, размещены здесь: $SourcesUrl`r`n" } else { '' }))
$sourceText = $sourceText.Replace('{CONTACT_LINE}', $(if ($Contact) { "   Либо запросите исходный код по адресу: $Contact`r`n" } else { '' }))
[IO.File]::WriteAllText((Join-Path $licenses 'SOURCE-CODE.txt'), $sourceText, [Text.UTF8Encoding]::new($true))
if (!$SourcesUrl -and !$Contact) { Write-Warning 'No -SourcesUrl or -Contact given: SOURCE-CODE.txt has no way to reach you. Add one before publishing.' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'scripts/portable-readme.txt') -Destination (Join-Path $folder 'README.txt')  # ASCII name: some unpackers still ignore the UTF-8 flag and garble non-ASCII entry names
Copy-Item -LiteralPath (Join-Path $projectRoot 'scripts/diagnose.bat') -Destination (Join-Path $folder 'diagnose.bat')
# Self-test of the assembled folder: the bundled worker must probe two files and build a film with the bundled mkvmerge.
# (Saving is the only step that runs mkvmerge, so a probe alone would not notice a missing or broken one.)
$testRoot = Join-Path ([IO.Path]::GetTempPath()) 'soundmux-selftest'
if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $testRoot 'cache'), (Join-Path $testRoot 'work'), (Join-Path $testRoot 'out') | Out-Null
$testMovie = Join-Path $testRoot 'movie.mkv'; $testDub = Join-Path $testRoot 'dub.wav'
& $tools.ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=160x90:rate=10:duration=3' -f lavfi -i 'sine=frequency=440:duration=3' -c:v libx264 -pix_fmt yuv420p -c:a ac3 $testMovie
& $tools.ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=330:duration=3' $testDub
$bundled = @{ python = (Join-Path $folder 'runtime/python/python.exe'); ffmpeg = (Join-Path $folder 'bin/ffmpeg.exe'); ffprobe = (Join-Path $folder 'bin/ffprobe.exe')
    mkvmerge = (Join-Path $folder 'bin/mkvmerge.exe'); cache = (Join-Path $testRoot 'cache'); work = (Join-Path $testRoot 'work'); analyzer = (Join-Path $folder 'backend/vendor/audio_sync.py') }
function Invoke-Worker($action, $payload) {
    $json = @{ action = $action; payload = $payload; config = $bundled } | ConvertTo-Json -Depth 12 -Compress
    $requestFile = Join-Path $testRoot 'request.json'
    [IO.File]::WriteAllText($requestFile, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    $lines = cmd /c ('"{0}" -I -B -X utf8 -u "{1}" < "{2}"' -f $bundled.python, (Join-Path $folder 'backend/worker.py'), $requestFile)
    $last = $lines | Where-Object { $_ -match '"type": "(result|error)"' } | Select-Object -Last 1
    if (!$last) { throw "Self-test of the portable folder: no answer to '$action'. $($lines -join ' ')" }
    $answer = $last | ConvertFrom-Json
    if ($answer.type -eq 'error') { throw "Self-test of the portable folder: '$action' failed: $($answer.message) $($answer.detail)" }
    $answer.value
}
$testFilm = Invoke-Worker 'probe' @{ path = $testMovie; kind = 'movie' }
$testSound = Invoke-Worker 'probe' @{ path = $testDub; kind = 'audio' }
# Every mkvmerge in the package must really build a film: the main one and the older one for early Windows 10.
$mkvmergeBuilds = @((Join-Path $folder 'bin/mkvmerge.exe'))
if ($tools.mkvmergeLegacy) { $mkvmergeBuilds += (Join-Path $folder 'bin/legacy/mkvmerge.exe') }
foreach ($mkv in $mkvmergeBuilds) {
    $bundled.mkvmerge = $mkv
    $label = if ($mkv -match 'legacy') { 'legacy' } else { 'main' }
    $built = Invoke-Worker 'build' @{ movie = $testFilm; audio = $testSound
        settings = @{ referenceTrack = $testFilm.metadata.recommendedTrack; externalTrack = $testSound.metadata.recommendedTrack; language = 'Русский'; title = 'self-test'; makeDefault = $true; keepMovieAudio = $true; keepOriginal = $true; compatibility = $false }
        adjustment = @{ automaticDelayMs = 0; manualDelayMs = 0; speedCorrection = 1; preservePitch = $true }
        outputDirectory = (Join-Path $testRoot 'out'); outputName = "selftest-$label.NEW.mkv"; allowUnverified = $true; analysis = @{ verdict = 'aligned' } }
    if (!(Test-Path -LiteralPath $built.path)) { throw "Self-test of the portable folder: the $label mkvmerge reported success but wrote no file." }
}
Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
# The self-test must leave no bytecode behind: .pyc files carry the build machine's folder names.
Get-ChildItem -LiteralPath $folder -Recurse -Directory -Filter '__pycache__' -Force | Remove-Item -Recurse -Force
Write-Output "self-test: the bundled worker probed two files and built a film with $($mkvmergeBuilds.Count) mkvmerge build(s)"
if (!$NoZip) {
    $zip = Join-Path $root 'SoundMux-portable.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    # Not Compress-Archive: in Windows PowerShell 5.1 it stores "SoundMux\bin\..." with backslashes, which
    # Linux and macOS unpackers turn into flat files with "\" in their names. The ZIP format requires "/".
    # Default name encoding keeps ASCII names as they are and writes others as flagged UTF-8.
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $base = (Get-Item -LiteralPath $root).FullName.TrimEnd('\') + '\'
    $items = @(Get-Item -LiteralPath $folder) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force)
    $archive = [IO.Compression.ZipFile]::Open($zip, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($item in $items) {
            $name = $item.FullName.Substring($base.Length).Replace('\', '/')
            if ($item.PSIsContainer) { [void]$archive.CreateEntry($name + '/') }
            else { [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $item.FullName, $name, [IO.Compression.CompressionLevel]::Optimal) }
        }
    } finally { $archive.Dispose() }
    # Check what was written, not what was meant: every file of the folder, under SoundMux/, with "/" only.
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $names = @($archive.Entries | ForEach-Object { $_.FullName })
        $bad = @($names | Where-Object { $_.Contains('\') -or !$_.StartsWith('SoundMux/') })
        if ($bad.Count) { throw "Zip check: $($bad.Count) entry name(s) are not portable, e.g. $($bad[0])" }
        $fileCount = @($items | Where-Object { !$_.PSIsContainer }).Count
        $zipFileCount = @($names | Where-Object { !$_.EndsWith('/') }).Count
        if ($zipFileCount -ne $fileCount) { throw "Zip check: the folder has $fileCount files, the archive $zipFileCount." }
    } finally { $archive.Dispose() }
    Write-Output "zip check: $($names.Count) entries, all under SoundMux/ with '/' separators"
    Write-Output ('zip: {0} ({1:N0} MB)' -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB))
}
Write-Output ('folder: {0} ({1:N0} MB)' -f $folder, ((Get-ChildItem -LiteralPath $folder -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
