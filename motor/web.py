"""Canal web: página de pedidos con chat (solo stdlib). Cookie firmada = identidad del cliente (cid = teléfono)."""
import base64
import datetime as dt
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import traceback
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import carta, clientes, mesa, paginas, panel_web, temas, ubicacion
from . import ruta as reparto
from . import herramientas
from .herramientas import dinero, limpiar_imagen, purgar_comprobantes, subir_comprobante, tipo_imagen
from .nucleo import Servicio

PAGINA = Path(__file__).with_name("web_index.html")
PAGINA_PANEL = Path(__file__).with_name("panel_web.html")
PAGINA_REP = Path(__file__).with_name("repartidor.html")
PAGINA_QR = Path(__file__).with_name("panel_qr.html")
MANIFEST_REP = {"name": "Repartidor", "short_name": "Repartidor", "start_url": "/repartidor/", "scope": "/repartidor/", "display": "standalone",
                "background_color": "#ffffff", "theme_color": "#111113", "icons": [{"src": "/repartidor/icono.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}
FUENTES = ("epilogue-v1.woff2", "jakarta-v1.woff2", "mono-v1.woff2")
LEAFLET = {"leaflet-1.9.4.js": "text/javascript", "leaflet-1.9.4.css": "text/css",
           **{f"images/{f}": "image/png" for f in ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png", "layers.png", "layers-2x.png")}}
ESTATICOS = {**{f: "font/woff2" for f in FUENTES}, **LEAFLET, "logo-delifood-v1.png": "image/png"}  # lista blanca de /estatico/ (motor/estatico/) -> Content-Type
ESTATICO = Path(__file__).with_name("estatico")
# sin caché de pedidos (no se guardan en el móvil); solo se guardan las fuentes (Leaflet no)
SW_REP = ("const C='fuentes-v1',F=%s.map(f=>'/estatico/'+f);self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(F)).catch(()=>{}))});"
          "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
          "self.addEventListener('fetch',e=>{if(F.includes(new URL(e.request.url).pathname))e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)))});" % list(FUENTES)).encode()
ICONO_REP = Path(__file__).with_name("repartidor_icono.svg")
MAX_CUERPO = 8192
MAX_IMAGEN = 5 * 1024 * 1024
TIMEOUT_S = 15  # socket de cada conexión: un cliente callado no retiene un hilo
VIDA_S = 24 * 3600
VISITA_S = 4 * 3600  # sin actividad más de 4 h = visita nueva: chat limpio
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' blob: data: https://tile.openstreetmap.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
GENERICA = "Disculpe, tuvimos un problema técnico. Intente de nuevo en unos minutos."


MARCA = "DeliFood"  # nombre escrito en las páginas; se cambia por `marca:` de servicio.yaml al servir
ENLACE_TEMA = b'<link rel="stylesheet" href="estatico/tema.css">'


def marca_de(sv):
    return str(sv.cfg.get("marca") or MARCA)


PAGINAS = {PAGINA: "web", PAGINA_PANEL: "panel", PAGINA_REP: "repartidor"}


def _componer(f, css, tema_id, marca):
    """HTML de una de las tres páginas con su <style> incrustado, data-tema en <html> y el nombre de marca."""
    return (f.read_bytes().replace(ENLACE_TEMA, b"<style>" + css + b"</style>", 1)
            .replace(b'<html lang="es"', b'<html lang="es" data-tema="%s"' % tema_id.encode(), 1)
            .replace(MARCA.encode(), html.escape(marca).encode()))


def armar_pagina(sv, f):
    """Página lista para servir: tema incrustado (servicios/<slug>/tema.css si existe; si no, el de motor/estatico), las reglas
    estructurales de su tema (temas.estilo), al final el personalizado.css de ESE cliente si lo tiene, y nombre de marca.
    Si el cliente tiene página propia (servicios/<slug>/web/), sale esa."""
    propia = paginas.propia(sv.dir, f)
    if propia is None and not f.is_file():
        return b""
    tema = sv.dir / "tema.css"
    css = (tema if tema.is_file() else ESTATICO / "tema.css").read_bytes()
    tema_id = temas.tema_de(sv.dir)["tema"]  # validado contra el catálogo: entra tal cual en el atributo
    propio = temas.estilo(tema_id, PAGINAS[f])  # vacío en el clásico: su página queda byte a byte como antes (más data-tema)
    css += b"\n" + propio.encode() if propio else b""
    try:  # se sanea también al servir: un archivo puesto a mano tampoco puede cerrar el <style>
        css += b"\n" + temas.sanear_css((sv.dir / "personalizado.css").read_text(encoding="utf-8")).encode()
    except (OSError, ValueError):  # sin archivo, ilegible o fuera de la lista: la página sale con su tema
        pass
    if propia is not None:
        return paginas.sustituir(propia, css, html.escape(marca_de(sv)).encode())
    return _componer(f, css, tema_id, marca_de(sv))


# ---------- Vista previa de un tema (consola del operador): página real, datos de ejemplo, sin red ni sesión ----------
MARCA_DEMO = "Sazón"
CSP_VISTA = CSP.replace("default-src 'self';", "default-src 'self'; connect-src 'none';").replace("frame-ancestors 'none'", "frame-ancestors 'self'")
# Corre antes que la página: activa su modo demostración (datos de ejemplo que ya trae), corta toda la red y entra sin PIN.
JS_VISTA = b"""<script>"use strict";window.VISTA_DEMO=true;(()=>{
const no=()=>Promise.resolve(new Response("{}",{status:404,headers:{"Content-Type":"application/json"}}));
window.fetch=no;window.EventSource=function(){return{close(){},addEventListener(){}}};navigator.sendBeacon=()=>false;
try{Object.defineProperty(navigator,"serviceWorker",{value:undefined})}catch(e){}
addEventListener("load",()=>{const f=document.getElementById("form-pin");if(f){document.getElementById("pin").value="000000";f.requestSubmit()}
setTimeout(()=>{const d=document.getElementById("dlg-aviso"),ok=document.getElementById("aviso-ok");if(d&&d.open&&ok){ok.click();document.getElementById("aviso-seguir").click()}},250)})})();</script>"""


def _foto_demo(tono, forma):
    """Ilustración SVG genérica de un plato (data:), sin fotos reales."""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 320'><rect width='320' height='320' fill='hsl({tono} 45% 82%)'/>"
           f"<circle cx='160' cy='170' r='118' fill='hsl({tono} 30% 96%)'/><circle cx='160' cy='170' r='86' fill='hsl({tono} 60% 58%)'/>{forma}</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


_F = ["<circle cx='130' cy='150' r='26' fill='hsl(40 80% 70%)'/><circle cx='190' cy='190' r='22' fill='hsl(120 40% 45%)'/>",
      "<rect x='100' y='140' width='120' height='50' rx='25' fill='hsl(25 60% 40%)'/>",
      "<path d='M110 200q50-90 100 0z' fill='hsl(45 85% 65%)'/>"]
MENU_DEMO = [dict(id=i, nombre=n, descripcion=d, precio=p, categoria=c, disponible=a, foto=_foto_demo(t, _F[k % 3]))
             for k, (i, n, d, p, c, a, t) in enumerate([
                 ("bowl", "Bowl de pollo a la plancha", "Arroz, aguacate, maíz y salsa de la casa", 8.50, "platos", True, 20),
                 ("pasta", "Pasta al pesto", "Albahaca fresca, parmesano y piñones", 7.75, "platos", True, 110),
                 ("hamburguesa", "Hamburguesa clásica", "Carne de res, queso, tomate y papas", 9.25, "platos", True, 10),
                 ("pescado", "Filete de pescado", "Con puré de yuca y ensalada verde", 11.00, "platos", True, 200),
                 ("empanadas", "Empanadas (3 u.)", "De carne o de queso", 3.50, "entradas", True, 35),
                 ("ensalada", "Ensalada fresca", "Hojas verdes, pepino y vinagreta", 5.25, "entradas", True, 140),
                 ("limonada", "Limonada con hierbabuena", "", 2.50, "bebidas", True, 70),
                 ("flan", "Flan de coco", "", 3.75, "postres", False, 300)])]


def armar_vista(tema_id, pagina):
    """Vista previa de un tema: la página REAL con ese tema, la marca de ejemplo y datos ficticios. No lee ningún cliente."""
    temas.estilo(tema_id, pagina)  # valida tema y página (ValueError)
    f = {v: k for k, v in PAGINAS.items()}[pagina]
    css = (temas.generar_tema(tema_id) + "\n" + temas.estilo(tema_id, pagina)).encode()
    h = _componer(f, css, tema_id, MARCA_DEMO).replace(b"<head>", b"<head>" + JS_VISTA, 1)
    if pagina == "web":  # el menú de ejemplo, con fotos ilustradas
        h = re.sub(rb'(<script type="application/json" id="datos-menu">).*?(</script>)',
                   lambda m: m[1] + json.dumps(MENU_DEMO, ensure_ascii=False).encode() + m[2], h, count=1, flags=re.S)
    return h


def config_publica(sv, con_pago=False):
    """Lo que la web de pedidos muestra del restaurante (sale de servicio.yaml, no se escribe en el HTML). Los datos de pago solo con sesión de cliente."""
    p = sv.cfg.get("pedidos") or {}
    e = p.get("envio") or {}
    zonas, costo = e.get("zonas"), e.get("costo") or 0  # mismas tres formas que herramientas._envio: por zona, lista de zonas a costo fijo, o costo fijo ("")
    envio = dict(zonas) if isinstance(zonas, dict) else {z: costo for z in zonas} if zonas else {"": costo} if costo else {}
    c = sv.cfg
    d = {"marca": marca_de(sv), "horario": c.get("horario") or {}, "impuesto_pct": p.get("impuesto_pct") or 0, "envio": envio,
         "telefono": c.get("telefono") or "", "anuncio": c.get("anuncio") or "", "cerrado": bool(c.get("cerrado")), "efectivo": herramientas.efectivo_ok(sv)}
    if con_pago:
        d["pago"] = {k: v for k, v in (sv.cfg.get("pago") or {}).items() if k in ("yappy", "transferencia") and v}
    return d


def secreto_web(carpeta):
    if s := os.environ.get("WEB_SECRET"):
        return s
    f = Path(carpeta) / ".web_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


def firmar(secreto, cid, exp):
    return f"{cid}.{exp}." + hmac.new(secreto.encode(), f"{cid}.{exp}".encode(), hashlib.sha256).hexdigest()


def cid_de_cookie(secreto, cabecera):
    """cid si la cookie 'sesion' es válida y no ha expirado; si no, None."""
    for parte in (cabecera or "").split(";"):
        k, _, v = parte.strip().partition("=")
        if k == "sesion":
            try:
                cid, exp, _firma = v.split(".")
                ok = hmac.compare_digest(v.encode(), firmar(secreto, cid, exp).encode()) and int(exp) > time.time()
            except ValueError:
                return None
            return cid if ok else None
    return None


def enmascarar(tel):
    """+507 6***-**12: nunca se imprime el teléfono completo."""
    return panel_web._mask(tel, con_prefijo=True)


def aviso_de(sv):
    """(versión, texto) del aviso de privacidad desde servicio.yaml: web: {aviso_version, aviso}."""
    w = sv.cfg.get("web") or {}
    f = sv.dir / w.get("aviso", "aviso.md")
    if w.get("aviso_version") and f.is_file():
        return str(w["aviso_version"]), f.read_text(encoding="utf-8")
    return "sin-aviso", "Aviso de privacidad pendiente"


def formato_pedido(p):
    return {"id": p["id"], "estado": p["estado"], "modalidad": p["modalidad"],
            "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], "precio": dinero(l["precio_c"])} for l in p["lineas"]], "ubicacion": bool(p.get("ubicacion")),
            "subtotal": dinero(p["subtotal_c"]), "impuesto": dinero(p["itbms_c"]), "envio": dinero(p["envio_c"]), "total": dinero(p["total_c"]),
            "por_cobrar": bool(p.get("por_cobrar")),
            **({"mesa": p["mesa"], "espera_personal": bool(p.get("espera_personal"))} if p.get("mesa") else {})}  # solo en pedidos de mesa: la API de los demás no cambia


class Limite:
    """Ventana deslizante de 60 s en memoria; borra claves viejas para no crecer sin fin."""

    def __init__(self):
        self.d, self.lock = defaultdict(list), threading.Lock()

    def excede(self, clave, maximo):
        ahora = time.time()
        with self.lock:
            if len(self.d) > 1000:
                for k in [k for k, v in self.d.items() if not v or v[-1] < ahora - 60]:
                    del self.d[k]
            v = self.d[clave] = [t for t in self.d[clave] if t > ahora - 60]
            if len(v) >= maximo:
                return True
            v.append(ahora)
            return False


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, codigo, cuerpo, tipo="application/json", extra=()):
        if not isinstance(cuerpo, bytes):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _err(self, codigo, msg):
        self.close_connection = True
        if not self.__dict__.get("_leido"):  # leer el cuerpo antes de cerrar: si no, Windows manda RST y el cliente pierde la respuesta
            try:
                largo = int(self.headers.get("Content-Length") or 0)
                if 0 < largo <= 65536:  # los cuerpos enormes no se esperan
                    self.rfile.read(largo)
            except (OSError, ValueError):
                pass
        self._leido = True
        self._enviar(codigo, {"error": msg})


def crear_servidor(sv, puerto=8080, secreto=None):
    return ThreadingHTTPServer(("0.0.0.0", puerto), crear_manejador(sv, secreto))


def crear_manejador(sv, secreto=None, env=None):
    """La clase que atiende a UN servicio; todo su estado (sesiones, límites, posiciones) queda cerrado sobre `sv`.
    `env`: de dónde salen PIN, repartidores y lat/lon (por defecto os.environ; en `alojar`, solo las variables de ese cliente)."""
    secreto, env = secreto or secreto_web(sv.dir), os.environ if env is None else env
    usuarios = panel_web.Usuarios(sv, panel_web.PANEL, env)  # un PIN por persona (tabla usuarios; semilla PANEL_PIN*)
    acceso, bloqueo = panel_web.Acceso(secreto, usuarios), panel_web.Bloqueo()
    repartidores = panel_web.Usuarios(sv, panel_web.REPARTO, env)  # semilla REPARTIDORES; se gestionan desde el panel
    acc_rep = panel_web.Acceso(secreto, repartidores, "repartidor", "/repartidor", inactivo=panel_web.VIDA_S)  # sesión de turno: 12 h, sin bloqueo por inactividad
    bloqueo_rep, ubic_pend = panel_web.Bloqueo(), {}  # ubic_pend: cid -> ubicación recibida antes de tener pedido
    limite, nombres = Limite(), {}  # nombres: cid -> nombre pendiente de anteponer al primer mensaje
    pos = ubicacion.Posiciones()  # última posición de cada repartidor en camino (solo memoria)

    def aviso_rep(nombre):
        """{version, texto, aceptado} del aviso de ubicación del repartidor (servicio.yaml repartidor: {aviso_ubicacion_version, aviso_ubicacion})."""
        r = sv.cfg.get("repartidor") or {}
        v = str(r.get("aviso_ubicacion_version", ubicacion.REP_AVISO_VERSION))
        fila = sv._q("SELECT version FROM aviso_repartidor WHERE nombre=?", (nombre,))
        return {"version": v, "texto": r.get("aviso_ubicacion", ubicacion.REP_AVISO).replace("{Restaurante}", sv.cfg.get("nombre") or "el restaurante"),
                "aceptado": bool(fila) and fila[0][0] == v}

    def restaurante():
        try:  # RESTAURANTE_LAT/LON (.env): opcional; sin ellos o inválidos, None
            return ubicacion.validar(float(env["RESTAURANTE_LAT"]), float(env["RESTAURANTE_LON"]))
        except (KeyError, ValueError):
            return None

    def en_ruta():
        """(repartidor -> pedidos en_camino vigentes, en orden de parada; posiciones que quedan; ahora; repartidor -> ruta). Borra (y audita) las
        posiciones que ya no tocan. La ruta (motor/ruta.py) se calcula aquí cada vez, sobre sus pedidos listos y en camino: sale de su posición
        si es reciente; si no, del restaurante.
        ponytail: un `listo` asignado a mitad de viaje se ordena como si ya lo llevara encima; separar viajes si molesta."""
        rutas, carga, ahora = {}, {}, sv.ahora()
        for p in sv.listar("pedido"):
            desde = p.get("en_camino_at") or p.get("estado_at")
            if p["estado"] == "en_camino" and p.get("repartidor") and not (desde and (ahora - dt.datetime.fromisoformat(desde)).total_seconds() > ubicacion.REP_CADUCIDAD_S):
                rutas.setdefault(p["repartidor"], []).append(p)
                carga.setdefault(p["repartidor"], []).append(p)
            elif p["estado"] == "listo" and p.get("repartidor"):
                carga.setdefault(p["repartidor"], []).append(p)
        t = time.time()
        quedan, fuera = pos.purgar(set(rutas), t)
        for n in fuera:
            panel_web.auditar(sv, n, "ubicacion_off")
        plan = {}
        for n, ps in carga.items():
            v = quedan.get(n)
            plan[n] = reparto.de_pedidos(ps, v[:2] if v and t - v[3] <= ubicacion.REP_VIEJO_S else restaurante(), ahora, sv._cfg)  # _cfg: sin los ajustes del dueño
            rutas.get(n, []).sort(key=lambda p: reparto.parada(plan[n], p["id"])["n"])
        return rutas, quedan, t, plan

    def mapa_panel():
        rutas, quedan, t, _ = en_ruta()
        r = restaurante()
        rest = {"lat": r[0], "lon": r[1], "nombre": sv.cfg.get("nombre") or "Restaurante"} if r else None
        return {"restaurante": rest, "repartidores": [{"repartidor": n, "pedido_id": p["id"], **ubicacion.vista_posicion(quedan[n], t)} for n, ps in rutas.items() if n in quedan for p in ps]}

    def mapa_cliente(p, rutas, quedan, t, plan):
        """Campos extra del pedido en_camino del cliente: nombre de pila, su número de parada y minutos estimados; posición solo si es la
        siguiente parada (si no, mensaje). Nunca la ruta ni las otras paradas."""
        n = p.get("repartidor")
        par = reparto.parada(plan.get(n), p["id"]) if any(x["id"] == p["id"] for x in rutas.get(n, ())) else None  # caducado: sin dato
        d = {"repartidor": re.match(r"[A-Za-z0-9]*", n or "").group() or None, "repartidor_pos": None, "mensaje": None,
             "parada": par and par["n"], "eta_min": par and par["eta_min"]}
        if par and par["n"] > 1:
            d["mensaje"] = ubicacion.REP_OTRA_PARADA
        elif par and n in quedan:
            d["repartidor_pos"] = ubicacion.vista_posicion(quedan[n], t)
        return d

    def mesas_on():
        return "mesas" in panel_web.modulos(sv)

    def armar_qr():
        """Página de gestión de QR del dueño: la del motor con el tema de ESTE cliente."""
        tema = sv.dir / "tema.css"
        return _componer(PAGINA_QR, (tema if tema.is_file() else ESTATICO / "tema.css").read_bytes(), temas.tema_de(sv.dir)["tema"], marca_de(sv))

    corte = {}  # cid -> id de pedido hasta el que (inclusive) ya no se muestra si está cerrado  # ponytail: en memoria; tras reiniciar reaparece el último cerrado

    def pedidos_de(cid):
        return [p for p in sv.listar("pedido") if p["telefono"] == cid]

    class H(_Base):
        timeout = TIMEOUT_S  # se lee al crear el manejador

        def _del_tunel(self):
            """TUNEL=1 y la conexión viene de este mismo PC (cloudflared): se pueden creer sus cabeceras CF-*."""
            return os.environ.get("TUNEL") == "1" and self.client_address[0] in ("127.0.0.1", "::1")

        def _proxy(self):
            """PROXY=1 (Render/Fly): todo llega por el proxy de la plataforma, así que sus cabeceras se creen."""
            return os.environ.get("PROXY") == "1"

        def _ip(self):
            """IP real: CF-Connecting-IP solo si llega por el túnel (si no, cualquiera de la LAN podría falsearla).
            Con PROXY=1: Fly-Client-IP solo en Fly (FLY_APP_NAME; en otro sitio el cliente la puede escribir) o, si no, el X-Forwarded-For
            contando desde la DERECHA (lo que añade el proxy de confianza; el primer valor lo escribe el cliente). PROXY_SALTOS = proxies de confianza (1)."""
            if self._proxy():
                try:
                    if os.environ.get("FLY_APP_NAME") and self.headers.get("Fly-Client-IP"):
                        return str(ipaddress.ip_address(self.headers["Fly-Client-IP"].strip()))
                    xff = [x.strip() for x in (self.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
                    return str(ipaddress.ip_address(xff[-max(1, int(os.environ.get("PROXY_SALTOS") or 1))]))
                except (ValueError, IndexError):
                    pass
            if self._del_tunel():
                try:
                    return str(ipaddress.ip_address((self.headers.get("CF-Connecting-IP") or "").strip()))
                except ValueError:
                    pass
            return self.client_address[0]

        def _seguro(self):
            """Cookie Secure si el cliente llegó por https a través del túnel."""
            if self._proxy():
                return "https" in (self.headers.get("X-Forwarded-Proto") or "").lower()
            return self._del_tunel() and "https" in ((self.headers.get("X-Forwarded-Proto") or "") + (self.headers.get("CF-Visitor") or "")).lower()

        def _cid(self):
            return cid_de_cookie(secreto, self.headers.get("Cookie"))

        def _base(self):
            """https://dominio de esta petición (para las URL de los QR). El Host solo entra si tiene forma de nombre de host."""
            host = (self.headers.get("Host") or "").strip().lower()
            return ("https" if self._seguro() else "http") + "://" + (host if re.fullmatch(r"[a-z0-9.-]{1,253}(:\d{1,5})?", host) else "localhost")

        def _pagina(self, codigo, titulo, texto=""):
            """Página mínima para quien escanea un QR (móvil): sin datos del restaurante más allá de su marca."""
            cuerpo = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(titulo)}</title>'
                      f'<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;font:18px/1.5 system-ui,sans-serif;text-align:center;padding:24px}}h1{{font-size:1.6rem}}</style></head>'
                      f'<body><main><h1>{html.escape(titulo)}</h1><p>{html.escape(texto)}</p><small>{html.escape(marca_de(sv))}</small></main></body></html>').encode()
            self._enviar(codigo, cuerpo, "text/html", [("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")])

        def _escaneo(self, codigo):
            """GET /m/<codigo>: la URL estable que lleva el QR. Cuenta el escaneo y lleva al destino que el dueño tiene puesto AHORA (sin cachés)."""
            f = mesa.por_codigo(sv, codigo) if mesas_on() else None
            if limite.excede(("m", self._ip()), 30) or limite.excede(("m", codigo), 120):
                return self._pagina(429, "Demasiados intentos", "Espere un momento e inténtelo de nuevo.")
            if not f:
                return self._pagina(404, "Código no válido", "Este código QR ya no está activo.")
            mesa.contar(sv, f["id"])
            ir = lambda url: self._enviar(302, b"", "text/plain", [("Location", url), ("Referrer-Policy", "no-referrer")])
            if f["tipo"] == "pedir":
                return ir("/?m=" + codigo)
            if f["tipo"] == "menu":
                return ir("/?solo=menu")
            if f["tipo"] in ("promo", "resena"):
                return ir(f["destino"]) if mesa.URL.fullmatch(f["destino"]) else self._pagina(200, "Promoción del día", f["destino"])
            self._pagina(200, mesa.MSG_PAUSA, "Pregunte al personal.")

        def _json(self, tope=MAX_CUERPO):
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._err(415, "Se requiere application/json")
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "Cuerpo inválido")
            if not 0 < largo <= tope:  # antes de leer
                return self._err(413, "Cuerpo demasiado grande")
            try:
                self._leido = True
                crudo = self.rfile.read(largo)
            except OSError:
                return self._err(400, "Cuerpo incompleto")
            if len(crudo) != largo:
                return self._err(400, "Cuerpo incompleto")
            try:
                d = json.loads(crudo)
            except ValueError:
                return self._err(400, "JSON inválido")
            return d if isinstance(d, dict) else self._err(400, "JSON inválido")

        def _panel(self, metodo, ruta):
            """Rutas /panel*: solo LAN (o PANEL_PUBLICO=1) y solo con PIN configurado; el resto de peticiones ni lo ven.
            PANEL_PUBLICO: en `motor web`, la del proceso. En `alojar` se fija por cliente con PANEL_PUBLICO_<SLUG> (1 abre, 0 cierra)
            y la del proceso es solo el valor por defecto de quien no tenga la suya."""
            ip = self._ip()
            if not (acceso.activo and ((panel_web.es_lan(ip) and not self._proxy()) or env.get("PANEL_PUBLICO", os.environ.get("PANEL_PUBLICO")) == "1")):
                return self._err(404, "No encontrado")
            if ruta == "/panel" and metodo == "GET":
                return self._enviar(200, armar_pagina(sv, PAGINA_PANEL), "text/html", [("Content-Security-Policy", paginas.csp(sv, PAGINA_PANEL, CSP))])  # en cada visita: el nombre se cambia desde el panel
            if ruta == "/panel/entrar" and metodo == "POST":
                if bloqueo.bloqueada(ip):
                    return self._err(429, "Demasiados intentos; espere 5 minutos")
                d = self._json()
                if d is None:
                    return
                pin = d.get("pin")
                time.sleep(bloqueo.retraso())  # antes de comprobar: si solo esperase el fallo, la tardanza delataría el PIN malo
                quien = acceso.rol_de_pin(pin) if isinstance(pin, str) and len(pin) <= 64 else None
                if not quien:
                    bloqueo.fallo(ip)
                    panel_web.auditar(sv, "?", "login_fallido")
                    return self._err(401, "PIN incorrecto")
                bloqueo.acierto(ip)
                panel_web.auditar(sv, quien, "login")
                return self._enviar(200, {"ok": True, "nombre": quien, "rol": usuarios.rol(quien)}, extra=[("Set-Cookie", acceso.cookie(quien, seguro=self._seguro()))])
            ses = acceso.sesion(self.headers.get("Cookie"))
            if not ses:
                return self._err(401, "Sesión inválida")
            quien, lim = ses
            rol = usuarios.rol(quien)
            renovar = [("Set-Cookie", acceso.cookie(quien, lim, self._seguro()))]
            if ruta == "/panel/salir" and metodo == "POST":
                panel_web.auditar(sv, quien, "salir")
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", acceso.borrar_cookie(self._seguro()))])
            partes = ruta.split("/")  # ['', 'panel', 'estado'] ...
            if partes[:3] == ["", "panel", "qr"] and metodo == "GET":  # página de QR del dueño: hoja imprimible y descarga SVG
                return self._qr(partes, rol, renovar)
            try:
                if ruta == "/panel/estado" and metodo == "GET":
                    desde = int((parse_qs(urlsplit(self.path).query).get("desde") or ["0"])[0])
                    return self._enviar(200, panel_web.estado_panel(sv, desde, repartidores, en_ruta()[3]), extra=renovar)
                if len(partes) == 4 and partes[2] in ("pedido", "comprobante") and metodo == "GET":
                    pid = int(partes[3])
                    if partes[2] == "pedido":
                        p = next((p for p in sv.listar("pedido") if p["id"] == pid), None)
                        return self._enviar(200, {"pedido": panel_web.pedido_json(p, True)}, extra=renovar) if p else self._err(404, "No existe ese pedido")
                    img = panel_web.comprobante(sv, pid)
                    if not img:
                        return self._err(404, "Sin comprobante")
                    panel_web.auditar(sv, quien, "ver_comprobante", pid)
                    return self._enviar(200, img[0], img[1], renovar)
                if ruta == "/panel/marcar" and metodo == "POST":
                    d = self._json()
                    if d is None:
                        return
                    pid, est = d.get("pedido_id"), d.get("estado")
                    if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(est, str)):
                        return self._err(400, "Datos inválidos")
                    try:
                        p = panel_web.marcar(sv, pid, est, quien)
                    except ValueError as e:
                        return self._err(409, str(e))
                    panel_web.auditar(sv, quien, est, pid)
                    en_ruta()  # entregado/cancelado: borra la posición si ya no le quedan en camino
                    return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_json(p)}, extra=renovar)
                if ruta == "/panel/asignar" and metodo == "POST":
                    d = self._json()
                    if d is None:
                        return
                    pid, rep = d.get("pedido_id"), d.get("repartidor", "")
                    if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(rep, str)):
                        return self._err(400, "Datos inválidos")
                    try:
                        p = panel_web.asignar(sv, pid, rep, repartidores)
                    except ValueError as e:
                        return self._err(409, str(e))
                    panel_web.auditar(sv, quien, "asignar", pid)
                    en_ruta()  # reasignado
                    return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_json(p)}, extra=renovar)
                if ruta == "/panel/repartidores_mapa" and metodo == "GET":
                    return self._enviar(200, mapa_panel(), extra=renovar)
                if ruta == "/panel/menu" and metodo == "GET":  # todo el panel ve el menú; editarlo es del dueño
                    return self._enviar(200, {"menu": panel_web.menu_json(sv)}, extra=renovar)
                if ruta == "/panel/agotado" and metodo == "POST":  # agotado/disponible: también el personal
                    d = self._json()
                    if d is None:
                        return
                    try:
                        return self._enviar(200, panel_web.poner_disponible(sv, quien, d.get("plato"), d.get("disponible")), extra=renovar)
                    except LookupError:
                        return self._err(404, "No existe ese plato")
                    except ValueError as e:
                        return self._err(400, str(e))
                if ruta == "/panel/tienda" and metodo == "POST":  # cerrar o abrir a mano: también el personal
                    d = self._json()
                    if d is None:
                        return
                    try:
                        return self._enviar(200, panel_web.poner_cerrado(sv, quien, d.get("cerrado")), extra=renovar)
                    except ValueError as e:
                        return self._err(400, str(e))
                if len(partes) == 5 and partes[2] == "escalado" and partes[4] == "atendido" and metodo == "POST":
                    try:
                        panel_web.atender_escalado(sv, int(partes[3]))
                    except ValueError as e:
                        return self._err(409, str(e))
                    return self._enviar(200, {"ok": True}, extra=renovar)
                if len(partes) == 4 and partes[2] == "dueno":  # ajustes: usuarios y menú (solo rol dueno)
                    if rol != "dueno":
                        return self._err(403, "Solo el dueño puede hacer esto")
                    if (metodo, partes[3]) == ("POST", "foto"):
                        return self._subir_foto(quien, renovar)
                    if (metodo, partes[3]) in (("POST", "carta_foto"), ("POST", "carta_publicar")):
                        return self._carta(partes[3], quien, renovar)
                    if partes[3] == "preguntar" and limite.excede(("preguntar", quien), 10):  # cada pregunta es una llamada al modelo
                        return self._err(429, "Demasiadas preguntas seguidas; espera un minuto.")
                    if (metodo, partes[3]) == ("POST", "qr_logo") and mesas_on():
                        return self._subir_logo(quien, renovar)
                    d = self._json() if metodo == "POST" else {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    if d is None:
                        return
                    d["_base"] = self._base()  # lo pone el servidor, pise lo que pise el cliente
                    try:
                        return self._enviar(200, panel_web.api_dueno(sv, quien, rol, metodo, partes[3], d), extra=renovar)
                    except LookupError:
                        return self._err(404, "No encontrado")
                    except ValueError as e:
                        return self._err(409, str(e))
            except ValueError:  # int() de la ruta
                return self._err(400, "Datos inválidos")
            self._err(404, "No encontrado")

        def _carta(self, accion, quien, renovar):
            """Carta por foto (motor/carta.py): carta_foto {fotos: [base64]} → borrador; carta_publicar {platos} → menú."""
            foto = accion == "carta_foto"
            if foto and limite.excede(("carta", self._ip()), 5):  # cada lectura es una llamada al modelo
                return self._err(429, "Demasiadas cartas seguidas, espere un minuto")
            d = self._json(carta.MAX_CUERPO if foto else carta.MAX_CUERPO_PUBLICAR)
            if d is None:
                return
            try:
                r = carta.extraer(sv, carta.de_base64(d.get("fotos"))) if foto else carta.publicar(sv, quien, d.get("platos"))
            except carta.Tope as e:
                return self._err(429, str(e))
            except carta.SinVision as e:
                return self._err(503, str(e))
            except panel_web.TipoNoValido as e:
                return self._err(415, str(e))
            except ValueError as e:
                return self._err(400, str(e))
            return self._enviar(200, r, extra=renovar)
        def _qr(self, partes, rol, renovar):
            if rol != "dueno":
                return self._err(403, "Solo el dueño puede hacer esto")
            if not mesas_on():
                return self._err(404, "No encontrado")
            if partes == ["", "panel", "qr"]:
                return self._enviar(200, armar_qr(), "text/html", [("Content-Security-Policy", CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")), *renovar])  # frame-ancestors 'self': va dentro de la pestaña QR del panel
            est, logo, base = mesa.estilo(sv), mesa.logo_uri(sv), self._base()
            if len(partes) == 5 and partes[3] == "svg" and partes[4].isdigit():  # un QR para descargar
                f = next((c for c in mesa.listar(sv, base) if c["id"] == int(partes[4])), None)
                if not f:
                    return self._err(404, "No existe ese código")
                return self._enviar(200, mesa.svg_de(sv, f["url"], est, logo, f["etiqueta"]).encode(), "image/svg+xml",
                                    [("Content-Disposition", f'attachment; filename="qr-{f["id"]}.svg"'), ("Content-Security-Policy", paginas.CSP_SVG), *renovar])
            if partes == ["", "panel", "qr", "hoja"]:  # varios en una hoja; ?plantilla=mesa|flyer|pegatina&ids=1,2 (sin ids: todos los activos)
                q = parse_qs(urlsplit(self.path).query)
                ids = {int(x) for x in re.findall(r"\d+", (q.get("ids") or [""])[0])}
                plantilla = (q.get("plantilla") or ["mesa"])[0]
                if plantilla not in mesa.PLANTILLAS:
                    return self._err(400, "Plantilla inválida")
                cs = [c for c in mesa.listar(sv, base) if c["activo"] and (not ids or c["id"] in ids)]
                if not cs:
                    return self._err(404, "No hay códigos activos")
                return self._enviar(200, mesa.hoja(marca_de(sv), [{"etiqueta": c["etiqueta"], "svg": mesa.svg_de(sv, c["url"], est, logo, c["etiqueta"])} for c in cs], plantilla, est["color"], est),
                                    "text/html", [("Content-Security-Policy", CSP), *renovar])
            return self._err(404, "No encontrado")

        def _subir_logo(self, quien, renovar):
            """POST /panel/dueno/qr_logo, cuerpo = bytes crudos del logo (PNG, WebP o SVG), máx. 200 KB. Se comprueba el tipo por su contenido."""
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() not in ("image/png", "image/webp", "image/svg+xml"):
                return self._err(415, "Solo PNG, WebP o SVG")
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "Cuerpo inválido")
            if largo > mesa.MAX_LOGO:
                return self._err(413, "El logo supera 200 KB")
            if largo <= 0:
                return self._err(400, "Logo no válido")
            if limite.excede(("logo", quien), 10):
                return self._err(429, "Demasiadas subidas, espere un minuto")
            try:
                self._leido = True
                datos = self.rfile.read(largo)
            except OSError:
                return self._err(400, "Cuerpo incompleto")
            if len(datos) != largo:
                return self._err(400, "Cuerpo incompleto")
            try:
                mesa.guardar_logo(sv, datos)
            except ValueError as e:
                return self._err(415, str(e))
            panel_web.auditar(sv, quien, "qr_logo")
            return self._enviar(200, {"ok": True}, extra=renovar)

        def _subir_foto(self, quien, renovar):
            """POST /panel/dueno/foto?plato=<id>, cuerpo = bytes crudos. Exigir image/* bloquea formularios ajenos (CSRF) sin CORS."""
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() not in panel_web.TIPOS_FOTO:
                return self._err(415, "Solo se aceptan imágenes JPEG, PNG o WebP")
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "Cuerpo inválido")
            if limite.excede(("foto_up", quien), 10):  # decodificar cuesta CPU: tope propio por minuto
                return self._err(429, "Demasiadas subidas, espere un minuto")
            if largo > panel_web.MAX_FOTO:  # antes de leer
                return self._err(413, "La imagen supera 3 MB")
            if largo <= 0:
                return self._err(400, "Imagen no válida")
            try:
                self._leido = True
                datos = self.rfile.read(largo)
            except OSError:
                return self._err(400, "Cuerpo incompleto")
            if len(datos) != largo:
                return self._err(400, "Cuerpo incompleto")
            try:
                r = panel_web.guardar_foto(sv, quien, (parse_qs(urlsplit(self.path).query).get("plato") or [""])[0], datos)
            except LookupError:
                return self._err(404, "No existe ese plato")
            except panel_web.TipoNoValido as e:
                return self._err(415, str(e))
            except ValueError as e:
                return self._err(400, str(e))
            return self._enviar(200, r, extra=renovar)

        def _estatico(self, ruta):
            """GET /estatico/<nombre>: público, sin sesión; solo los nombres de ESTATICOS (fuentes y Leaflet autoalojado)."""
            nombre = ruta[len("/estatico/"):]
            if limite.excede(("foto", self._ip()), 300):
                return self._err(429, "Demasiadas peticiones")
            f = ESTATICO / nombre
            if nombre not in ESTATICOS or not f.is_file():
                return self._err(404, "No encontrado")
            datos = f.read_bytes()
            self.send_response(200)
            for k, v in (("Content-Type", ESTATICOS[nombre]), ("Content-Length", str(len(datos))), ("X-Content-Type-Options", "nosniff"),
                         ("Cache-Control", "public, max-age=31536000, immutable")):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(datos)

        def _foto(self, ruta):
            """GET /menu/foto/<id>.webp[?s=2]: público; la URL lleva ?v=<hash>, así que se cachea para siempre."""
            iid = ruta[len("/menu/foto/"):].removesuffix(".webp")
            img = ruta.endswith(".webp") and panel_web.ID_PLATO.fullmatch(iid) and sv.foto(iid, (parse_qs(urlsplit(self.path).query).get("s") or [""])[0] == "2")
            if not img:
                return self._err(404, "No encontrado")
            self.send_response(200)
            versionada = "v" in parse_qs(urlsplit(self.path).query)
            for k, v in (("Content-Type", "image/webp"), ("Content-Length", str(len(img))), ("X-Content-Type-Options", "nosniff"),
                         ("Cache-Control", "public, max-age=31536000, immutable" if versionada else "no-cache")):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(img)

        def _repartidor(self, metodo, ruta):
            """Rutas /repartidor*: PIN propio por repartidor; ve solo sus pedidos abiertos. No es solo-LAN (el móvil va por la calle): PIN + bloqueo; HTTPS en producción."""
            ip = self._ip()
            if not acc_rep.activo:
                return self._err(404, "No encontrado")
            if ruta in ("/repartidor", "/repartidor/") and metodo == "GET":
                return self._enviar(200, armar_pagina(sv, PAGINA_REP), "text/html", [("Content-Security-Policy", paginas.csp(sv, PAGINA_REP, CSP))])
            if ruta == "/repartidor/manifest.webmanifest" and metodo == "GET":
                return self._enviar(200, MANIFEST_REP, "application/manifest+json")
            if ruta == "/repartidor/sw.js" and metodo == "GET":
                return self._enviar(200, SW_REP, "text/javascript", [("Service-Worker-Allowed", "/repartidor/")])
            if ruta == "/repartidor/icono.svg" and metodo == "GET":
                return self._enviar(200, ICONO_REP.read_bytes(), "image/svg+xml")
            if ruta == "/repartidor/entrar" and metodo == "POST":
                if bloqueo_rep.bloqueada(ip):
                    return self._err(429, "Demasiados intentos; espere 5 minutos")
                d = self._json()
                if d is None:
                    return
                pin = d.get("pin")
                time.sleep(bloqueo_rep.retraso())
                nombre = acc_rep.rol_de_pin(pin) if isinstance(pin, str) and len(pin) <= 64 else None
                if not nombre:
                    bloqueo_rep.fallo(ip)
                    panel_web.auditar(sv, "?", "repartidor_login_fallido")
                    return self._err(401, "PIN incorrecto")
                bloqueo_rep.acierto(ip)
                panel_web.auditar(sv, nombre, "repartidor_login")
                return self._enviar(200, {"ok": True, "nombre": nombre, "aviso_ubicacion": aviso_rep(nombre)}, extra=[("Set-Cookie", acc_rep.cookie(nombre, seguro=self._seguro()))])
            ses = acc_rep.sesion(self.headers.get("Cookie"))
            if not ses:
                return self._err(401, "Sesión inválida")
            nombre, lim = ses
            renovar = [("Set-Cookie", acc_rep.cookie(nombre, lim, self._seguro()))]
            if ruta == "/repartidor/salir" and metodo == "POST":
                panel_web.auditar(sv, nombre, "repartidor_salir")
                if pos.quitar(nombre):
                    panel_web.auditar(sv, nombre, "ubicacion_off")
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", acc_rep.borrar_cookie(self._seguro()))])
            if ruta == "/repartidor/pedidos" and metodo == "GET":
                r = en_ruta()[3].get(nombre) or reparto.calcular([], None, reparto.DEFECTO)  # solo la suya
                return self._enviar(200, {"nombre": nombre, "pedidos": panel_web.pedidos_repartidor(sv, nombre, r), "ruta": r, "aviso_ubicacion": aviso_rep(nombre)}, extra=renovar)
            if ruta == "/repartidor/aviso_ubicacion" and metodo == "POST":
                d = self._json()
                if d is None:
                    return
                if d.get("version") != aviso_rep(nombre)["version"]:
                    return self._err(409, "El aviso cambió; vuelva a leerlo")
                sv._q("INSERT OR REPLACE INTO aviso_repartidor(nombre, version, at) VALUES (?,?,?)", (nombre, d["version"], sv.ahora().isoformat()))
                return self._enviar(200, {"ok": True}, extra=renovar)
            if ruta == "/repartidor/posicion" and metodo == "POST":
                return self._posicion(nombre, renovar)
            if ruta == "/repartidor/marcar" and metodo == "POST":
                d = self._json()
                if d is None:
                    return
                pid, est = d.get("pedido_id"), d.get("estado")
                if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(est, str)):
                    return self._err(400, "Datos inválidos")
                try:
                    p = panel_web.marcar_repartidor(sv, nombre, pid, est, d.get("cobrado") is True)
                except ValueError as e:
                    return self._err(409, str(e))
                panel_web.auditar(sv, nombre, est, pid)
                r = en_ruta()[3].get(nombre)
                return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_repartidor(p, r) if p["estado"] in panel_web.EN_RUTA else None}, extra=renovar)
            self._err(404, "No encontrado")

        def _posicion(self, nombre, renovar):
            """POST /repartidor/posicion {lat, lon, precision}: guarda solo la última; nunca a logs ni auditoría (solo ubicacion_on/off)."""
            if pos.demasiado_pronto(nombre, time.time()):
                return self._err(429, "Demasiado seguido")
            d = self._json()
            if d is None:
                return
            try:
                lat, lon, precision = ubicacion.validar_posicion(d, sv.cfg)
            except ValueError as e:
                return self._err(400, str(e))
            if not aviso_rep(nombre)["aceptado"]:
                return self._err(403, "Acepte primero el aviso de ubicación")
            if nombre not in en_ruta()[0]:
                return self._err(409, "No tiene pedidos en camino")
            if pos.poner(nombre, lat, lon, precision, time.time()):
                panel_web.auditar(sv, nombre, "ubicacion_on")
            return self._enviar(200, {"ok": True}, extra=renovar)

        def do_GET(self):
            if self.path.split("?")[0].startswith("/estatico/"):
                return self._estatico(self.path.split("?")[0])
            if self.path.split("?")[0].startswith("/repartidor"):
                return self._repartidor("GET", self.path.split("?")[0])
            if self.path.split("?")[0].startswith("/panel"):
                return self._panel("GET", self.path.split("?")[0])
            ruta = self.path.split("?")[0]
            if ruta == "/salud":  # para el monitor de la plataforma: sin cookie ni datos
                try:
                    sv._q("SELECT 1")
                    return self._enviar(200, {"ok": True})
                except Exception:
                    return self._enviar(503, {"ok": False})
            if ruta.startswith("/menu/foto/"):  # límite propio: un menú con fotos son decenas de GET de una vez
                return self._err(429, "Demasiadas peticiones") if limite.excede(("foto", self._ip()), 300) else self._foto(ruta)
            if ruta.startswith("/web/"):  # archivos de la página propia del cliente (motor/paginas.py)
                return self._err(429, "Demasiadas peticiones") if limite.excede(("foto", self._ip()), 300) else paginas.servir(self, sv.dir / "web", ruta)
            if limite.excede(("ip", self._ip()), 60):
                return self._err(429, "Demasiadas peticiones")
            if ruta.startswith("/m/"):
                return self._escaneo(ruta[3:])
            if ruta == "/":
                return self._enviar(200, armar_pagina(sv, PAGINA), "text/html", [("Content-Security-Policy", paginas.csp(sv, PAGINA, CSP))])
            if ruta == "/menu":
                return self._enviar(200, panel_web.menu_json(sv))
            if ruta == "/config":
                d, cid = config_publica(sv, con_pago=bool(self._cid())), self._cid()
                if mesas_on():  # ?m=<codigo> de un QR «pedir», o una sesión de mesa ya abierta
                    f = mesa.por_codigo(sv, (parse_qs(urlsplit(self.path).query).get("m") or [""])[0])
                    if f and f["tipo"] == "pedir":
                        d.update(mesa=f["mesa"], etiqueta=f["etiqueta"])
                    elif mesa.de_cid(cid):
                        d["mesa"] = mesa.de_cid(cid)
                return self._enviar(200, d)
            if ruta == "/aviso":
                v, t = aviso_de(sv)
                return self._enviar(200, {"version": v, "texto": t})
            if ruta in ("/avisos", "/pedidos", "/ultimo"):
                cid = self._cid()
                if not cid:
                    return self._err(401, "Sesión inválida")
                if ruta == "/ultimo":  # /pedidos ya no lista el pedido entregado: aquí lo ve 30 min la persona que lo hizo (mensaje de gracias y «repetir»)
                    ps = pedidos_de(cid)
                    ult = ps[-1] if ps else None
                    desde = ult and ult["estado"] == "entregado" and ult.get("estado_at")
                    ok = bool(desde) and (sv.ahora() - dt.datetime.fromisoformat(desde)).total_seconds() < 1800
                    return self._enviar(200, {"pedido": formato_pedido(ult) if ok else None})
                if ruta == "/pedidos":
                    self._visita_nueva(cid)
                    mios = [p for p in reversed(pedidos_de(cid)) if p["id"] > corte.get(cid, 0) or p["estado"] in herramientas.ABIERTOS]
                    vivo = en_ruta() if any(p["estado"] == "en_camino" for p in mios) else None
                    return self._enviar(200, {"pedidos": [{**formato_pedido(p), **(mapa_cliente(p, *vivo) if p["estado"] == "en_camino" else {})} for p in mios]})
                textos = []
                for m in sv.pendientes():
                    if m["telefono"] != cid:
                        continue
                    try:
                        sv.actualizar(m["id"], {"estado": "enviado"}, desde={"pendiente"})
                    except ValueError:  # otro hilo ya lo entregó
                        continue
                    textos.append(m["texto"])
                return self._enviar(200, {"mensajes": textos})
            self._err(404, "No encontrado")

        def do_POST(self):
            if self.path.split("?")[0].startswith("/repartidor"):
                return self._repartidor("POST", self.path.split("?")[0])
            if self.path.split("?")[0].startswith("/panel"):
                return self._panel("POST", self.path.split("?")[0])
            ip = self._ip()
            ruta = self.path.split("?")[0]
            if limite.excede(("ip", ip), 60) or (ruta == "/entrar" and limite.excede(("entrar", ip), 10)):
                return self._err(429, "Demasiadas peticiones")
            if ruta == "/entrar":
                return self._entrar()
            if ruta == "/chat":
                return self._chat()
            if ruta == "/comprobante":
                return self._comprobante()
            if ruta == "/ubicacion":
                return self._ubicacion()
            if ruta == "/efectivo":
                return self._efectivo()
            if ruta == "/sugerencia":
                return self._sugerencia()
            self._err(404, "No encontrado")

        def _sugerencia(self):
            """POST /sugerencia {pedido}: la venta sugerida de la web (una vez por pedido, solo del propio cliente y por confirmar). No cambia el pedido."""
            cid = self._cid()
            if not cid:
                return self._err(401, "Sesión inválida")
            if limite.excede(("cid", cid), 30):
                return self._err(429, "Demasiadas peticiones")
            d = self._json()
            if d is None:
                return
            p = next((p for p in pedidos_de(cid) if str(p["id"]) == str(d.get("pedido"))), None)
            if not p:
                return self._err(404, "No existe ese pedido")
            self._enviar(200, {"sugerencia": herramientas.sugerir_complemento(sv, p["id"])["sugerencia"]})

        def _entrar(self):
            d = self._json()
            if d is None:
                return
            nombre, tel = d.get("nombre"), re.sub(r"[ \-+]", "", str(d.get("telefono", "")))
            f = mesa.por_codigo(sv, d.get("codigo")) if mesas_on() else None
            en_mesa = f["mesa"] if f and f["tipo"] == "pedir" and not tel else None  # QR de mesa: sin teléfono y con nombre obligatorio
            if en_mesa:
                if limite.excede(("mesa", f["id"]), 20):
                    return self._err(429, "Demasiadas peticiones")
                if not (isinstance(nombre, str) and 1 <= len(nombre.strip()) <= 60):
                    return self._err(400, "Nombre inválido")
            else:
                if not (isinstance(nombre, str) and 1 <= len(nombre.strip()) <= 60):
                    return self._err(400, "Nombre inválido")
                if not (tel.isascii() and tel.isdigit() and 8 <= len(tel) <= 15):
                    return self._err(400, "Teléfono inválido")
            if d.get("consentimiento") is not True and d.get("acepta_privacidad") is not True:
                return self._err(400, "Se requiere el consentimiento")
            version = aviso_de(sv)[0]
            if d.get("version_aviso") != version:
                return self._err(400, "El aviso cambió; vuelva a aceptarlo")
            nombre = nombre.strip()
            vida = VIDA_S
            if en_mesa:  # la misma sesión de esa mesa se conserva (recargar no pierde el seguimiento); si no, cid nuevo; la cookie dura una visita
                actual = self._cid()
                tel, vida = actual if mesa.de_cid(actual) == en_mesa else mesa.nuevo_cid(en_mesa), VISITA_S
            sv.insertar("consentimiento", {"telefono": tel, "nombre": nombre, "consentimiento_at": sv.ahora().isoformat(), "version_aviso": version})
            if nombre:
                nombres[tel] = nombre
            self._visita_nueva(tel)
            cookie = f"sesion={firmar(secreto, tel, int(time.time()) + vida)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={vida}{'; Secure' if self._seguro() else ''}"
            self._enviar(200, {"ok": True, "nombre": nombre, **({"mesa": en_mesa} if en_mesa else {})}, extra=[("Set-Cookie", cookie)])

        def _visita_nueva(self, cid):
            """Chat limpio (historial del modelo) si el último pedido terminó o hubo >VISITA_S sin actividad. El pedido en BD no se toca."""
            fila, ps = sv._q("SELECT actualizado FROM conversaciones WHERE id=?", (cid,)), pedidos_de(cid)
            cerrado = bool(ps) and ps[-1]["estado"] not in herramientas.ABIERTOS and ps[-1]["id"] > corte.get(cid, 0)
            if cerrado:
                corte[cid] = ps[-1]["id"]
            if fila and (cerrado or time.time() - fila[0][0] > VISITA_S):
                sv._q("DELETE FROM conversaciones WHERE id=?", (cid,))

        def _comprobante(self):
            cid = self._cid()
            if not cid:
                return self._err(401, "Sesión inválida")
            if limite.excede(("cid", cid), 30):
                return self._err(429, "Demasiadas peticiones")
            tipo = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if tipo not in ("image/jpeg", "image/png"):
                return self._err(415, "Solo se aceptan imágenes JPEG o PNG")
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "Cuerpo inválido")
            if largo > MAX_IMAGEN:  # antes de leer
                return self._err(413, "La imagen supera 5 MB")
            if largo <= 0:
                return self._err(400, "Imagen no válida")
            q = parse_qs(urlsplit(self.path).query)
            ref = (q.get("referencia") or [""])[0].strip()
            if len(ref) > 40:
                return self._err(400, "Referencia demasiado larga")
            pid = (q.get("pedido") or [""])[0]
            p = next((p for p in pedidos_de(cid) if str(p["id"]) == pid), None)
            if not p:
                return self._err(404, "No existe ese pedido")
            if p["estado"] not in ("confirmado", "pago_reportado"):
                return self._err(409, "El pedido no está esperando pago")
            try:
                self._leido = True
                datos = self.rfile.read(largo)
            except OSError:  # timeout o corte
                return self._err(400, "Cuerpo incompleto")
            if len(datos) != largo:
                return self._err(400, "Cuerpo incompleto")
            ext = tipo_imagen(datos)
            if ext != {"image/jpeg": "jpg", "image/png": "png"}[tipo]:
                return self._err(415, "El archivo no es una imagen del tipo indicado")
            try:
                limpia = limpiar_imagen(datos, ext)
            except ValueError as e:
                return self._err(400, str(e))
            try:
                nuevo = subir_comprobante(sv, p, ext, limpia, ref)
            except ValueError:
                return self._err(409, "El pedido no está esperando pago")
            self._enviar(200, {"ok": True, "pedido": formato_pedido(nuevo)})

        def _efectivo(self):
            cid = self._cid()
            if not cid:
                return self._err(401, "Sesión inválida")
            if limite.excede(("cid", cid), 30):
                return self._err(429, "Demasiadas peticiones")
            d = self._json()
            if d is None:
                return
            p = next((p for p in pedidos_de(cid) if str(p["id"]) == str(d.get("pedido"))), None)
            if not p:
                return self._err(404, "No existe ese pedido")
            try:
                herramientas.pagar_efectivo(sv, p["id"])
            except ValueError as e:
                return self._err(409, str(e))
            self._enviar(200, {"ok": True, "pedido": formato_pedido(next(x for x in pedidos_de(cid) if x["id"] == p["id"]))})

        def _ubicacion(self):
            cid = self._cid()
            if not cid:
                return self._err(401, "Sesión inválida")
            if limite.excede(("cid", cid), 30):
                return self._err(429, "Demasiadas peticiones")
            d = self._json()
            if d is None:
                return
            try:
                if "enlace" in d:
                    if not isinstance(d["enlace"], str):
                        raise ValueError("Pegue un enlace de Google Maps.")
                    lat, lon, enlace = ubicacion.desde_enlace(d["enlace"])
                    fuente = "enlace"
                else:
                    lat, lon, fuente, enlace = d.get("lat"), d.get("lon", d.get("lng")), "navegador", None
                if lat is not None or fuente == "navegador":
                    lat, lon = ubicacion.validar(lat, lon)
            except ValueError as e:
                return self._err(400, str(e))
            pid = self._aplicar_ubicacion(cid, (lat, lon, fuente, enlace, d.get("precision")))
            self._enviar(200, {"ok": True, "pedido_id": pid, "ubicacion": True})

        def _aplicar_ubicacion(self, cid, u):
            """La une al pedido de entrega abierto más reciente del cliente; si no hay, la guarda en memoria para el siguiente. Devuelve el id o None."""
            abiertos = [p for p in pedidos_de(cid) if p["modalidad"] == "entrega" and p["estado"] in herramientas.ABIERTOS]
            if not abiertos:
                ubic_pend[cid] = u
                if len(ubic_pend) > 1000:
                    ubic_pend.pop(next(iter(ubic_pend)))
                return None
            lat, lon, fuente, enlace, precision = u
            herramientas.poner_ubicacion(sv, abiertos[-1]["id"], lat, lon, fuente, enlace, precision)
            ubic_pend.pop(cid, None)
            return abiertos[-1]["id"]

        def _chat(self):
            cid = self._cid()
            if not cid:
                return self._err(401, "Sesión inválida")
            if limite.excede(("cid", cid), 30):
                return self._err(429, "Demasiadas peticiones")
            if mesa.de_cid(cid) and limite.excede(("mesa_chat", mesa.de_cid(cid)), 60):  # tope por mesa además del de cada cid
                return self._err(429, "Demasiadas peticiones")
            d = self._json()
            if d is None:
                return
            t = d.get("texto")
            if not (isinstance(t, str) and 1 <= len(t) <= 1000):
                return self._err(400, "Texto inválido")
            self._visita_nueva(cid)
            if n := nombres.pop(cid, None):
                t = f"[Nombre del cliente: {n}] {t}"
            if m := mesa.de_cid(cid):
                t = f"[Cliente SENTADO en la MESA {m} (pedido en mesa por QR, NO online): usa siempre modalidad 'mesa'; NO preguntes dirección, teléfono, entrega ni recogida ni las ofrezcas; el nombre ya lo tienes (arriba), no lo pidas de nuevo; al confirmar di «Mesa {m}»] {t}"
            antes = {p["id"]: p for p in pedidos_de(cid)}
            try:
                r = sv.responder(cid, t, canal="web")
            except Exception:
                print(f"Error atendiendo a {enmascarar(cid)}")  # sin teléfono completo ni mensajes
                traceback.print_exc()
                r = GENERICA
            if cid in ubic_pend:  # ubicación enviada antes de que hubiera pedido
                self._aplicar_ubicacion(cid, ubic_pend[cid])
            # pedido más reciente del cliente que se creó o cambió en este turno
            cambiados = [p for p in pedidos_de(cid) if antes.get(p["id"]) != p]
            self._enviar(200, {"respuesta": r, "pedido": formato_pedido(cambiados[-1]) if cambiados else None})

    return H


