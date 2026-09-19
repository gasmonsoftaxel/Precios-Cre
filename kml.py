#!/usr/bin/env python3
"""
Descarga el KML de Profeco y actualiza los dos archivos que solo el KML puede dar:

  atributos_profeco.csv.gz  -> razon social, domicilio, marca, grupo,
                               clasificacion, tipo de flete, fronterizo, TAR
  precio_compra.csv.gz      -> precio de compra en TAR y margen, por estacion
                               y combustible, mas el precio del mapa

Corre una vez por semana. La geografia NO sale de aqui: eso lo hace geo.py con
el catalogo del INEGI, que es mas confiable y cubre las 15,000+ estaciones.

FUSIONA, NO REEMPLAZA
---------------------
Profeco dejo de publicar Premium en el mapa en septiembre de 2026. Si cada
corrida reescribiera los archivos desde cero, esas 2,010 filas se perderian.
Por eso ahora se fusiona: lo que trae el KML nuevo pisa a lo viejo, y lo que
ya no trae se conserva tal cual, con la vigencia con la que se capturo. La
columna vigencia dice de que semana es cada fila.

GUARDAS
-------
El 14 de septiembre de 2026 una version anterior de este script sobrescribio
los dos archivos con archivos vacios: el KML bajo completo (paso la revision
de 5 MB) pero el parser no encontro un solo Placemark utilizable, y aun asi
escribio. Ahora se aborta antes de tocar nada si:
  - no se extrajo ni una estacion, o
  - se extrajo menos de la mitad de lo que ya habia.
Al abortar deja _kml_diagnostico.txt con las etiquetas y los nombres de campo
que si encontro.

Uso:
    python kml.py               # descarga y fusiona
    python kml.py archivo.kml   # usa un KML local (para pruebas)
    python kml.py --reemplazar  # NO fusiona: escribe solo lo que trae el KML
"""
import csv, collections, datetime, gzip, io, os, re, sys, unicodedata, urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
MID  = "1yvHhc5KYiEEMMVKxzOMIUUKEuq1H7_c"
URL  = "https://www.google.com/maps/d/kml?mid=%s&forcekml=1" % MID

ATRIBUTOS   = os.path.join(BASE, "atributos_profeco.csv.gz")
COMPRA      = os.path.join(BASE, "precio_compra.csv.gz")
DIAGNOSTICO = os.path.join(BASE, "_kml_diagnostico.txt")

# Si el KML trae menos de esta fraccion de lo que ya existe, algo se rompio.
PISO_MINIMO = 0.50

COLS_ATRIBUTOS = ["cre_id", "razon_social", "marca", "grupo", "clasificacion",
                  "tipo_flete", "fronterizo", "tar_etiqueta",
                  "estado_profeco", "municipio_profeco", "domicilio", "vigencia"]
COLS_COMPRA    = ["llave", "cre_id", "combustible", "precio_compra_tar",
                  "margen_profeco", "precio_mapa", "vigencia"]

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

# Nombre normalizado (sin acentos, minusculas) -> campo interno.
CAMPOS = {
    "numero de permiso": "cre_id", "num de permiso": "cre_id",
    "no de permiso": "cre_id", "permiso": "cre_id",
    "combustible": "combustible", "producto": "combustible",
    "razon social": "razon_social",
    "domicilio": "domicilio", "direccion": "domicilio",
    "imagen comercial": "marca", "marca": "marca",
    "grupo": "grupo",
    "clasificacion": "clasificacion",
    "tipo de flete": "tipo_flete",
    "fronterizo": "fronterizo",
    "tar": "tar_etiqueta",
    "entidad federativa": "entidad", "estado": "entidad",
    "municipio": "municipio",
    "precios de compra/tar": "precio_compra_tar",
    "precio de compra/tar": "precio_compra_tar",
    "precio de compra tar": "precio_compra_tar",
    "precios de compra tar": "precio_compra_tar",
    "margen": "margen",
}

