# ETL medallón — Supermercados Andinos

Este repositorio implementa un ETL en Python que transforma archivos CSV de ventas, tiendas, productos, clientes y promociones en un modelo dimensional para Power BI. El proceso organiza los datos en capas **Bronce → Plata → Oro**, registra la trazabilidad de limpieza y bloquea la publicación de Oro si fallan los controles de calidad.

Este README documenta únicamente el ETL: cómo instalarlo, ejecutarlo, revisar sus resultados y entender sus etapas. No documenta el uso o la validación visual del informe Power BI.

## Requisitos

- Python 3.11 o posterior.
- `pip`.
- Archivos fuente en `data/bronce/`.
- Para publicar en SharePoint: acceso configurado a Microsoft Graph y las variables/secrets indicados en [Publicación opcional en SharePoint](#publicación-opcional-en-sharepoint).

Dependencias:

- `pandas`
- `openpyxl`
- `pytest` para pruebas

## Instalación en Windows

Ejecuta estos comandos desde la raíz del repositorio en PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Si la política de ejecución de PowerShell no permite activar el entorno, puedes invocar Python directamente:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Fuentes esperadas

Bronce espera encontrar estos archivos directamente bajo `data/bronce/`:

| Archivo | Llave natural | Uso |
|---|---|---|
| `ventas.csv` | `venta_id` | Líneas de venta y devolución |
| `tiendas.csv` | `tienda_id` | Tiendas, ciudad, formato y superficie |
| `productos.csv` | `producto_id` | Catálogo, categoría, proveedor, costo y precio |
| `clientes.csv` | `cliente_id` | Segmento y atributos de cliente |
| `promociones.csv` | `promocion_id` | Campañas, fechas y categorías objetivo |

El contrato exacto de columnas y las reglas centrales se definen en [`andinos_etl/config.py`](andinos_etl/config.py). El archivo `data/bronce/diccionario.csv` describe los campos de las fuentes.

### Lotes adicionales

Bronce incorpora lotes incrementales únicamente si respetan esta estructura:

```text
data/bronce/lotes/<nombre-del-lote>/<tabla>.csv
```

El nombre del archivo debe corresponder a una de las tablas esperadas (por ejemplo, `ventas.csv` o `productos.csv`). Los lotes se anexan en orden reproducible. Los archivos guardados fuera de `data/bronce/lotes/` no participan del flujo.

## Ejecutar el ETL completo

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe main.py --stage completo
```

El modo completo:

1. lee las fuentes de `data/bronce/` sin modificarlas;
2. crea Bronce, Plata, Oro y los reportes en staging;
3. evalúa las reglas de calidad;
4. publica las carpetas nuevas solo si no hay controles bloqueantes fallidos;
5. conserva la versión publicada anterior si la calidad falla o se interrumpe la promoción.

Las salidas quedan bajo `data/`:

```text
data/
├── bronce/                 # fuentes; entrada de solo lectura
├── plata/                  # datos limpios y tipados
│   └── rechazados/         # filas apartadas con motivo
├── oro/                    # seis tablas del modelo dimensional
├── calidad/
│   └── reporte_calidad.csv
├── logs/
│   └── etl.log
└── _staging/                # zona temporal durante la ejecución
```

`data/_staging/` es una zona de trabajo. El pipeline la administra durante sus ejecuciones; no la uses como fuente permanente ni modifiques sus archivos mientras corre el ETL.

Para cambiar la carpeta raíz de entrada/salida del modo completo:

```powershell
.\.venv\Scripts\python.exe main.py --stage completo --data-dir ruta/a/data
```

`--data-dir` define también la ruta esperada para `bronce/`, salvo que se configure una ruta distinta al llamar las etapas por separado.

## Ejecutar por etapas

El modo por etapas crea artefactos portables e independientes. **Sigue el orden Bronce → Plata → Oro** y conserva cada carpeta hasta terminar la verificación. Plata y Oro verifican los checksums de sus artefactos de entrada; no deben recibir carpetas CSV arbitrarias sin `manifest.json` y `checksums.sha256`.

```powershell
# 1. Validar y empaquetar las fuentes Bronce
.\.venv\Scripts\python.exe main.py --stage bronce `
  --bronze-dir data/bronce `
  --output-dir artifacts/bronce

# 2. Limpiar el artefacto Bronce verificado
.\.venv\Scripts\python.exe main.py --stage plata `
  --bronze-dir artifacts/bronce `
  --output-dir artifacts/plata

# 3. Construir Oro, evaluar calidad y producir el bundle aprobado
.\.venv\Scripts\python.exe main.py --stage oro `
  --bronze-dir artifacts/bronce `
  --plata-dir artifacts/plata `
  --output-dir artifacts/oro

# 4. Verificar el bundle Oro aprobado
.\.venv\Scripts\python.exe main.py --stage verificar `
  --output-dir artifacts/oro/andinos-model-approved
```

Si Oro no supera el gate, la etapa no genera el bundle aprobado. Conserva un diagnóstico bajo `artifacts/oro/diagnostico/`, incluidos el gate y el reporte de calidad disponibles. Corrige la causa en los datos o reglas y vuelve a ejecutar las etapas.

La etapa `bronce` puede ejecutarse también con su valor de salida predeterminado `data/bronce-validado`; `plata`, con `data/plata-validado`; `oro`, con `data/`; y `verificar`, con `data/andinos-model-approved`. Para un flujo repetible y fácil de revisar, se recomienda especificar siempre `--output-dir`.

## Qué hace cada capa

### Bronce

- Lee CSV como texto para preservar identificadores y ceros iniciales.
- Comprueba que existan los archivos y las columnas requeridas.
- Añade `_fila_origen` para poder rastrear filas hasta el archivo fuente.
- Tolera columnas adicionales en la fuente, pero no las usa en la transformación.
- Anexa los lotes que cumplan la estructura configurada.
- No modifica los CSV de entrada.

### Plata

- Recorta espacios y convierte cadenas vacías en valores nulos.
- Convierte fechas, números y enteros a los tipos requeridos.
- Valida llaves y atributos obligatorios de las dimensiones.
- Elimina duplicados exactos y aparta IDs duplicados conflictivos.
- Valida fechas, cantidades, precios, descuentos y signos de ventas/devoluciones.
- Canonicaliza los medios de pago conocidos; valores no reconocidos se clasifican como `Otro`.
- Permite cliente o promoción sin identificar; un ID informado pero inexistente se rechaza como referencia huérfana.
- Completa costos unitarios ausentes desde `productos.costo_base` y marca `costo_imputado`. Si no hay costo de referencia, rechaza la fila.
- Guarda datos válidos, rechazados con `motivo_rechazo` y `reglas_limpieza.csv`.

### Oro

Genera una tabla de hechos a nivel de línea y cinco dimensiones:

```text
fact_ventas
├── dim_fecha
├── dim_tienda
├── dim_producto
├── dim_cliente
└── dim_promocion
```

Calcula por línea:

```text
importe_bruto      = cantidad × precio_unitario
importe_descuento  = importe_bruto × descuento_pct
importe_neto       = importe_bruto − importe_descuento
costo_total        = cantidad × costo_unitario
margen             = importe_neto − costo_total
```

Las cantidades de devolución son negativas, por lo que sus importes disminuyen los totales al agregarse. Los importes Oro se redondean a cuatro decimales. Las dimensiones de cliente y promoción incluyen un miembro con clave sustituta `0` para representar cliente no identificado o ausencia de promoción.

La salida incluye seis CSV y `modelo_estrella.xlsx`, con una hoja por tabla. Estos archivos son datos fuente del modelo semántico; el ETL no realiza el refresco de Power BI.

## Controles y evidencias

La capa de calidad produce `reporte_calidad.csv`, con el control, su descripción, resultado esperado, resultado obtenido y estado. Los controles comprueban, entre otros aspectos:

- conciliación de filas e importes entre capas;
- integridad referencial, unicidad de llaves y consistencia temporal;
- signos de venta/devolución y vigencia de promociones;
- costo, descuentos, margen y ventas por m²;
- porcentajes y filas de promoción/devolución;
- integridad del calendario y filas dimensionales.

El umbral de costo imputado es 1 %. Si se supera, el control correspondiente genera una **advertencia** para revisar el costo estimado. Un control bloqueante en `FALLO` impide la publicación de Oro.

En un artefacto por etapas aprobado, las evidencias principales son:

| Archivo o carpeta | Contenido |
|---|---|
| `andinos-model-approved/modelo_estrella/` | CSV de las seis tablas Oro |
| `andinos-model-approved/modelo_estrella.xlsx` | Libro Excel con las tablas Oro |
| `andinos-model-approved/calidad/reporte_calidad.csv` | Resultados de los controles |
| `andinos-model-approved/calidad/reglas_limpieza.csv` | Reglas y filas afectadas |
| `andinos-model-approved/gate.json` | Estado `APPROVED`/`REJECTED` y detalle de controles |
| `andinos-model-approved/manifest.json` | Etapa, commit, conteos, métricas y hashes |
| `andinos-model-approved/checksums.sha256` | Hashes SHA-256 del artefacto |

La verificación compara hashes y, para Oro, los conteos de filas entre manifest, CSV y hojas Excel.

## Ejecutar pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

La automatización de CI también ejecuta compilación estática y pruebas:

```powershell
.\.venv\Scripts\python.exe -m compileall -q andinos_etl main.py tests
```

## Publicación opcional en SharePoint

El workflow de GitHub Actions publica únicamente cuando se cumple todo lo siguiente:

- el evento es un `push` a la rama `main`;
- las pruebas y las etapas previas terminan correctamente;
- Oro tiene gate `APPROVED`;
- la variable de repositorio `PUBLISH_ENABLED` está configurada como `true`;
- el entorno `sharepoint-production` tiene permisos y configuración válidos.

Variables requeridas:

- `SHAREPOINT_SITE_ID`
- `SHAREPOINT_FOLDER`

Secrets requeridos:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`

El módulo `andinos_etl.sharepoint` publica el artefacto aprobado bajo una carpeta versionada por ejecución y verifica los SHA-256 remotos. También publica una copia estable de `modelo_estrella.xlsx`. **No asumas que el modelo Power BI ya consume esa copia:** compara la carpeta configurada con la URL de Power Query y prueba un refresco real con las credenciales del entorno.

Nunca guardes credenciales en el README, el código, los CSV ni los logs. Usa variables/secrets protegidos de GitHub Actions.

## Códigos de salida

| Código | Significado |
|---:|---|
| `0` | Ejecución correcta |
| `2` | Error de configuración, entrada o ETL |
| `3` | Quality gate de Oro rechazado |
| `4` | Fallo de verificación de integridad |

Para consultar opciones disponibles:

```powershell
.\.venv\Scripts\python.exe main.py --help
```

## Estructura del proyecto

| Ruta | Responsabilidad |
|---|---|
| `main.py` | CLI del modo completo y de las etapas |
| `andinos_etl/config.py` | Contrato, rutas y reglas centrales |
| `andinos_etl/almacen.py` | Lectura/escritura CSV |
| `andinos_etl/bronce.py` | Lectura de fuentes y trazabilidad |
| `andinos_etl/plata.py` | Limpieza, validación y rechazados |
| `andinos_etl/oro.py` | Hecho, dimensiones y exportación Excel |
| `andinos_etl/calidad.py` | Conciliaciones y gate de calidad |
| `andinos_etl/pipeline.py` | Orquestación completa, staging y publicación atómica |
| `andinos_etl/stages.py` | Ejecuciones separadas y gate Oro |
| `andinos_etl/artefactos.py` | Formato de transporte de Bronce/Plata |
| `andinos_etl/integridad.py` | Manifests, checksums y verificación |
| `andinos_etl/sharepoint.py` | Publicación opcional mediante Microsoft Graph |
| `tests/` | Pruebas automatizadas |
| `.github/workflows/main.yml` | CI y publicación condicional |

## Límites de interpretación

- `venta_id` identifica una **línea**, no un ticket ni una compra.
- Las cifras dependen de la versión de datos y deben leerse junto con el manifest y el reporte de calidad de la misma ejecución.
- Los costos completados desde costo base son estimaciones identificadas por `costo_imputado`.
- Los resultados descriptivos no prueban que promociones o decisiones hayan causado cambios en ventas o margen.
- Una ejecución correcta del ETL no demuestra que el modelo semántico haya refrescado ni que el informe Power BI se haya validado visualmente.
