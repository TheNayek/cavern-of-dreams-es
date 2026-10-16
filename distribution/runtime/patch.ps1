# Portable Cavern of Dreams Spanish patch. Windows PowerShell 5.1.
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall')][string]$Mode = 'Install',
    [string]$Game = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:PackageRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$script:GameRoot = $null
$script:Work = $null
$script:PreserveWork = $false
$script:Lock = $null
$script:Expected = @(
    'Cavern of Dreams_Data/level0',
    'Cavern of Dreams_Data/level1',
    'Cavern of Dreams_Data/StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle',
    'Cavern of Dreams_Data/StreamingAssets/aa/StandaloneWindows64/localization-locales_assets_all.bundle',
    'Cavern of Dreams_Data/StreamingAssets/aa/StandaloneWindows64/localization-assets-shared_assets_all.bundle',
    'Cavern of Dreams_Data/StreamingAssets/aa/catalog.json'
)

function Assert-NoReparse([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -ne $item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Ruta con enlace o junction no admitida: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Resolve-Safe([string]$Root, [string]$Relative) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains('\') -or
        $Relative -cnotmatch '^[A-Za-z0-9_ .()/-]+$' -or
        [IO.Path]::IsPathRooted($Relative)) { throw "Ruta relativa no valida: $Relative" }
    foreach ($part in $Relative.Split('/')) {
        if ($part -eq '' -or $part -eq '.' -or $part -eq '..' -or
            $part.EndsWith('.') -or $part.EndsWith(' ')) { throw "Ruta relativa no valida: $Relative" }
    }
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $full = [IO.Path]::GetFullPath([IO.Path]::Combine($base, $Relative.Replace('/', '\')))
    if (-not $full.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Ruta fuera del directorio autorizado: $Relative"
    }
    Assert-NoReparse $full
    return $full
}

function Require-Properties($Object, [string[]]$Names) {
    if ($null -eq $Object) { throw 'Objeto JSON vacio.' }
    foreach ($name in $Names) {
        if ($null -eq $Object.PSObject.Properties[$name]) { throw "Falta campo JSON: $name" }
    }
}

function Assert-Hash($Value) {
    if ($Value -isnot [string] -or $Value -cnotmatch '^[0-9a-f]{64}$') {
        throw 'SHA256 no valido en manifiesto o recibo.'
    }
}

function Assert-Size($Value) {
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt 0) {
        throw 'Tamano no valido en manifiesto o recibo.'
    }
}

function Get-Hash([string]$Path) {
    Assert-NoReparse $Path
    if (-not [IO.File]::Exists($Path)) { throw "Falta archivo: $Path" }
    $algorithm = $null
    $stream = $null
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $stream = [IO.File]::OpenRead($Path)
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $algorithm) { $algorithm.Dispose() }
    }
}

function Assert-File([string]$Path, [string]$Hash, [long]$Size = -1) {
    if ((Get-Hash $Path) -cne $Hash) { throw "Integridad incorrecta: $Path" }
    if ($Size -ge 0 -and (Get-Item -LiteralPath $Path).Length -ne $Size) {
        throw "Tamano incorrecto: $Path"
    }
}

function Assert-GameClosed {
    if (Get-Process -Name 'Cavern of Dreams' -ErrorAction SilentlyContinue) {
        throw 'Cierra Cavern of Dreams antes de continuar.'
    }
}

function Invoke-Delta([string]$Tool, [string]$Source, [string]$Delta, [string]$Output) {
    # xdelta interprets XDELTA as extra command-line options. Never inherit them.
    $savedOptions = [Environment]::GetEnvironmentVariable('XDELTA', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('XDELTA', $null, 'Process')
        & $Tool '-d' '-D' '-R' '-s' $Source $Delta $Output
        if ($LASTEXITCODE -ne 0) { throw "xdelta3 fallo (codigo $LASTEXITCODE). El juego no se ha modificado." }
    } finally { [Environment]::SetEnvironmentVariable('XDELTA', $savedOptions, 'Process') }
}

function Read-Json([string]$Path) {
    Assert-NoReparse $Path
    if (-not [IO.File]::Exists($Path)) { throw "Falta archivo: $Path" }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-Receipt($Receipt, [string]$Path) {
    Assert-NoReparse $Path
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $json = $Receipt | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        # PS5.1 converts $null to an invalid empty backup path; NullString sends a CLR null.
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, [System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

# Replace an existing game file atomically. The sibling is on the same volume.
# Compare immediately before replacement to avoid overwriting an external change.
function Copy-GameFile([string]$Source, [string]$Target, [string]$Before, [string]$After, [long]$Size) {
    Assert-NoReparse $Target
    $temporary = $Target + '.cod-es-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::Copy($Source, $temporary, $false)
        Assert-File $temporary $After $Size
        Assert-GameClosed
        Assert-File $Target $Before
        [IO.File]::Replace($temporary, $Target, [System.Management.Automation.Language.NullString]::Value)
        Assert-File $Target $After $Size
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function New-Receipt($Manifest, [string]$State) {
    $records = @()
    foreach ($entry in $Manifest.files) {
        $records += [pscustomobject]@{
            path = $entry.path
            original_sha256 = $entry.source_sha256
            installed_sha256 = $entry.target_sha256
            original_size = $entry.source_size
            installed_size = $entry.target_size
        }
    }
    return [pscustomobject]@{
        schema_version = 1
        version = $Manifest.version
        steam_build = $Manifest.steam_build
        game = $script:GameRoot
        state = $State
        files = $records
    }
}

function Invoke-Patch {
    Assert-NoReparse $script:PackageRoot
    $gamePath = $Game
    if ([string]::IsNullOrWhiteSpace($gamePath)) {
        $gamePath = [IO.Path]::GetDirectoryName($script:PackageRoot)
    }
    $script:GameRoot = [IO.Path]::GetFullPath($gamePath).TrimEnd('\')
    Assert-NoReparse $script:GameRoot
    if (-not [IO.Directory]::Exists($script:GameRoot) -or
        -not [IO.Directory]::Exists((Resolve-Safe $script:GameRoot 'Cavern of Dreams_Data'))) {
        throw 'No se encuentra el juego. Extrae el ZIP en su carpeta o usa -Game con la ruta correcta.'
    }
    Assert-GameClosed
    $lockPath = Resolve-Safe $script:PackageRoot '.patch.lock'
    try {
        $script:Lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'No se puede bloquear el paquete. Cierra otros instaladores y comprueba permisos de escritura.' }

    $manifest = Read-Json (Resolve-Safe $script:PackageRoot 'manifest.json')
    Require-Properties $manifest @('schema_version', 'version', 'steam_build', 'tool', 'files')
    if ($manifest.schema_version -isnot [int] -or $manifest.schema_version -ne 1 -or
        $manifest.version -isnot [string] -or
        $manifest.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$' -or
        $manifest.steam_build -isnot [string] -or $manifest.steam_build -cne '13947382') {
        throw 'Manifiesto incompatible: se requiere version valida y Steam 13947382.'
    }
    Require-Properties $manifest.tool @('path', 'sha256')
    if ($manifest.tool.path -cne 'bin/xdelta3.exe') { throw 'Ruta de herramienta no admitida.' }
    Assert-Hash $manifest.tool.sha256
    if ($manifest.files -isnot [Array] -or $manifest.files.Count -ne 6) { throw 'El manifiesto debe contener los seis archivos previstos.' }
    $entries = @{}
    $patchNames = @{}
    foreach ($entry in $manifest.files) {
        Require-Properties $entry @('path', 'patch', 'source_sha256', 'target_sha256', 'patch_sha256', 'source_size', 'target_size')
        if ($script:Expected -cnotcontains $entry.path -or $entries.ContainsKey($entry.path)) { throw 'Destino duplicado o no autorizado.' }
        if ($entry.patch -isnot [string] -or $entry.patch -cnotmatch '^deltas/[A-Za-z0-9_.-]+\.xdelta$' -or $patchNames.ContainsKey($entry.patch)) {
            throw 'Delta duplicado o ruta no admitida.'
        }
        $null = Resolve-Safe $script:GameRoot $entry.path
        $null = Resolve-Safe $script:PackageRoot $entry.patch
        Assert-Hash $entry.source_sha256
        Assert-Hash $entry.target_sha256
        Assert-Hash $entry.patch_sha256
        Assert-Size $entry.source_size
        Assert-Size $entry.target_size
        $entries[$entry.path] = $entry
        $patchNames[$entry.patch] = $true
    }
    $backupRoot = Resolve-Safe $script:PackageRoot 'backup'
    $receiptPath = Resolve-Safe $script:PackageRoot 'backup/receipt.json'
    $receipt = $null
    $previous = @{}
    if (Test-Path -LiteralPath $backupRoot) {
        if (-not [IO.Directory]::Exists($backupRoot) -or -not [IO.File]::Exists($receiptPath)) {
            throw 'Backup desconocido o incompleto. Conserva esa carpeta y recupera el paquete con su recibo; no se sobrescribe.'
        }
        $receipt = Read-Json $receiptPath
        Require-Properties $receipt @('schema_version', 'version', 'steam_build', 'game', 'state', 'files')
        if ($receipt.schema_version -isnot [int] -or $receipt.schema_version -ne 1 -or
            $receipt.version -isnot [string] -or $receipt.steam_build -isnot [string] -or
            $receipt.game -isnot [string] -or $receipt.game -ine $script:GameRoot -or
            @('installed', 'uninstalled') -cnotcontains $receipt.state -or
            $receipt.files -isnot [Array] -or $receipt.files.Count -ne 6) {
            throw 'Recibo incompatible o perteneciente a otra instalacion. No se modifica el juego.'
        }
        foreach ($record in $receipt.files) {
            Require-Properties $record @('path', 'original_sha256', 'installed_sha256', 'original_size', 'installed_size')
            if ($script:Expected -cnotcontains $record.path -or $previous.ContainsKey($record.path)) { throw 'Destinos no validos en el recibo.' }
            Assert-Hash $record.original_sha256
            Assert-Hash $record.installed_sha256
            Assert-Size $record.original_size
            Assert-Size $record.installed_size
            $backup = Resolve-Safe $backupRoot ('files/' + $record.path)
            Assert-File $backup $record.original_sha256 $record.original_size
            if ($Mode -eq 'Install' -and ($record.original_sha256 -cne $entries[$record.path].source_sha256 -or
                $record.original_size -ne $entries[$record.path].source_size)) {
                throw 'La copia original no corresponde a esta version de Steam. Usa un paquete compatible.'
            }
            $previous[$record.path] = $record
        }
    }
    if ($Mode -eq 'Uninstall' -and $null -eq $receipt) {
        throw 'No hay recibo ni copias de este instalador. No se puede desinstalar con seguridad.'
    }

    # Check every current file and every backup before creating or replacing anything.
    $current = @{}
    $allTarget = $true
    $allOriginal = $true
    foreach ($relative in $script:Expected) {
        $entry = $entries[$relative]
        $target = Resolve-Safe $script:GameRoot $relative
        $hash = Get-Hash $target
        $size = (Get-Item -LiteralPath $target).Length
        $current[$relative] = [pscustomobject]@{ hash = $hash; size = $size }
        if ($null -eq $receipt) {
            if ($hash -cne $entry.source_sha256 -or $size -ne $entry.source_size) {
                if ($hash -ceq $entry.target_sha256) {
                    throw "Traduccion ya presente sin backup portatil: $relative. Restaura primero los originales (verificar archivos en Steam)."
                }
                throw "Archivo modificado o version de Steam incompatible: $relative. Restaura/verifica los archivos originales en Steam antes de instalar."
            }
        } else {
            $record = $previous[$relative]
            $isOriginal = $hash -ceq $record.original_sha256 -and $size -eq $record.original_size
            $isInstalled = $hash -ceq $record.installed_sha256 -and $size -eq $record.installed_size
            if (-not $isOriginal -and -not $isInstalled) {
                throw "Cambio externo detectado: $relative. No se sobrescribe. Conserva backup y restaura la version correspondiente antes de continuar."
            }
        }
        if ($hash -cne $entry.target_sha256 -or $size -ne $entry.target_size) { $allTarget = $false }
        if ($null -ne $receipt -and ($hash -cne $previous[$relative].original_sha256 -or $size -ne $previous[$relative].original_size)) { $allOriginal = $false }
    }

    if ($Mode -eq 'Install') {
        $tool = Resolve-Safe $script:PackageRoot $manifest.tool.path
        Assert-File $tool $manifest.tool.sha256
        foreach ($entry in $manifest.files) { Assert-File (Resolve-Safe $script:PackageRoot $entry.patch) $entry.patch_sha256 }
        if ($null -ne $receipt -and $allTarget -and $receipt.version -ceq $manifest.version -and $receipt.state -ceq 'installed') {
            Write-Host 'La traduccion ya esta instalada. Archivos y backups verificados.'
            return
        }
    } elseif ($allOriginal -and $receipt.state -ceq 'uninstalled') {
        Write-Host 'El juego ya esta restaurado. Backups originales verificados.'
        return
    }

    $script:Work = Resolve-Safe $script:PackageRoot ('.work-' + [Guid]::NewGuid().ToString('N'))
    $null = [IO.Directory]::CreateDirectory($script:Work)
    $outputs = @{}
    $rollback = @{}
    foreach ($relative in $script:Expected) {
        $entry = $entries[$relative]
        $target = Resolve-Safe $script:GameRoot $relative
        $index = [Array]::IndexOf($script:Expected, $relative)
        $saved = Resolve-Safe $script:Work ("before-$index.bin")
        [IO.File]::Copy($target, $saved, $false)
        Assert-File $saved $current[$relative].hash $current[$relative].size
        $rollback[$relative] = $saved
        if ($Mode -eq 'Install') {
            $source = $target
            if ($null -ne $receipt) { $source = Resolve-Safe $backupRoot ('files/' + $relative) }
            Assert-File $source $entry.source_sha256 $entry.source_size
            $output = Resolve-Safe $script:Work ("after-$index.bin")
            $delta = Resolve-Safe $script:PackageRoot $entry.patch
            Write-Host "Preparando: $relative"
            Invoke-Delta $tool $source $delta $output
            Assert-File $output $entry.target_sha256 $entry.target_size
            $outputs[$relative] = $output
        } else {
            $outputs[$relative] = Resolve-Safe $backupRoot ('files/' + $relative)
        }
    }

    if ($null -eq $receipt) {
        # Publish a complete, verified backup with a recovery receipt in one rename.
        $prepared = Resolve-Safe $script:Work 'prepared-backup'
        $null = [IO.Directory]::CreateDirectory($prepared)
        foreach ($relative in $script:Expected) {
            $dest = Resolve-Safe $prepared ('files/' + $relative)
            $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
            [IO.File]::Copy($rollback[$relative], $dest, $false)
            Assert-File $dest $entries[$relative].source_sha256 $entries[$relative].source_size
        }
        $receipt = New-Receipt $manifest 'uninstalled'
        Write-Receipt $receipt (Resolve-Safe $prepared 'receipt.json')
        if (Test-Path -LiteralPath $backupRoot) { throw 'Ha aparecido otra carpeta backup. No se sobrescribe.' }
        [IO.Directory]::Move($prepared, $backupRoot)
    }

    $oldReceiptBytes = [IO.File]::ReadAllBytes($receiptPath)
    $changed = New-Object 'System.Collections.Generic.List[string]'
    try {
        # Recheck the entire transaction immediately before the first game write.
        Assert-GameClosed
        foreach ($relative in $script:Expected) {
            Assert-File (Resolve-Safe $script:GameRoot $relative) $current[$relative].hash $current[$relative].size
            $original = $entries[$relative]
            if ($Mode -eq 'Uninstall') { $original = $previous[$relative] }
            $hash = $entries[$relative].source_sha256
            $size = $entries[$relative].source_size
            if ($Mode -eq 'Uninstall') { $hash = $original.original_sha256; $size = $original.original_size }
            Assert-File (Resolve-Safe $backupRoot ('files/' + $relative)) $hash $size
        }
        foreach ($relative in $script:Expected) {
            $after = $entries[$relative].target_sha256
            $size = $entries[$relative].target_size
            if ($Mode -eq 'Uninstall') { $after = $previous[$relative].original_sha256; $size = $previous[$relative].original_size }
            if ($current[$relative].hash -ceq $after) { continue }
            $changed.Add($relative)
            Copy-GameFile $outputs[$relative] (Resolve-Safe $script:GameRoot $relative) $current[$relative].hash $after $size
            Write-Host "Verificado: $relative"
        }
        $nextReceipt = $receipt
        if ($Mode -eq 'Install') { $nextReceipt = New-Receipt $manifest 'installed' }
        else {
            $nextReceipt = ($receipt | ConvertTo-Json -Depth 8 | ConvertFrom-Json)
            $nextReceipt.state = 'uninstalled'
        }
        Write-Receipt $nextReceipt $receiptPath
    } catch {
        $failure = $_.Exception.Message
        $recoveryErrors = New-Object 'System.Collections.Generic.List[string]'
        for ($i = $changed.Count - 1; $i -ge 0; $i--) {
            $relative = $changed[$i]
            try {
                $target = Resolve-Safe $script:GameRoot $relative
                $actual = Get-Hash $target
                if ($actual -ceq $current[$relative].hash) { continue }
                $after = $entries[$relative].target_sha256
                if ($Mode -eq 'Uninstall') { $after = $previous[$relative].original_sha256 }
                if ($actual -cne $after) { throw "Cambio externo durante recuperacion: $relative" }
                Copy-GameFile $rollback[$relative] $target $actual $current[$relative].hash $current[$relative].size
            } catch { $recoveryErrors.Add($_.Exception.Message) }
        }
        try {
            # Restore the prior receipt even if writing the new receipt failed.
            $savedReceipt = Resolve-Safe $script:Work 'receipt-before.json'
            [IO.File]::WriteAllBytes($savedReceipt, $oldReceiptBytes)
            Assert-NoReparse $receiptPath
            [IO.File]::Replace($savedReceipt, $receiptPath, [System.Management.Automation.Language.NullString]::Value)
        } catch { $recoveryErrors.Add('No se pudo recuperar el recibo: ' + $_.Exception.Message) }
        if ($recoveryErrors.Count -gt 0) {
            $script:PreserveWork = $true
            throw ($failure + "`nRECUPERACION INCOMPLETA. Conserva backup, recibo y temporales en $script:Work. " + ($recoveryErrors -join ' | '))
        }
        throw ($failure + "`nOperacion cancelada; se ha recuperado el estado anterior del juego.")
    }
    if ($Mode -eq 'Install') { Write-Host 'Traduccion instalada y verificada. Conserva CavernOfDreams-ES/backup para desinstalar.' }
    else { Write-Host 'Juego original restaurado y verificado. Se conservan las copias para recuperar o reinstalar.' }
}

$exitCode = 0
try { Invoke-Patch }
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($script:Work -and -not $script:PreserveWork -and [IO.Directory]::Exists($script:Work)) {
        try {
            Assert-NoReparse $script:Work
            foreach ($item in Get-ChildItem -LiteralPath $script:Work -Recurse -Force) { Assert-NoReparse $item.FullName }
            Remove-Item -LiteralPath $script:Work -Recurse -Force
        } catch { Write-Warning ('No se pudo limpiar la carpeta temporal: ' + $script:Work) }
    }
    if ($null -ne $script:Lock) { $script:Lock.Dispose() }
}
exit $exitCode