VIGENCIA_PATRONES = [
    r"(\d{1,2}\s+al\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"(\d{1,2}\s+de\s+\w+\s+al\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"(\d{1,2}\s*[-/]\s*\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    r"(\d{1,2}/\d{1,2}/\d{4}\s*(?:al|a|-)\s*\d{1,2}/\d{1,2}/\d{4})",
]


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


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
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n",
                       extrasaction="ignore", restval="")
    w.writeheader()
    w.writerows(filas)
    with open(ruta, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as gz:
            gz.write(buf.getvalue().encode("utf-8"))


def _leer_gz(ruta):
    if not os.path.exists(ruta):
        return []
    try:
        with gzip.open(ruta, "rt", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


# --------------------------------------------------------------------------
# Extraccion
# --------------------------------------------------------------------------

def extraer(ruta_kml):
    """Lee el KML. Devuelve (atributos, compra, diagnostico). No escribe nada."""
    vigencia, primera_desc = "", ""
    atributos, compra, vistos = {}, {}, set()
    placemarks = con_permiso = 0
    campos_vistos = collections.Counter()
    etiquetas = collections.Counter()

    for _, el in ET.iterparse(ruta_kml, events=("end",)):
        tag = el.tag.split("}")[-1]
        etiquetas[tag] += 1

        if tag in ("description", "Snippet"):
            txt = el.text or ""
            if not primera_desc and txt.strip():
                primera_desc = txt.strip()[:400]
            if not vigencia:
                for pat in VIGENCIA_PATRONES:
                    m = re.search(pat, txt)
                    if m:
                        vigencia = m.group(1)
                        break

        if tag != "Placemark":
            continue

        placemarks += 1
        d = {}
        # Se busca Data a cualquier profundidad y con o sin namespace, por si
        # Google mueve el nodo o cambia el esquema.
        for dd in el.iter():
            if dd.tag.split("}")[-1] != "Data":
                continue
            bruto = dd.get("name") or ""
            campos_vistos[bruto] += 1
            clave = CAMPOS.get(_norm(bruto))
            if not clave:
                continue
            for hijo in dd:
                if hijo.tag.split("}")[-1] == "value":
                    d[clave] = (hijo.text or "").strip()
                    break

        precio_mapa = ""
        for hijo in el:
            if hijo.tag.split("}")[-1] == "name":
                precio_mapa = (hijo.text or "").strip()
                break
        el.clear()

        cre = (d.get("cre_id") or "").strip()
        if not cre:
            continue
        con_permiso += 1
        comb = _norm(d.get("combustible", ""))

        if cre not in atributos:
            atributos[cre] = {
                "cre_id": cre,
                "razon_social": titulo(d.get("razon_social", "")),
                "marca": titulo(d.get("marca", "")),
                "grupo": titulo(d.get("grupo", "")),
                "clasificacion": titulo(d.get("clasificacion", "")),
                "tipo_flete": titulo(d.get("tipo_flete", "")),
                "fronterizo": titulo(d.get("fronterizo", "")),
                "tar_etiqueta": (d.get("tar_etiqueta") or "").strip(),
                "estado_profeco": ESTADOS.get(_norm(d.get("entidad", "")),
                                              titulo(d.get("entidad", ""))),
                "municipio_profeco": titulo(d.get("municipio", "")),
                "domicilio": titulo(d.get("domicilio", "")),
                "vigencia": "",
            }

        if comb:
            llave = "%s|%s" % (cre, comb)
            if llave in vistos:
                continue
            vistos.add(llave)
            compra[llave] = {
                "llave": llave, "cre_id": cre, "combustible": comb,
                "precio_compra_tar": numero(d.get("precio_compra_tar")),
                "margen_profeco": numero(d.get("margen")),
                "precio_mapa": numero(precio_mapa),
                "vigencia": "",
            }

    if vigencia:
        etiqueta = vigencia
    else:
        cdmx = datetime.timezone(datetime.timedelta(hours=-6))
        etiqueta = "corte " + datetime.datetime.now(cdmx).strftime("%Y-%m-%d")
    for r in atributos.values():
        r["vigencia"] = etiqueta
    for r in compra.values():
        r["vigencia"] = etiqueta

    return atributos, compra, {
        "placemarks": placemarks, "con_permiso": con_permiso,
        "campos": campos_vistos, "etiquetas": etiquetas,
        "vigencia": vigencia, "descripcion": primera_desc,
    }


# --------------------------------------------------------------------------
# Fusion y guardas
# --------------------------------------------------------------------------

def fusionar(nuevas, viejas, clave):
    """Lo nuevo pisa a lo viejo; lo que ya no viene se conserva."""
    salida = {r[clave]: dict(r) for r in viejas if r.get(clave)}
    conservadas = len(set(salida) - set(nuevas))
    salida.update({k: dict(v) for k, v in nuevas.items()})
    return [salida[k] for k in sorted(salida)], conservadas


def _escribir_diagnostico(diag, motivos):
    with open(DIAGNOSTICO, "w", encoding="utf-8") as fh:
        fh.write("Diagnostico del KML de Profeco\n" + "=" * 60 + "\n\n")
        fh.write("Motivos por los que no se escribio:\n")
        for m in motivos:
            fh.write("  - %s\n" % m)
        fh.write("\nPlacemarks encontrados : %d\n" % diag["placemarks"])
        fh.write("Con numero de permiso  : %d\n" % diag["con_permiso"])
        fh.write("Vigencia detectada     : %s\n" % (diag["vigencia"] or "ninguna"))
        fh.write("\nPrimera <description> del archivo:\n  %s\n"
                 % (diag["descripcion"] or "(vacia)"))
        fh.write("\nEtiquetas XML mas frecuentes:\n")
        for t, n in diag["etiquetas"].most_common(20):
            fh.write("  %-24s %d\n" % (t, n))
        fh.write("\nNombres de campo en <Data name=...>:\n")
        if diag["campos"]:
            for c, n in diag["campos"].most_common(40):
                flag = "" if _norm(c) in CAMPOS else "   <-- NO RECONOCIDO"
                fh.write("  %-40s %6d%s\n" % (repr(c), n, flag))
        else:
            fh.write("  (ninguno: el KML ya no trae ExtendedData)\n")
    print("Diagnostico guardado en %s" % os.path.basename(DIAGNOSTICO))


def procesar(ruta_kml, fusiona=True):
    atributos, compra, diag = extraer(ruta_kml)
    viejas_a, viejas_c = _leer_gz(ATRIBUTOS), _leer_gz(COMPRA)

    print("kml: %d placemarks · %d con permiso · %d estaciones · %d combinaciones"
          % (diag["placemarks"], diag["con_permiso"], len(atributos), len(compra)))
    print("     vigencia: %s" % (diag["vigencia"] or "NO DETECTADA"))
    if compra:
        porc = collections.Counter(r["combustible"] for r in compra.values())
        print("     en el KML: %s"
              % " · ".join("%s %d" % (k, v) for k, v in porc.most_common()))

    motivos = []
    if not atributos:
        motivos.append("el KML no dio ni una estacion con numero de permiso")
    elif viejas_a and len(atributos) < PISO_MINIMO * len(viejas_a):
        motivos.append("solo %d estaciones contra %d que ya habia (menos del %d%%)"
                       % (len(atributos), len(viejas_a), PISO_MINIMO * 100))
    if motivos:
        print("\nABORTADO: no se sobrescribe nada. Motivos:", file=sys.stderr)
        for m in motivos:
            print("  - %s" % m, file=sys.stderr)
        print("  Los archivos anteriores quedan intactos.", file=sys.stderr)
        _escribir_diagnostico(diag, motivos)
        return None

    if fusiona:
        filas_a, cons_a = fusionar(atributos, viejas_a, "cre_id")
        filas_c, cons_c = fusionar(compra, viejas_c, "llave")
        if cons_a or cons_c:
            print("     conservadas del corte anterior: %d estaciones · %d combinaciones"
                  % (cons_a, cons_c))
    else:
        filas_a = [atributos[k] for k in sorted(atributos)]
        filas_c = [compra[k] for k in sorted(compra)]

    _escribir_gz(ATRIBUTOS, COLS_ATRIBUTOS, filas_a)
    _escribir_gz(COMPRA, COLS_COMPRA, filas_c)
    if os.path.exists(DIAGNOSTICO):
        os.remove(DIAGNOSTICO)

    final = collections.Counter(r["combustible"] for r in filas_c)
    print("OK: %d estaciones · %d combinaciones (%s)"
          % (len(filas_a), len(filas_c),
             " · ".join("%s %d" % (k, v) for k, v in final.most_common())))
    return len(filas_a), len(filas_c)


def main():
    args = sys.argv[1:]
    fusiona = "--reemplazar" not in args
    rutas = [a for a in args if not a.startswith("--")]

    if rutas:
        return 0 if procesar(rutas[0], fusiona) else 1

    tmp = os.path.join(BASE, "_kml_tmp.kml")
    try:
        print("Descargando el KML de Profeco...")
        mb = descargar(tmp) / 1e6
        print("Descargado: %.1f MB" % mb)
        if mb < 5:
            print("ERROR: el archivo llego incompleto; no se reescribe nada.",
                  file=sys.stderr)
            return 1
        return 0 if procesar(tmp, fusiona) else 1
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    sys.exit(main())
