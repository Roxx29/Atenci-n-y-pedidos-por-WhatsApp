"""Ubicación del cliente: coordenadas del navegador o enlace de Google Maps. Las coordenadas salen siempre de código, nunca del modelo."""
import math
import re
import threading
import urllib.parse
import urllib.request

CENTRO = (7.95, -80.425)  # entre Chitré y Los Santos
RADIO_KM = 30
HOST_OK = re.compile(r"(^|\.)(google\.(com|com\.pa|pa)|goo\.gl|g\.co)$")
NUM = r"(-?\d{1,3}\.\d{3,})"
PATRONES = [re.compile(p) for p in (rf"!3d{NUM}!4d{NUM}", rf"@{NUM},{NUM}", rf"[?&](?:q|ll|query|destination|center)={NUM}(?:,|%2C){NUM}")]
MAX_SALTOS = 4


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def host_ok(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme == "https" and bool(p.hostname) and bool(HOST_OK.search(p.hostname))


def extraer(url):
    """(lat, lon) si la URL ya trae coordenadas; si no, None."""
    u = urllib.parse.unquote(url)
    for pat in PATRONES:
        if m := pat.search(u):
            return float(m.group(1)), float(m.group(2))
    return None


def _seguir(url, timeout=4):
    """Sigue las redirecciones de un enlace corto, solo por dominios de Google; devuelve la URL final."""
    abrir = urllib.request.build_opener(_SinRedireccion).open
    for _ in range(MAX_SALTOS):
        if extraer(url) or not host_ok(url):
            return url
        try:
            abrir(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout)
            return url  # respondió sin redirigir
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code not in (301, 302, 303, 307, 308) or not loc:
                return url
            url = urllib.parse.urljoin(url, loc)
        except OSError:
            return url
    return url


def distancia_km(a, b):
    (la1, lo1), (la2, lo2) = map(lambda p: map(math.radians, p), (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def zona(cfg):
    """Kwargs de validar() desde servicio.yaml: ubicacion: {centro: [lat, lon], radio_km: N}."""
    u = cfg.get("ubicacion") or {}
    return {"centro": tuple(u.get("centro", CENTRO)), "radio_km": u.get("radio_km", RADIO_KM)}


def validar(lat, lon):
    """(lat, lon) redondeadas a 6 decimales; ValueError si no son números dentro de rango. No juzga la distancia (ver fuera_de_zona)."""
    ok = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (lat, lon))
    if not (ok and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Ubicación no válida.")
    return round(float(lat), 6), round(float(lon), 6)


def fuera_de_zona(lat, lon, centro=CENTRO, radio_km=RADIO_KM):
    """True si las coordenadas quedan a más de radio_km del centro de entrega (solo aviso; la zona y el envío los decide crear_pedido)."""
    return distancia_km((lat, lon), centro) > radio_km


# --- posición en vivo del repartidor (dato personal): solo la última, en memoria; sin histórico, logs, IA ni coordenadas en auditoría ---
# Criterios legales: legal/2026-11-02-web-pedidos.md §2.10. Todo configurable aquí.
REP_INTERVALO_S = 5          # mínimo entre envíos de un mismo repartidor (si no, 429); el cliente envía cada 10-15 s y solo si se movió >20 m
REP_VIEJO_S = 120            # más antigua: viejo=True
REP_CADUCIDAD_S = 2 * 3600   # desde en_camino_at: pasado esto ni se guarda ni se sirve
REP_MARGEN_KM = 20           # se acepta hasta radio_km de la zona + este margen; más lejos es absurdo (400)
REP_MAX_PRECISION = 5000     # metros
REP_AVISO_VERSION = "v1"     # servicio.yaml repartidor: {aviso_ubicacion_version, aviso_ubicacion} lo sobrescribe
REP_AVISO = ("Mientras tengas un pedido en camino, esta app envía tu ubicación cada pocos segundos a {Restaurante}. La ven el personal del restaurante "
             "y el cliente de ese pedido, para saber cuándo llegas. Se apaga sola al entregar o cancelar, o al salir de la app. No guardamos tu recorrido: "
             "solo la última posición, que se borra al terminar. Fuera de un pedido en camino no se envía nada.")
REP_OTRA_PARADA = "Tu pedido va en camino; el repartidor tiene otra entrega antes"


class Posiciones:
    """{repartidor: (lat, lon, precision, t)}. Se borra al no quedarle pedidos en_camino vigentes (purgar) o al salir (quitar)."""

    def __init__(self):
        self.d, self.intento, self.lock = {}, {}, threading.Lock()

    def demasiado_pronto(self, nombre, ahora):
        with self.lock:
            if ahora - self.intento.get(nombre, -1e9) < REP_INTERVALO_S:
                return True
            self.intento[nombre] = ahora
            return False

    def poner(self, nombre, lat, lon, precision, ahora):
        """True si es la primera (ubicacion_on)."""
        with self.lock:
            nuevo = nombre not in self.d
            self.d[nombre] = (lat, lon, precision, ahora)
            return nuevo

    def quitar(self, nombre):
        """True si había posición (ubicacion_off)."""
        with self.lock:
            self.intento.pop(nombre, None)
            return self.d.pop(nombre, None) is not None

    def purgar(self, en_camino, ahora):
        """en_camino: repartidores con algún pedido en_camino vigente. (copia de lo que queda, nombres borrados)."""
        with self.lock:
            fuera = [n for n in self.d if n not in en_camino]
            for n in fuera:
                del self.d[n]
            for n in [n for n, t in self.intento.items() if ahora - t > REP_INTERVALO_S]:
                del self.intento[n]
            return dict(self.d), fuera


def vista_posicion(v, ahora):
    lat, lon, precision, t = v
    edad = max(0, int(ahora - t))
    return {"lat": lat, "lon": lon, "precision": precision, "edad_s": edad, "viejo": edad > REP_VIEJO_S}


def validar_posicion(d, cfg):
    """(lat, lon, precision) o ValueError: rango, precision 0..REP_MAX_PRECISION y a menos de radio_km + REP_MARGEN_KM del centro."""
    lat, lon = validar(d.get("lat"), d.get("lon"))
    pr = d.get("precision")
    if not (isinstance(pr, (int, float)) and not isinstance(pr, bool) and math.isfinite(pr) and 0 <= pr <= REP_MAX_PRECISION):
        raise ValueError("Precisión no válida.")
    z = zona(cfg)
    if distancia_km((lat, lon), z["centro"]) > z["radio_km"] + REP_MARGEN_KM:
        raise ValueError("Ubicación fuera de la zona de servicio.")
    return lat, lon, round(float(pr), 1)


def desde_enlace(enlace, resolver=None):
    """(lat, lon, url) de un enlace de Google Maps (largo o corto) o de "lat, lon". lat/lon son None si el enlace es de Google pero no trae
    coordenadas (lugar por nombre, búsqueda); url es la URL final de Google (None para "lat, lon"). ValueError si no es de Google."""
    enlace = str(enlace).strip()
    if m := re.fullmatch(rf"{NUM}\s*,\s*{NUM}", enlace):  # "8.0, -80.4" pegado a mano
        return float(m.group(1)), float(m.group(2)), None
    if not 8 <= len(enlace) <= 500 or not host_ok(enlace):
        raise ValueError("Pegue un enlace de Google Maps (https://maps.app.goo.gl/… o https://www.google.com/maps/…).")
    final = enlace if extraer(enlace) else (resolver or _seguir)(enlace)
    if not host_ok(final) or len(final) > 500 or urllib.parse.urlsplit(final).hostname.startswith("consent."):
        final = enlace  # redirección fuera de Google o a la página de consentimiento: se conserva el enlace original (de Google)
    return (*(extraer(final) or (None, None)), final)


def url_mapa(p):
    """Enlace de Google Maps para llegar al pedido: por coordenadas si las hay; si solo hay enlace de Google, ese enlace; si no, la dirección escrita."""
    u = p.get("ubicacion") or {}
    if u.get("lat") is None and u.get("enlace") and host_ok(u["enlace"]):
        return u["enlace"]
    destino = f"{u['lat']},{u['lon']}" if u.get("lat") is not None else " ".join(x for x in (p.get("direccion"), p.get("zona"), "Panamá") if x)
    return "https://www.google.com/maps/dir/?api=1&destination=" + urllib.parse.quote(destino, safe=",")
