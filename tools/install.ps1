param([string]$Game = "E:\SteamLibrary\steamapps\common\Cavern of Dreams")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo "build\out"
$data = Join-Path $Game "Cavern of Dreams_Data"
$aa = Join-Path $data "StreamingAssets\aa"
$receiptPath = Join-Path $repo "orig\installation.json"
$manifestPath = Join-Path $out 'native_ui_manifest.json'
if (-not (Test-Path -LiteralPath $aa)) { throw "No existe: $aa" }
if (Get-Process -Name 'Cavern of Dreams' -ErrorAction SilentlyContinue) { throw "Cierra el juego antes de instalar." }
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "Build incompleto: ejecuta build_es.py y verify_build.py." }
$previous = $null
if (Test-Path -LiteralPath $receiptPath) { $previous = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json }
$files = @(
    [pscustomobject]@{ Source=(Join-Path $out 'localization-string-tables-english(en)_assets_all.bundle'); Target=(Join-Path $aa 'StandaloneWindows64\localization-string-tables-english(en)_assets_all.bundle'); Backup='StandaloneWindows64\localization-string-tables-english(en)_assets_all.bundle' },
    [pscustomobject]@{ Source=(Join-Path $out 'localization-locales_assets_all.bundle'); Target=(Join-Path $aa 'StandaloneWindows64\localization-locales_assets_all.bundle'); Backup='StandaloneWindows64\localization-locales_assets_all.bundle' },
    [pscustomobject]@{ Source=(Join-Path $out 'localization-assets-shared_assets_all.bundle'); Target=(Join-Path $aa 'StandaloneWindows64\localization-assets-shared_assets_all.bundle'); Backup='StandaloneWindows64\localization-assets-shared_assets_all.bundle' }
)
foreach ($name in @('level0','level1')) {
    $pristine = Join-Path $repo ('orig\scenes\' + $name)
    $target = Join-Path $data $name
    $built = Join-Path $out ('scenes\' + $name)
    if (-not (Test-Path -LiteralPath $pristine)) { throw "Falta fuente pristina: $pristine" }
    if (-not (Test-Path -LiteralPath $built)) { throw "Falta escena construida: $built" }
    $currentHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    $allowed = @((Get-FileHash -LiteralPath $pristine -Algorithm SHA256).Hash, (Get-FileHash -LiteralPath $built -Algorithm SHA256).Hash)
    if ($previous) { $allowed += @($previous.Files | Where-Object { $_.Target -eq $target } | ForEach-Object { $_.SHA256 }) }
    if ($allowed -notcontains $currentHash) { throw "Escena con cambios ajenos o actualizada: $target. No se sobrescribe." }
    $files += [pscustomobject]@{ Source=$built; Target=$target; Backup=('scenes\' + $name) }
}
$files += [pscustomobject]@{ Source=(Join-Path $out 'catalog.json'); Target=(Join-Path $aa 'catalog.json'); Backup='catalog.json' }
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file.Source)) { throw "Falta salida: $($file.Source)" }
    if (-not (Test-Path -LiteralPath $file.Target)) { throw "Falta destino original: $($file.Target)" }
}
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bak = Join-Path $repo "orig\backup-$stamp"
Write-Host "Backup en $bak"
foreach ($file in $files) {
    $backupFile = Join-Path $bak $file.Backup
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupFile) | Out-Null
    Copy-Item -LiteralPath $file.Target -Destination $backupFile
}
$applied = New-Object System.Collections.Generic.List[object]
$records = @()
try {
    foreach ($file in $files) {
        $applied.Add($file)
        Copy-Item -LiteralPath $file.Source -Destination $file.Target -Force
        $expected = (Get-FileHash -LiteralPath $file.Source -Algorithm SHA256).Hash
        $actual = (Get-FileHash -LiteralPath $file.Target -Algorithm SHA256).Hash
        if ($actual -ne $expected) { throw "Hash incorrecto tras copiar: $($file.Target)" }
        $records += [pscustomobject]@{ Target=$file.Target; SHA256=$actual; Backup=$file.Backup }
        Write-Host "instalado: $($file.Backup)"
    }
} catch {
    $installError = $_
    for ($index=$applied.Count-1; $index -ge 0; $index--) {
        $file = $applied[$index]
        $original = Join-Path $bak $file.Backup
        if ((Get-FileHash -LiteralPath $file.Target -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash) {
            Copy-Item -LiteralPath $original -Destination $file.Target -Force
        }
    }
    throw $installError
}
[pscustomobject]@{ InstalledAt=(Get-Date).ToString('o'); Backup=$bak; Files=$records } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
Write-Host "Parche instalado: tablas, interfaz y escenas."
