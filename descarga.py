#!/usr/bin/env python3
"""
Descarga los precios vigentes de la CNE/CRE y los archiva como CSV con fecha.
Pensado para correr una vez al dia (GitHub Actions, Raspberry Pi, cron, etc.).
Idempotente: si el archivo del dia ya existe, no lo reescribe.
"""
import csv, datetime, gzip, io, os, sys, urllib.request
import xml.etree.ElementTree as ET

import geo

PLACES = "https://publicacionexterna.azurewebsites.net/publicaciones/places"
PRICES = "https://publicacionexterna.azurewebsites.net/publicaciones/prices"
PRECIO_MIN, PRECIO_MAX = 10.0, 45.0
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historico")


def bajar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def main() -> int:
    cdmx = datetime.timezone(datetime.timedelta(hours=-6))
    fecha = datetime.datetime.now(cdmx).strftime("%Y-%m-%d")

    os.makedirs(DEST, exist_ok=True)
    salida = os.path.join(DEST, f"precios_{fecha}.csv.gz")
    if os.path.exists(salida):
        print(f"OMITIDO: ya existe {salida}")
        return 0

    print("Descargando catalogo...")
    places = ET.fromstring(bajar(PLACES))
    print("Descargando precios...")
    prices = ET.fromstring(bajar(PRICES))

    # place_id -> (cre_id, nombre, lat, lon)
    cat = {}
    for p in places.findall("place"):
        pid = p.get("place_id")
        cre = (p.findtext("cre_id") or "").strip()
        if not pid or not cre:
            continue
        loc = p.find("location")
        cat[pid] = (
            cre,
            (p.findtext("name") or "").strip(),
            (loc.findtext("y") if loc is not None else "") or "",
            (loc.findtext("x") if loc is not None else "") or "",
        )
    print(f"Catalogo: {len(cat)} estaciones")

    filas, vistos, fuera, sin_permiso = [], set(), 0, 0
    for p in prices.findall("place"):
        ent = cat.get(p.get("place_id"))
        if not ent:
            sin_permiso += 1
            continue
        cre = ent[0]
        for g in p.findall("gas_price"):
            tipo = (g.get("type") or "").strip().lower()
            if not tipo:
                continue
            try:
                valor = float((g.text or "").strip())
            except ValueError:
                continue
            if not (PRECIO_MIN <= valor <= PRECIO_MAX):
                fuera += 1
                continue
            if (cre, tipo) in vistos:      # ~136 pares duplicados en el feed
                continue
            vistos.add((cre, tipo))
            filas.append({"fecha": fecha, "cre_id": cre,
                          "combustible": tipo, "precio": f"{valor:.2f}"})

    if not filas:
        print("ERROR: no se obtuvo ninguna fila; no se escribe archivo.", file=sys.stderr)
        return 1

    filas.sort(key=lambda r: (r["cre_id"], r["combustible"]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["fecha", "cre_id", "combustible", "precio"],
                       lineterminator="\n")
    w.writeheader()
    w.writerows(filas)
    crudo = buf.getvalue().encode("utf-8")
    with open(salida, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as fh:
            fh.write(crudo)
    print(f"Comprimido: {len(crudo)/1e6:.2f} MB -> {os.path.getsize(salida)/1e6:.2f} MB")

    # catalogo: se reescribe solo si cambio
    cpath = os.path.join(os.path.dirname(DEST), "catalogo.csv")
    nuevo = [{"cre_id": c, "nombre": n, "latitud": la, "longitud": lo}
             for (c, n, la, lo) in sorted(cat.values())]
    anterior = None
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as fh:
            anterior = list(csv.DictReader(fh))
    if anterior != nuevo:
        with open(cpath, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["cre_id", "nombre", "latitud", "longitud"])
            w.writeheader()
            w.writerows(nuevo)
        print(f"Catalogo actualizado: {len(nuevo)} estaciones")

    # geografia: se recalcula siempre, para que las estaciones nuevas de la
    # CNE nunca aparezcan sin estado ni municipio
    try:
        geo.construir(nuevo)
    except Exception as e:              # nunca tumbar la descarga por esto
        print(f"AVISO: no se pudo regenerar la geografia: {e}", file=sys.stderr)

    print(f"OK: {len(filas)} filas -> {salida} "
          f"(descartados: {fuera} fuera de rango, {sin_permiso} sin permiso)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
