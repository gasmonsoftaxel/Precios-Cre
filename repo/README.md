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
