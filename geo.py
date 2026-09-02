#!/usr/bin/env python3
"""
Reconstruye estaciones_geo.csv.gz a partir de:
  - el catalogo vivo de la CNE (place_id, cre_id, nombre, coordenadas)
  - inegi_localidades.csv.gz  -> estado y municipio por localidad mas cercana
  - atributos_profeco.csv.gz  -> marca, grupo, TAR, clasificacion, tipo de flete

La geografia se recalcula en cada corrida, asi que las estaciones nuevas de la
CNE nunca se quedan sin estado ni municipio. Los atributos de Profeco son
estaticos: se conservan para las estaciones que los tienen y quedan vacios para
las nuevas, hasta que se vuelva a procesar el KML.

Sin dependencias externas: usa una rejilla de 0.5 grados para prefiltrar
candidatos, asi que no hace falta numpy.
"""
import csv, gzip, math, os, collections

BASE = os.path.dirname(os.path.abspath(__file__))
LOCALIDADES = os.path.join(BASE, "inegi_localidades.csv.gz")
ATRIBUTOS   = os.path.join(BASE, "atributos_profeco.csv.gz")
TARS        = os.path.join(BASE, "tars.csv")
SALIDA      = os.path.join(BASE, "estaciones_geo.csv.gz")

COLS = ["cre_id","tipo_permiso","razon_social","cve_geo","cve_ent","cve_mun","estado","municipio",
        "latitud","longitud","km_localidad","geo_origen","en_mapa_profeco",
        "tar_cercana","tar_region","km_tar",
        "marca","grupo","clasificacion","tipo_flete","fronterizo","tar_etiqueta",
        "zona_estado","estado_profeco","municipio_profeco"]


def _leer_gz(ruta):
    with gzip.open(ruta, "rt", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    dl = (la2 - la1) * p
    dg = (lo2 - lo1) * p
    h = (math.sin(dl / 2) ** 2 +
         math.cos(la1 * p) * math.cos(la2 * p) * math.sin(dg / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


class Geocoder:
    """Localidad INEGI mas cercana, con rejilla de 0.5 grados."""

    def __init__(self, ruta=LOCALIDADES):
        self.loc = []
        self.rejilla = collections.defaultdict(list)
        for r in _leer_gz(ruta):
            try:
                la, lo = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError):
                continue
            i = len(self.loc)
            self.loc.append((la, lo, r["cve_ent"], r["cve_mun"], r["estado"], r["municipio"]))
            self.rejilla[(int(la * 2), int(lo * 2))].append(i)

    def _candidatos(self, la, lo):
        cx, cy = int(la * 2), int(lo * 2)
        for radio in (1, 2, 4, 8, 16, 32):
            idx = []
            for i in range(-radio, radio + 1):
                for j in range(-radio, radio + 1):
                    idx += self.rejilla.get((cx + i, cy + j), ())
            if len(idx) >= 30 or radio == 32:
                return idx
        return []

    def buscar(self, la, lo):
        mejor, dmin = None, float("inf")
        for i in self._candidatos(la, lo):
            p = self.loc[i]
            d = _km(la, lo, p[0], p[1])
            if d < dmin:
                dmin, mejor = d, p
        return mejor, dmin


def _tars(ruta=TARS):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as fh:
        return [(r["tar"], r["region"], float(r["lat"]), float(r["lon"]))
                for r in csv.DictReader(fh)]


def tar_mas_cercana(la, lo, tars):
    """Terminal de Almacenamiento y Reparto mas proxima en linea recta.

    OJO: es proximidad geografica, NO la terminal que realmente surte a la
    estacion. Esa asignacion es contractual y no la publica nadie.
    """
    mejor, dmin = None, float("inf")
    for nombre, region, tla, tlo in tars:
        d = _km(la, lo, tla, tlo)
        if d < dmin:
            dmin, mejor = d, (nombre, region)
    return mejor, dmin


def tipo_permiso(cre_id):
    """Separa las gasolineras publicas de las instalaciones privadas.

    ESA = Estacion de Servicio para Autoconsumo: flotas de empresas
          (Coppel, Barcel, mineras, lineas de autotransporte). No venden
          al publico y no compiten en el mercado.
    TRA = permisos de transporte.
    ES  = estacion de servicio al publico, que es la que interesa para
          cualquier analisis de precios de mercado.
    """
    c = (cre_id or "").upper()
    if "/ESA/" in c:
        return "Autoconsumo"
    if "/TRA/" in c:
        return "Transporte"
    if "/ES/" in c:
        return "Publico"
    return "Otro"


def coordenada_valida(la, lo):
    return la is not None and lo is not None and 14 < la < 33 and -119 < lo < -86


def construir(catalogo):
    """catalogo: lista de dicts con cre_id, nombre, latitud, longitud."""
    geo = Geocoder()
    tars = _tars()
    attrs = {r["cre_id"]: r for r in _leer_gz(ATRIBUTOS)} if os.path.exists(ATRIBUTOS) else {}

    filas, sin_coord, nuevas = [], 0, 0
    for x in catalogo:
        try:
            la, lo = float(x["latitud"]), float(x["longitud"])
        except (TypeError, ValueError):
            la = lo = None

        a = attrs.get(x["cre_id"])
        if a is None:
            nuevas += 1

        fila = {c: "" for c in COLS}
        fila["cre_id"] = x["cre_id"]
        fila["tipo_permiso"] = tipo_permiso(x["cre_id"])
        fila["razon_social"] = (a or {}).get("razon_social") or (x.get("nombre") or "").title()
        fila["en_mapa_profeco"] = "si" if a else "no"
        for c in ("marca", "grupo", "clasificacion", "tipo_flete", "fronterizo",
                  "tar_etiqueta", "zona_estado", "estado_profeco", "municipio_profeco"):
            fila[c] = (a or {}).get(c, "")

        if coordenada_valida(la, lo):
            p, d = geo.buscar(la, lo)
            fila["latitud"] = "%.6f" % la
            fila["longitud"] = "%.6f" % lo
            if p:
                fila["cve_ent"], fila["cve_mun"] = p[2], p[3]
                fila["cve_geo"] = p[2] + p[3]
                fila["estado"], fila["municipio"] = p[4], p[5]
                fila["km_localidad"] = "%.2f" % d
                fila["geo_origen"] = "inegi"
            if tars:
                t, dt = tar_mas_cercana(la, lo, tars)
                if t:
                    fila["tar_cercana"], fila["tar_region"] = t
                    fila["km_tar"] = "%.1f" % dt
            else:
                fila["geo_origen"] = "sin_referencia"
        else:
            sin_coord += 1
            fila["geo_origen"] = "sin_coordenadas"

        filas.append(fila)

    filas.sort(key=lambda f: (f["estado"], f["municipio"], f["cre_id"]))
    import io
    with open(SALIDA, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as gz:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            w.writerows(filas)
            gz.write(buf.getvalue().encode("utf-8"))

    tipos = collections.Counter(f["tipo_permiso"] for f in filas)
    print("geo: %d estaciones · %d sin coordenadas · %d nuevas sin atributos de Profeco"
          % (len(filas), sin_coord, nuevas))
    print("     por tipo de permiso: %s"
          % " · ".join("%s %d" % (k, v) for k, v in tipos.most_common()))
    return len(filas)
