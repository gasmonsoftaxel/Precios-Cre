#!/usr/bin/env python3
"""
Corrige el precio de DIESEL del archivo del dia usando la API de consulta de
la CNE, para los estados listados en ESTADOS.

Por que: el feed XML tiene un solo tipo "diesel" y la API distingue subproducto
(Diesel, Diesel Automotriz, DUBA, Diesel Marino). En las estaciones que
reportan varios, el XML puede quedarse con el equivocado.

Regla para elegir UN precio de diesel por estacion:
  1. Se descarta "Diesel Marino": es combustible para embarcaciones, no se
     vende en la bomba y contamina cualquier comparativo de mercado.
  2. De lo que queda se toma el MAXIMO. En Chiapas, 4 de las 6 estaciones con
     varios subproductos los reportan al mismo precio, asi que la regla solo
     decide en los casos raros; se elige el mayor porque el precio publicado
     al consumidor es el del producto de especificacion mas alta.

No crea tablas nuevas: reescribe la columna precio del archivo
historico/precios_AAAA-MM-DD.csv.gz que ya genero descarga.py, y deja
constancia de cada cambio en detalle/correcciones_AAAA-MM-DD.csv.

Debe correr DESPUES de descarga.py.
"""
import csv, datetime, gzip, io, json, os, sys, time, urllib.error, urllib.request

BASE   = os.path.dirname(os.path.abspath(__file__))
HIST   = os.path.join(BASE, "historico")
DEST   = os.path.join(BASE, "detalle")
CAT    = "https://api-catalogo.cne.gob.mx"
REPORT = "https://api-reportediario.cne.gob.mx"

# Claves INEGI de dos digitos. "07" = Chiapas.
ESTADOS = ["07"]

PRECIO_MIN, PRECIO_MAX = 5.0, 60.0


def pedir(url, intentos=3):
    for n in range(intentos):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            if n == intentos - 1:
                print(f"   sin respuesta: {url} ({e})", file=sys.stderr)
                return None
            time.sleep(2 * (n + 1))
    return None


def diesel_por_estacion(estados):
    """{cre_id: (precio, subproducto)} con la regla descrita arriba."""
    mejor, fallos = {}, 0
    for ent in estados:
        mun = pedir(f"{CAT}/api/utiles/municipios?EntidadFederativaId={ent}") or []
        if not mun:
            print(f"ERROR: sin municipios para la entidad {ent}", file=sys.stderr)
            fallos += 1
            continue
        nombre = (mun[0].get("EntidadFederativa") or {}).get("Nombre", ent)
        print(f"{nombre}: consultando {len(mun)} municipios...")

        for m in mun:
            j = pedir(f"{REPORT}/api/EstacionServicio/Petroliferos"
                      f"?entidadId={int(ent)}&municipioId={int(m['MunicipioId'])}")
            if j is None:
                fallos += 1
                continue
            for x in (j.get("Value") or []):
                if (x.get("Producto") or "").strip() != "Diésel":
                    continue
                sub = (x.get("SubProducto") or "").strip()
                if "marino" in sub.lower():
                    continue
                try:
                    p = float(x.get("PrecioVigente"))
                except (TypeError, ValueError):
                    continue
                if not (PRECIO_MIN <= p <= PRECIO_MAX):
                    continue
                cre = (x.get("Numero") or "").strip()
                if cre and (cre not in mejor or p > mejor[cre][0]):
                    mejor[cre] = (p, sub)
    return mejor, fallos


def main():
    cdmx = datetime.timezone(datetime.timedelta(hours=-6))
    fecha = datetime.datetime.now(cdmx).strftime("%Y-%m-%d")
    archivo = os.path.join(HIST, f"precios_{fecha}.csv.gz")
    if not os.path.exists(archivo):
        print(f"ERROR: no existe {os.path.basename(archivo)}; "
              f"api_cne.py debe correr despues de descarga.py", file=sys.stderr)
        return 1

    api, fallos = diesel_por_estacion(ESTADOS)
    if not api:
        print("ERROR: la API no devolvio ningun precio; no se toca el archivo.",
              file=sys.stderr)
        return 1
    print(f"API: {len(api)} estaciones con diesel")

    with gzip.open(archivo, "rt", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))

    cambios, revisadas = [], 0
    for r in filas:
        if r["combustible"] != "diesel":
            continue
        dato = api.get(r["cre_id"])
        if not dato:
            continue
        revisadas += 1
        nuevo, sub = dato
        viejo = float(r["precio"])
        if abs(viejo - nuevo) >= 0.005:
            r["precio"] = f"{nuevo:.2f}"
            cambios.append({"fecha": fecha, "cre_id": r["cre_id"],
                            "precio_xml": f"{viejo:.2f}", "precio_api": f"{nuevo:.2f}",
                            "diferencia": f"{nuevo - viejo:+.2f}", "subproducto": sub})

    if not cambios:
        print(f"Sin diferencias: las {revisadas} estaciones revisadas ya coincidian.")
        return 0

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["fecha", "cre_id", "combustible", "precio"],
                       lineterminator="\n")
    w.writeheader()
    w.writerows(filas)
    with open(archivo, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as gz:
            gz.write(buf.getvalue().encode("utf-8"))

    os.makedirs(DEST, exist_ok=True)
    with open(os.path.join(DEST, f"correcciones_{fecha}.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["fecha", "cre_id", "precio_xml",
                                           "precio_api", "diferencia", "subproducto"])
        w.writeheader()
        w.writerows(cambios)

    print(f"OK: {len(cambios)} precios de diesel corregidos de {revisadas} revisados "
          f"({fallos} municipios sin respuesta)")
    for c in cambios[:10]:
        print(f"   {c['cre_id']:<24} {c['precio_xml']} -> {c['precio_api']}  "
              f"({c['diferencia']})  {c['subproducto'][:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
