"""Ubicación del cliente: coordenadas del navegador o enlace de Google Maps. Las coordenadas salen siempre de código, nunca del modelo."""
import math
import re
import urllib.parse
import urllib.request

CENTRO = (7.95, -80.425)  # entre Chitré y Los Santos
RADIO_KM = 30
HOST_OK = re.compile(r"(^|\.)(google\.(com|com\.pa|pa)|goo\.gl)$")
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


def validar(lat, lon, centro=CENTRO, radio_km=RADIO_KM):
    """(lat, lon) redondeadas a 6 decimales; ValueError con mensaje para el cliente si no son válidas o quedan lejos."""
    ok = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (lat, lon))
    if not (ok and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Ubicación no válida.")
    if distancia_km((lat, lon), centro) > radio_km:
        raise ValueError("Esa ubicación queda fuera de la zona de entrega (Chitré y Los Santos). Escriba la dirección o elija recogida.")
    return round(float(lat), 6), round(float(lon), 6)


def desde_enlace(enlace, resolver=_seguir):
    """(lat, lon) de un enlace de Google Maps (largo o corto). ValueError si no es de Google o no trae coordenadas."""
    enlace = str(enlace).strip()
    if m := re.fullmatch(rf"{NUM}\s*,\s*{NUM}", enlace):  # "8.0, -80.4" pegado a mano
        return float(m.group(1)), float(m.group(2))
    if not 8 <= len(enlace) <= 500 or not host_ok(enlace):
        raise ValueError("Pegue un enlace de Google Maps (https://maps.app.goo.gl/… o https://www.google.com/maps/…).")
    final = enlace if extraer(enlace) else resolver(enlace)
    coords = extraer(final) if host_ok(final) else None
    if not coords:
        raise ValueError("Ese enlace no trae la ubicación exacta. En Google Maps use «Compartir» sobre su ubicación actual.")
    return coords


def url_mapa(p):
    """Enlace de Google Maps para llegar al pedido: por coordenadas si las hay, si no por la dirección escrita."""
    u = p.get("ubicacion")
    destino = f"{u['lat']},{u['lon']}" if u else " ".join(x for x in (p.get("direccion"), p.get("zona"), "Panamá") if x)
    return "https://www.google.com/maps/dir/?api=1&destination=" + urllib.parse.quote(destino, safe=",")
