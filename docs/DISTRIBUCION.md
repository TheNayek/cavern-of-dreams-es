# Distribución y compilación local

## Artefacto público

La versión 0.1.0-beta se dirige a la versión de Steam para Windows x64, build 13947382. No se ha validado en Linux, Steam Deck ni GOG.

El archivo de Release principal es `Cavern-of-Dreams-ES-v0.1.0-beta-windows.zip`. Contiene `Cavern of Dreams_Data/` con los seis archivos modificados en sus rutas de instalación y `Traduccion-ES/` con instrucciones, notas, créditos y manifiesto SHA-256. No contiene ejecutables, instaladores, scripts ni herramientas de parcheo.

El usuario cierra el juego, guarda una copia de `Cavern of Dreams_Data` fuera de la instalación, y copia las dos carpetas del ZIP junto a `Cavern of Dreams.exe`, aceptando combinar carpetas y sustituir archivos. También puede extraer directamente en ese destino. Se necesitan permisos de escritura en la carpeta del juego. El idioma `es` se selecciona según el idioma del sistema; no existe un selector en el juego. Solo se han validado sistemas configurados en español.

Los seis destinos son tres bundles de localización, `catalog.json`, `level0` y `level1`. El empaquetador verifica el build y sus archivos antes de incluirlos; el manifiesto permite comprobar sus hashes. La copia manual no detecta versiones incompatibles ni otros mods y no crea backups. Este formato de distribución fue elegido expresamente por el usuario para simplificar la instalación. Los archivos completos modificados se incluyen solo en el ZIP de jugadores; no se añaden al repositorio de código. No se modifican partidas guardadas ni `Managed`.

Para desinstalar, restaura tu copia original o verifica la integridad del juego en Steam; después puedes borrar `Traduccion-ES`. Si Steam ya ha actualizado el juego, utiliza la verificación y un parche compatible, sin copiar backups de una build antigua. Los archivos del ZIP son los mismos del build ya probado: una instalación de desarrollo que ya tenga esos archivos no necesita restaurarse antes para copiarlos de nuevo.

## Preparar fuentes y compilar

El repositorio público de código y textos es distinto del ZIP de Release. El paquete de fuentes `Cavern-of-Dreams-ES-v0.1.0-beta-fuentes.zip` contiene una copia saneada del proyecto, sin `orig/`, `extracted/`, `build/` ni binarios o datos originales del juego. Para construir localmente hacen falta una copia legal y compatible del juego y sus archivos originales de Addressables y escenas, que cada colaborador debe obtener de su propia instalación.

Para preparar las fuentes desde una instalación local, ejecuta `python tools\prepare_sources.py --game "D:\ruta\a\Cavern of Dreams"`. El comando valida los seis hashes de compatibilidad y copia los datos originales necesarios a `orig/`, sin sobrescribir originales existentes. Define tú `CAVERN_GAME_ROOT` en la misma sesión de PowerShell para que las herramientas encuentren las DLL del juego. No publiques las copias originales, las salidas extraídas, el build ni partidas guardadas.

Con Python 3.13, prepara un entorno virtual e instala las dependencias declaradas en `requirements.txt`. Después:

```powershell
$env:CAVERN_GAME_ROOT = "D:\ruta\a\Cavern of Dreams"
python tools\prepare_sources.py --game "D:\ruta\a\Cavern of Dreams"
python tools\extract.py
python tools\check_translation.py
python tools\build_es.py
python tools\verify_build.py
```

El proceso genera `extracted/` y `build/out/`; ambos son datos locales generados y están excluidos del repositorio público. Para una prueba de desarrollo, usa `powershell -File tools\install.ps1 -Game $env:CAVERN_GAME_ROOT` solo sobre la build validada; para deshacer, `powershell -File tools\restore.ps1 -Game $env:CAVERN_GAME_ROOT`.

## Generar el ZIP y preparar GitHub

```powershell
python tools\package_release.py --version 0.1.0-beta
python -m unittest discover -s tools -p test_release_package.py -v
```

El empaquetador verifica traducción y build y copia únicamente los seis destinos declarados. Genera el ZIP manual para jugadores, el ZIP de fuentes, `SHA256SUMS.txt` y las notas en `patches/v0.1.0-beta/`, sin descargar ni incluir xdelta. Las pruebas del formato manual comprueban la lista exacta, hashes y contenido de los seis archivos, y simulan copia y restauración en `build/qa/`; no modifican la instalación real.

El empaquetador conserva el formato anterior mediante `--format delta` o `--format both`. Solo estas opciones descargan xdelta3 y sus avisos fijados por SHA-256 y verifican la reconstrucción de las diferencias. El archivo opcional se llama `Cavern-of-Dreams-ES-v0.1.0-beta-windows-delta.zip` y contiene su propio `LEEME.txt`. Los tests del instalador se ejecutan cuando ese ZIP está presente; para comprobar únicamente la descarga principal: `python -m unittest discover -s tools -p test_release_package.py -k ManualArchiveTests -v`.

`build/public-repo/` contiene la selección de código y documentos para el repositorio público. Es una exportación mediante lista explícita de archivos; no subas la carpeta de trabajo completa. Desde esa exportación se puede inicializar Git y subir el proyecto al repositorio elegido. Después se crea una pre-release `v0.1.0-beta` con el ZIP para Windows, los hashes y las notas. Estos comandos locales no crean repositorios ni publican nada.

## Licencias y contenido de terceros

MIT cubre únicamente el código propio que se identifique expresamente como cubierto por `LICENSE`. No cubre el juego, sus recursos ni concede derechos generales sobre los textos traducidos. El ZIP manual incluye los créditos; el formato delta opcional incluye además las licencias de sus herramientas. Los derechos sobre el juego siguen perteneciendo a sus titulares.

No se publica ninguna Release automáticamente. La preparación local del paquete y la publicación en GitHub son acciones separadas.