LOCALES = ("127.0.0.1", "::1")  # desde dónde se atiende /_ask


def secreto_cliente(c):
    """Secreto de cookies propio de cada cliente: HMAC(maestro, slug). El maestro es WEB_SECRET o el .web_secret de su carpeta;
    aunque dos clientes lo compartan (WEB_SECRET común, carpeta copiada), una cookie firmada para uno no vale en otro."""
    return hmac.new(secreto_web(c.carpeta).encode(), b"cliente:" + c.slug.encode(), hashlib.sha256).hexdigest()


def crear_alojamiento(lista, puerto=8080, ruta=None, al_abrir=None):
    """Un servidor para todos los clientes activos de `lista` (clientes.cargar). Cada uno con su Servicio, su BD, su secreto y sus
    variables; se elige por la cabecera Host (dominio exacto o, sin PROXY=1, <slug>.localhost) en CADA petición. Lo demás es 404.
    `ruta`: el clientes.yaml de donde salió `lista`; cuando cambia en disco se vuelve a leer sin reiniciar (pausar, reactivar, alta).
    `al_abrir(sv)`: se llama con cada Servicio que se abre, también los que entran en caliente.
    Escucha en ALOJAR_HOST (por defecto 127.0.0.1: solo el Caddy de esta máquina; en un contenedor, 0.0.0.0)."""
    servicios, manejadores, cerrojo = {}, {}, threading.Lock()
    por_host, dominios, activos, visto = {}, set(), [], None

    def huella():
        try:
            st = os.stat(ruta)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None  # sin archivo: se sigue con lo que había

    def aplicar(lista):
        """Los ya abiertos conservan su instancia (BD, sesiones, límites); un pausado solo deja de estar en el mapa de hosts.
        ponytail: carpeta o variables de un cliente ya abierto no cambian en caliente (pide reinicio); un pausado sigue con la BD abierta."""
        nonlocal por_host, dominios, activos
        hosts, doms = {}, {}
        for c in lista:
            if c.activo:
                if c.slug not in manejadores:
                    env = clientes.entorno(c.slug)
                    sv = Servicio(c.carpeta, env=env)
                    manejadores[c.slug], servicios[c.slug] = crear_manejador(sv, secreto_cliente(c), env), sv
                    if al_abrir:
                        al_abrir(sv)
                sv = servicios[c.slug]
                sv.modulos_operador = c.modulos  # None = manda su servicio.yaml; se relee en cada petición (panel_web.modulos)
                sv.tope_ia = clientes.tope_ia(c)
                hosts[c.dominio] = doms[c.dominio] = (manejadores[c.slug], sv, c.prueba_hasta)
                if os.environ.get("PROXY") != "1":  # atajo para probar en este PC; tras un proxy sería un segundo nombre que nadie vigila
                    hosts[c.slug + ".localhost"] = hosts[c.dominio]
        por_host, dominios, activos = hosts, doms, [servicios[c.slug] for c in lista if c.activo]

    def vigente(mapa, nombre):
        """El manejador de ese host/dominio, o None si no es de un cliente activo o su prueba gratis venció (hoy > prueba_hasta en la zona
        horaria de su servicio). Se mira en cada petición: una resta de fechas, sin tarea de fondo."""
        h, sv, hasta = mapa.get(nombre) or (None, None, None)
        return None if h is None or (hasta is not None and sv.ahora().date() > hasta) else h

    def recargar():
        nonlocal visto
        if ruta is None or huella() in (visto, None):
            return
        with cerrojo:
            h = huella()
            if h in (visto, None):
                return
            visto = h  # también si está roto: no se reintenta en cada petición, solo cuando vuelva a cambiar
            try:
                aplicar(clientes.cargar(ruta))
            except Exception as e:  # a medio escribir o inválido: se conserva la lista anterior
                print(f"[alojar] {ruta} no se pudo recargar; sigue la lista anterior: {e}")

    aplicar(lista)
    visto = huella() if ruta is not None else None

    class D(_Base):
        timeout = TIMEOUT_S

        def _caddy(self):
            """¿Pregunta el propio Caddy? Desde esta máquina; o, con PROXY=1 (Caddy en otro contenedor), desde la red privada."""
            ip = self.client_address[0]
            return ip in LOCALES or (os.environ.get("PROXY") == "1" and panel_web.es_lan(ip))

        def _despachar(self):
            recargar()
            ruta, _, consulta = self.path.partition("?")
            reenviada = bool(self.headers.get("X-Forwarded-For"))
            if reenviada and self.client_address[0] in LOCALES and os.environ.get("PROXY") != "1":
                # Un proxy en esta máquina sin PROXY=1: `_ip()` daría 127.0.0.1 a todo el mundo (panel abierto como si fuera la LAN,
                # y bloqueo de PIN y límites compartidos entre todos los visitantes). No se sirve nada hasta que se configure.
                return self._err(500, "Configuración: hay un proxy delante (llega X-Forwarded-For) y falta PROXY=1 en el .env del servidor")
            if self.command == "GET" and ruta == "/_ask" and self._caddy() and not reenviada:
                # Caddy (on-demand TLS) pregunta desde esta máquina antes de emitir un certificado. Lo que el propio Caddy reenvía
                # de fuera también llega desde 127.0.0.1, pero con X-Forwarded-For: eso no se contesta aquí.
                return self._enviar(200, {"ok": True}) if vigente(dominios, (parse_qs(consulta).get("domain") or [""])[0].lower()) else self._err(404, "No encontrado")
            if self.command == "GET" and ruta == "/salud":  # monitor de la plataforma: llega sin dominio de cliente
                if not self._caddy() or reenviada:  # desde internet: barato y sin decir nada (un SELECT por cliente a petición de cualquiera, no)
                    return self._enviar(200, {"ok": True})
                try:
                    for sv in activos:
                        sv._q("SELECT 1")
                    return self._enviar(200, {"ok": True})
                except Exception:
                    return self._enviar(503, {"ok": False})
            h = vigente(por_host, (self.headers.get("Host") or "").strip().lower().partition(":")[0].rstrip("."))
            if not h:  # desconocido, pausado o con la prueba vencida: la misma respuesta, sin decir cuál
                return self._err(404, "No encontrado")
            self.__dict__.pop("_leido", None)
            self.__class__ = h  # el manejador del cliente atiende esta petición...
            try:
                getattr(self, "do_" + self.command)()
            finally:
                self.__class__ = D  # ...y la siguiente de la misma conexión se vuelve a despachar (Caddy reutiliza conexiones entre dominios)

        do_GET = do_POST = _despachar

    srv = ThreadingHTTPServer((os.environ.get("ALOJAR_HOST") or "127.0.0.1", puerto), D)
    srv.servicios = servicios  # todos los abiertos, también los pausados después
    return srv


