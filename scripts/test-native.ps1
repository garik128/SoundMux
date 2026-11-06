$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:CARGO_HOME = Join-Path $projectRoot '.tools/cargo'
$env:RUSTUP_HOME = Join-Path $projectRoot '.tools/rustup'
& (Join-Path $env:CARGO_HOME 'bin/cargo.exe') test --offline --manifest-path src-tauri/Cargo.toml
exit $LASTEXITCODE
