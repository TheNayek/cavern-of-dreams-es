param(
    [string]$Game = "E:\SteamLibrary\steamapps\common\Cavern of Dreams"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$orig = Join-Path $repo "orig\aa"
$aa = Join-Path $Game "Cavern of Dreams_Data\StreamingAssets\aa"

if (-not (Test-Path -LiteralPath $aa)) { throw "No existe: $aa" }
if (-not (Test-Path -LiteralPath (Join-Path $orig "catalog.json"))) { throw "No hay backup en $orig" }
if (Get-Process -Name 'Cavern of Dreams' -ErrorAction SilentlyContinue) { throw "Cierra el juego antes de restaurar." }
$receipt = $null
$receiptPath = Join-Path $repo 'orig\installation.json'
if (Test-Path -LiteralPath $receiptPath) { $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json }
foreach ($name in @('level0','level1')) {
    $source = Join-Path $repo ('orig\scenes\' + $name)
    $target = Join-Path $Game ('Cavern of Dreams_Data\' + $name)
    if (Test-Path -LiteralPath $source) {
        $allowed = @((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
        if ($receipt) { $allowed += @($receipt.Files | Where-Object { $_.Target -eq $target } | ForEach-Object { $_.SHA256 }) }
        if ($allowed -notcontains (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
            throw "La escena contiene cambios ajenos: $target. No se sobrescribe."
        }
    }
}

Copy-Item -LiteralPath (Join-Path $orig "catalog.json") -Destination (Join-Path $aa "catalog.json") -Force
Get-ChildItem -LiteralPath (Join-Path $orig "StandaloneWindows64") -Filter *.bundle | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $aa "StandaloneWindows64\$($_.Name)") -Force
    Write-Host "restaurado: $($_.Name)"
}
foreach ($name in @('level0','level1')) {
    $source = Join-Path $repo ('orig\scenes\' + $name)
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $Game ('Cavern of Dreams_Data\' + $name)) -Force
        Write-Host "restaurado: $name"
    }
}
Write-Host "Juego restaurado al estado original."
