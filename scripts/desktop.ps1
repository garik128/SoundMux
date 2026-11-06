param([switch]$Check, [switch]$Build)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:CARGO_HOME = Join-Path $projectRoot '.tools/cargo'
$env:RUSTUP_HOME = Join-Path $projectRoot '.tools/rustup'
$env:PATH = (Join-Path $env:CARGO_HOME 'bin') + ';' + $env:PATH
$cargo = Join-Path $env:CARGO_HOME 'bin/cargo.exe'
if (!(Test-Path -LiteralPath $cargo)) { throw 'Local Rust toolchain is missing. See README.md.' }
if ($Check) { & $cargo check --manifest-path src-tauri/Cargo.toml; exit $LASTEXITCODE }
& npm.cmd run build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $cargo build --manifest-path src-tauri/Cargo.toml
if ($LASTEXITCODE) { exit $LASTEXITCODE }
if (!$Build) { & (Join-Path $projectRoot 'src-tauri/target/debug/SoundMux.exe') }