def _ip_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP: no envía nada
            return s.getsockname()[0]
    except OSError:
        return "no detectada"


def iniciar_purga(sv, cada=3600):
    """Purga ya y luego cada `cada` s en un hilo daemon; devuelve el Event que lo detiene."""
    parar = threading.Event()

    def bucle():
        while True:
            try:
                purgar_comprobantes(sv)
            except Exception as e:  # base bloqueada: reintenta en el próximo ciclo
                print(f"[purga] {e}")
            if parar.wait(cada):
                return

    threading.Thread(target=bucle, daemon=True).start()
    return parar


def alojar(ruta="clientes.yaml", puerto=8080):
    srv = crear_alojamiento(clientes.cargar(ruta), puerto, ruta, al_abrir=iniciar_purga)
    if os.environ.get("PROXY") != "1":
        for slug in srv.servicios:
            print(f"  {slug}: http://{slug}.localhost:{srv.server_address[1]}/")
    print(f"Alojando {len(srv.servicios)} cliente(s) en {srv.server_address[0]}:{srv.server_address[1]}")
    srv.serve_forever()


def servir(sv, puerto=8080):
    iniciar_purga(sv)
    srv = crear_servidor(sv, puerto)
    p = srv.server_address[1]
    print(f"Canal web de '{sv.cfg.get('nombre')}': http://localhost:{p}/  ·  http://{_ip_local()}:{p}/")
    srv.serve_forever()
