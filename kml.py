#!/usr/bin/env python3
"""
Descarga el KML de Profeco y regenera los dos archivos que solo el KML puede dar:

  atributos_profeco.csv.gz  -> razon social, marca, grupo, clasificacion,
                               tipo de flete, fronterizo, etiqueta TAR
  precio_compra.csv.gz      -> precio de compra en TAR y margen, por estacion
                               y combustible, mas el precio del mapa

Corre una vez por semana. La geografia NO sale de aqui: eso lo hace geo.py con
el catalogo del INEGI, que es mas confiable y cubre las 15,036 estaciones.

Uso:
    python kml.py            # descarga y regenera
    python kml.py archivo.kml  # usa un KML local (para pruebas)
"""
import csv, gzip, io, os, re, sys, unicodedata, urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
MID  = "1yvHhc5KYiEEMMVKxzOMIUUKEuq1H7_c"
URL  = "https://www.google.com/maps/d/kml?mid=%s&forcekml=1" % MID
NS   = {"k": "http://www.opengis.net/kml/2.2"}

ATRIBUTOS = os.path.join(BASE, "atributos_profeco.csv.gz")
COMPRA    = os.path.join(BASE, "precio_compra.csv.gz")

ESTADOS = {
    "aguascalientes": "Aguascalientes", "baja california": "Baja California",
    "baja california sur": "Baja California Sur", "campeche": "Campeche",
    "chiapas": "Chiapas", "chihuahua": "Chihuahua",
    "ciudad de mexico": "Ciudad de México", "coahuila de zaragoza": "Coahuila de Zaragoza",
    "colima": "Colima", "durango": "Durango", "guanajuato": "Guanajuato",
    "guerrero": "Guerrero", "hidalgo": "Hidalgo", "jalisco": "Jalisco",
    "mexico": "México", "michoacan de ocampo": "Michoacán de Ocampo",
    "morelos": "Morelos", "nayarit": "Nayarit", "nuevo leon": "Nuevo León",
    "oaxaca": "Oaxaca", "puebla": "Puebla", "queretaro": "Querétaro",
    "quintana roo": "Quintana Roo", "san luis potosi": "San Luis Potosí",
    "sinaloa": "Sinaloa", "sonora": "Sonora", "tabasco": "Tabasco",
    "tamaulipas": "Tamaulipas", "tlaxcala": "Tlaxcala",
    "veracruz de ignacio de la llave": "Veracruz de Ignacio de la Llave",
    "yucatan": "Yucatán", "zacatecas": "Zacatecas",
}
MINUS = {"de", "del", "la", "las", "los", "y", "el"}


def _sin_acentos(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def titulo(s):
    partes = (s or "").strip().split()
    return " ".join(w.lower() if (i and w.lower() in MINUS) else w.capitalize()
                    for i, w in enumerate(partes))


def numero(t):
    try:
        return float(str(t).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def descargar(destino):
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(destino, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
    return os.path.getsize(destino)


def _escribir_gz(ruta, cols, filas):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(filas)
    with open(ruta, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as gz:
            gz.write(buf.getvalue().encode("utf-8"))


def procesar(ruta_kml):
    vigencia = ""
    atributos, compra = {}, {}
    vistos = set()

    for _, el in ET.iterparse(ruta_kml, events=("end",)):
        tag = el.tag.split("}")[-1]

        if tag == "description" and not vigencia:
            txt = (el.text or "")
            m = re.search(r"(\d{1,2}\s+al\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4})", txt)
            if m:
                vigencia = m.group(1)

        if tag != "Placemark":
            continue

        d = {}
        for dd in el.findall("k:ExtendedData/k:Data", namespaces=NS):
            d[dd.get("name")] = (dd.findtext("k:value", namespaces=NS) or "").strip()
        precio_mapa = (el.findtext("k:name", namespaces=NS) or "").strip()
        el.clear()

        cre = (d.get("número de permiso") or "").strip()
        comb = (d.get("Combustible") or "").strip().lower()
        if not cre:
            continue

        if cre not in atributos:
            atributos[cre] = {
                "cre_id": cre,
                "razon_social": titulo(d.get("razón social", "")),
                "marca": titulo(d.get("imagen comercial", "")),
                "grupo": titulo(d.get("grupo", "")),
                "clasificacion": titulo(d.get("clasificación", "")),
                "tipo_flete": titulo(d.get("tipo de flete", "")),
                "fronterizo": titulo(d.get("fronterizo", "")),
                "tar_etiqueta": (d.get("tar") or "").strip(),
                "estado_profeco": ESTADOS.get(_sin_acentos(d.get("entidad federativa", "")),
                                              titulo(d.get("entidad federativa", ""))),
                "municipio_profeco": titulo(d.get("municipio", "")),
                "vigencia": vigencia,
            }

        if comb:
            llave = "%s|%s" % (cre, comb)
            if llave in vistos:
                continue
            vistos.add(llave)
            compra[llave] = {
                "llave": llave, "cre_id": cre, "combustible": comb,
                "precio_compra_tar": numero(d.get("Precios de compra/Tar")),
                "margen_profeco": numero(d.get("Margen")),
                "precio_mapa": numero(precio_mapa),
                "vigencia": vigencia,
            }

    for r in atributos.values():
        r["vigencia"] = vigencia
    for r in compra.values():
        r["vigencia"] = vigencia

    _escribir_gz(ATRIBUTOS,
                 ["cre_id", "razon_social", "marca", "grupo", "clasificacion",
                  "tipo_flete", "fronterizo", "tar_etiqueta",
                  "estado_profeco", "municipio_profeco", "vigencia"],
                 sorted(atributos.values(), key=lambda r: r["cre_id"]))
    _escribir_gz(COMPRA,
                 ["llave", "cre_id", "combustible", "precio_compra_tar",
                  "margen_profeco", "precio_mapa", "vigencia"],
                 sorted(compra.values(), key=lambda r: r["llave"]))

    print("kml: %d estaciones · %d combinaciones estacion/combustible · vigencia: %s"
          % (len(atributos), len(compra), vigencia or "no detectada"))
    return len(atributos), len(compra)


def main():
    if len(sys.argv) > 1:
        return 0 if procesar(sys.argv[1])[0] else 1

    tmp = os.path.join(BASE, "_kml_tmp.kml")
    try:
        print("Descargando el KML de Profeco...")
        mb = descargar(tmp) / 1e6
        print("Descargado: %.1f MB" % mb)
        if mb < 5:
            print("ERROR: el archivo llegó incompleto; no se reescribe nada.",
                  file=sys.stderr)
            return 1
        procesar(tmp)
        return 0
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    sys.exit(main())
