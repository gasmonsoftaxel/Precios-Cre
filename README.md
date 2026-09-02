# Histórico de precios de combustible (CNE / CRE México)

Archiva diariamente los precios vigentes de gasolina y diésel por estación de servicio.

## Qué genera

- `historico/precios_AAAA-MM-DD.csv.gz` — un archivo por día (gzip): `fecha, cre_id, combustible, precio`
- `catalogo.csv` — estaciones con `cre_id`, nombre y coordenadas. Se reescribe solo si cambió.

## Fuentes

- https://publicacionexterna.azurewebsites.net/publicaciones/places
- https://publicacionexterna.azurewebsites.net/publicaciones/prices

Se cruzan por `place_id` porque el feed de precios no trae el número de permiso.

## Limpieza aplicada

- Descarta precios fuera del rango 10–45 MXN (el feed trae capturas de $0.01 y $69.99)
- Elimina los ~136 pares (estación, producto) duplicados del feed
- Idempotente: si el archivo del día ya existe, no lo reescribe

## Programación

`.github/workflows/diario.yml` corre a las 13:00 UTC = 07:00 CDMX.
También se puede disparar a mano desde la pestaña **Actions**.

## Uso local (Raspberry Pi, cron)

    python3 descarga.py

En crontab, diario a las 7:00:

    0 7 * * * /usr/bin/python3 /ruta/al/repo/descarga.py >> /ruta/al/repo/bitacora.log 2>&1

## Geografía

`geo.py` recalcula `estaciones_geo.csv.gz` en cada corrida:

- **estado y municipio** salen de `inegi_localidades.csv.gz` (304,221 localidades
  del INEGI) por localidad más cercana. Validado contra 1,000 estaciones
  etiquetadas por Profeco: 99.1 % de acierto en estado, 88.6 % en municipio.
- **marca, grupo, TAR, clasificación, tipo de flete y fronterizo** salen de
  `atributos_profeco.csv.gz`, que es estático. Las estaciones nuevas de la CNE
  quedan con esos campos vacíos hasta que se vuelva a procesar el KML de Profeco.
- `cve_geo` es la clave INEGI de 5 dígitos, para cruzar con datos oficiales.
- `en_mapa_profeco` distingue lo original de lo recuperado.

Sin dependencias externas: usa una rejilla de 0.5° para prefiltrar, no necesita numpy.
Tarda unos 45 segundos.

### Terminales (TAR) y precios de compra

`tars.csv` trae las 73 Terminales de Almacenamiento y Reparto de Pemex con
coordenadas (servicio ArcGIS de SEMARNAT). `geo.py` calcula la terminal mas
cercana en linea recta (`tar_cercana`, `tar_region`, `km_tar`). Es proximidad
geografica, no la terminal que realmente surte: esa asignacion es contractual y
no la publica nadie.

`tar_etiqueta` viene del KML de Profeco y coincide con la terminal mas cercana en
apenas el 0.5 % de los casos (61 de 11,804). Esta desalineada; se conserva solo
como referencia.

## Ritmos

| Proceso | Frecuencia | Que actualiza |
|---|---|---|
| `descarga.py` (diario.yml) | 3 intentos al dia | precios del dia, catalogo, geografia |
| `kml.py` (semanal.yml) | lunes | precio de compra en TAR, margen, marca, grupo, clasificacion |

El KML de Profeco publica el promedio de la semana anterior, por eso el proceso
semanal corre en lunes. Cada archivo trae la columna `vigencia` con el rango de
fechas al que corresponden esos precios de compra.
