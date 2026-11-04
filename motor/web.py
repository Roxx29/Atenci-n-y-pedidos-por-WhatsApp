"""Canal web: página de pedidos con chat (solo stdlib). Cookie firmada = identidad del cliente (cid = teléfono)."""
import hashlib
import hmac
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

from . import panel_web, ubicacion
from . import herramientas
from .herramientas import dinero, limpiar_imagen, purgar_comprobantes, subir_comprobante, tipo_imagen

PAGINA = Path(__file__).with_name("web_index.html")
PAGINA_PANEL = Path(__file__).with_name("panel_web.html")
PAGINA_REP = Path(__file__).with_name("repartidor.html")
MANIFEST_REP = {"name": "Repartidor", "short_name": "Repartidor", "start_url": "/repartidor/", "scope": "/repartidor/", "display": "standalone",
                "background_color": "#ffffff", "theme_color": "#1f6f43", "icons": [{"src": "/repartidor/icono.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}
SW_REP = b"self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',()=>{});"  # sin caché: los pedidos no se guardan en el móvil
ICONO_REP = Path(__file__).with_name("repartidor_icono.svg")
MAX_CUERPO = 8192
MAX_IMAGEN = 5 * 1024 * 1024
TIMEOUT_S = 15  # socket de cada conexión: un cliente callado no retiene un hilo
VIDA_S = 24 * 3600
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' blob: data:"
GENERICA = "Disculpe, tuvimos un problema técnico. Intente de nuevo en unos minutos."


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
    """507******12: nunca se imprime el teléfono completo."""
    return tel[:3] + "*" * max(len(tel) - 5, 0) + tel[-2:] if len(tel) > 5 else "***"


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
            "subtotal": dinero(p["subtotal_c"]), "impuesto": dinero(p["itbms_c"]), "envio": dinero(p["envio_c"]), "total": dinero(p["total_c"])}


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


def crear_servidor(sv, puerto=8080, secreto=None):
    secreto = secreto or secreto_web(sv.dir)
    pagina = PAGINA.read_bytes()
    acceso, bloqueo = panel_web.Acceso(secreto), panel_web.Bloqueo()
    pagina_panel = PAGINA_PANEL.read_bytes() if PAGINA_PANEL.is_file() else b""
    repartidores = panel_web.env_repartidores()
    acc_rep = panel_web.Acceso(secreto, repartidores, "repartidor", "/repartidor", inactivo=panel_web.VIDA_S)  # sesión de turno: 12 h, sin bloqueo por inactividad
    bloqueo_rep, ubic_pend = panel_web.Bloqueo(), {}  # ubic_pend: cid -> ubicación recibida antes de tener pedido
    limite, nombres = Limite(), {}  # nombres: cid -> nombre pendiente de anteponer al primer mensaje
    menu = [{"id": i["id"], "nombre": i["nombre"], "precio": i["precio_c"] / 100, "categoria": i.get("categoria", ""),
             "disponible": i.get("disponible", True), **({"opciones": i["opciones"]} if "opciones" in i else {})} for i in sv.menu.values()]

    def pedidos_de(cid):
        return [p for p in sv.listar("pedido") if p["telefono"] == cid]

    class H(BaseHTTPRequestHandler):
        timeout = TIMEOUT_S

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

        def _del_tunel(self):
            """TUNEL=1 y la conexión viene de este mismo PC (cloudflared): se pueden creer sus cabeceras CF-*."""
            return os.environ.get("TUNEL") == "1" and self.client_address[0] in ("127.0.0.1", "::1")

        def _ip(self):
            """IP real: CF-Connecting-IP solo si llega por el túnel (si no, cualquiera de la LAN podría falsearla)."""
            if self._del_tunel():
                try:
                    return str(ipaddress.ip_address((self.headers.get("CF-Connecting-IP") or "").strip()))
                except ValueError:
                    pass
            return self.client_address[0]

        def _seguro(self):
            """Cookie Secure si el cliente llegó por https a través del túnel."""
            return self._del_tunel() and "https" in ((self.headers.get("X-Forwarded-Proto") or "") + (self.headers.get("CF-Visitor") or "")).lower()

        def _cid(self):
            return cid_de_cookie(secreto, self.headers.get("Cookie"))

        def _json(self):
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._err(415, "Se requiere application/json")
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "Cuerpo inválido")
            if not 0 < largo <= MAX_CUERPO:  # antes de leer
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
            """Rutas /panel*: solo LAN (o PANEL_PUBLICO=1) y solo con PIN configurado; el resto de peticiones ni lo ven."""
            ip = self._ip()
            if not (acceso.activo and (panel_web.es_lan(ip) or os.environ.get("PANEL_PUBLICO") == "1")):
                return self._err(404, "No encontrado")
            if ruta == "/panel" and metodo == "GET":
                return self._enviar(200, pagina_panel, "text/html", [("Content-Security-Policy", CSP)])
            if ruta == "/panel/entrar" and metodo == "POST":
                if bloqueo.bloqueada(ip):
                    return self._err(429, "Demasiados intentos; espere 5 minutos")
                d = self._json()
                if d is None:
                    return
                pin = d.get("pin")
                rol = acceso.rol_de_pin(pin) if isinstance(pin, str) and len(pin) <= 64 else None
                if not rol:
                    bloqueo.fallo(ip)
                    panel_web.auditar(sv, "?", "login_fallido")
                    return self._err(401, "PIN incorrecto")
                bloqueo.acierto(ip)
                panel_web.auditar(sv, rol, "login")
                return self._enviar(200, {"ok": True, "rol": rol}, extra=[("Set-Cookie", acceso.cookie(rol, seguro=self._seguro()))])
            ses = acceso.sesion(self.headers.get("Cookie"))
            if not ses:
                return self._err(401, "Sesión inválida")
            rol, lim = ses
            renovar = [("Set-Cookie", acceso.cookie(rol, lim, self._seguro()))]
            if ruta == "/panel/salir" and metodo == "POST":
                panel_web.auditar(sv, rol, "salir")
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", acceso.borrar_cookie(self._seguro()))])
            partes = ruta.split("/")  # ['', 'panel', 'estado'] ...
            try:
                if ruta == "/panel/estado" and metodo == "GET":
                    desde = int((parse_qs(urlsplit(self.path).query).get("desde") or ["0"])[0])
                    return self._enviar(200, panel_web.estado_panel(sv, desde, repartidores), extra=renovar)
                if len(partes) == 4 and partes[2] in ("pedido", "comprobante") and metodo == "GET":
                    pid = int(partes[3])
                    if partes[2] == "pedido":
                        p = next((p for p in sv.listar("pedido") if p["id"] == pid), None)
                        return self._enviar(200, {"pedido": panel_web.pedido_json(p, True)}, extra=renovar) if p else self._err(404, "No existe ese pedido")
                    img = panel_web.comprobante(sv, pid)
                    if not img:
                        return self._err(404, "Sin comprobante")
                    panel_web.auditar(sv, rol, "ver_comprobante", pid)
                    return self._enviar(200, img[0], img[1], renovar)
                if ruta == "/panel/marcar" and metodo == "POST":
                    d = self._json()
                    if d is None:
                        return
                    pid, est = d.get("pedido_id"), d.get("estado")
                    if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(est, str)):
                        return self._err(400, "Datos inválidos")
                    try:
                        p = panel_web.marcar(sv, pid, est, rol)
                    except ValueError as e:
                        return self._err(409, str(e))
                    panel_web.auditar(sv, rol, est, pid)
                    return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_json(p)}, extra=renovar)
                if ruta == "/panel/asignar" and metodo == "POST":
                    d = self._json()
                    if d is None:
                        return
                    pid, quien = d.get("pedido_id"), d.get("repartidor", "")
                    if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(quien, str)):
                        return self._err(400, "Datos inválidos")
                    try:
                        p = panel_web.asignar(sv, pid, quien, repartidores)
                    except ValueError as e:
                        return self._err(409, str(e))
                    panel_web.auditar(sv, rol, "asignar", pid)
                    return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_json(p)}, extra=renovar)
                if len(partes) == 5 and partes[2] == "escalado" and partes[4] == "atendido" and metodo == "POST":
                    try:
                        panel_web.atender_escalado(sv, int(partes[3]))
                    except ValueError as e:
                        return self._err(409, str(e))
                    return self._enviar(200, {"ok": True}, extra=renovar)
            except ValueError:  # int() de la ruta
                return self._err(400, "Datos inválidos")
            self._err(404, "No encontrado")

        def _repartidor(self, metodo, ruta):
            """Rutas /repartidor*: PIN propio por repartidor; ve solo sus pedidos abiertos. No es solo-LAN (el móvil va por la calle): PIN + bloqueo; HTTPS en producción."""
            ip = self._ip()
            if not acc_rep.activo:
                return self._err(404, "No encontrado")
            if ruta in ("/repartidor", "/repartidor/") and metodo == "GET":
                return self._enviar(200, PAGINA_REP.read_bytes() if PAGINA_REP.is_file() else b"", "text/html", [("Content-Security-Policy", CSP)])
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
                nombre = acc_rep.rol_de_pin(pin) if isinstance(pin, str) and len(pin) <= 64 else None
                if not nombre:
                    bloqueo_rep.fallo(ip)
                    panel_web.auditar(sv, "?", "repartidor_login_fallido")
                    return self._err(401, "PIN incorrecto")
                bloqueo_rep.acierto(ip)
                panel_web.auditar(sv, nombre, "repartidor_login")
                return self._enviar(200, {"ok": True, "nombre": nombre}, extra=[("Set-Cookie", acc_rep.cookie(nombre, seguro=self._seguro()))])
            ses = acc_rep.sesion(self.headers.get("Cookie"))
            if not ses:
                return self._err(401, "Sesión inválida")
            nombre, lim = ses
            renovar = [("Set-Cookie", acc_rep.cookie(nombre, lim, self._seguro()))]
            if ruta == "/repartidor/salir" and metodo == "POST":
                panel_web.auditar(sv, nombre, "repartidor_salir")
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", acc_rep.borrar_cookie(self._seguro()))])
            if ruta == "/repartidor/pedidos" and metodo == "GET":
                return self._enviar(200, {"nombre": nombre, "pedidos": panel_web.pedidos_repartidor(sv, nombre)}, extra=renovar)
            if ruta == "/repartidor/marcar" and metodo == "POST":
                d = self._json()
                if d is None:
                    return
                pid, est = d.get("pedido_id"), d.get("estado")
                if not (isinstance(pid, int) and not isinstance(pid, bool) and isinstance(est, str)):
                    return self._err(400, "Datos inválidos")
                try:
                    p = panel_web.marcar_repartidor(sv, nombre, pid, est)
                except ValueError as e:
                    return self._err(409, str(e))
                panel_web.auditar(sv, nombre, est, pid)
                return self._enviar(200, {"ok": True, "pedido": panel_web.pedido_repartidor(p) if p["estado"] in panel_web.EN_RUTA else None}, extra=renovar)
            self._err(404, "No encontrado")

        def do_GET(self):
            if self.path.split("?")[0].startswith("/repartidor"):
                return self._repartidor("GET", self.path.split("?")[0])
            if self.path.split("?")[0].startswith("/panel"):
                return self._panel("GET", self.path.split("?")[0])
            if limite.excede(("ip", self._ip()), 60):
                return self._err(429, "Demasiadas peticiones")
            ruta = self.path.split("?")[0]
            if ruta == "/":
                return self._enviar(200, pagina, "text/html", [("Content-Security-Policy", CSP)])
            if ruta == "/menu":
                return self._enviar(200, menu)
            if ruta == "/aviso":
                v, t = aviso_de(sv)
                return self._enviar(200, {"version": v, "texto": t})
            if ruta in ("/avisos", "/pedidos"):
                cid = self._cid()
                if not cid:
                    return self._err(401, "Sesión inválida")
                if ruta == "/pedidos":
                    return self._enviar(200, {"pedidos": [formato_pedido(p) for p in reversed(pedidos_de(cid))]})
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
            self._err(404, "No encontrado")

        def _entrar(self):
            d = self._json()
            if d is None:
                return
            nombre, tel = d.get("nombre"), re.sub(r"[ \-+]", "", str(d.get("telefono", "")))
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
            sv.insertar("consentimiento", {"telefono": tel, "nombre": nombre, "consentimiento_at": sv.ahora().isoformat(), "version_aviso": version})
            nombres[tel] = nombre
            cookie = f"sesion={firmar(secreto, tel, int(time.time()) + VIDA_S)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={VIDA_S}{'; Secure' if self._seguro() else ''}"
            self._enviar(200, {"ok": True, "nombre": nombre}, extra=[("Set-Cookie", cookie)])

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
            d = self._json()
            if d is None:
                return
            t = d.get("texto")
            if not (isinstance(t, str) and 1 <= len(t) <= 1000):
                return self._err(400, "Texto inválido")
            if n := nombres.pop(cid, None):
                t = f"[Nombre del cliente: {n}] {t}"
            antes = {p["id"]: p for p in pedidos_de(cid)}
            try:
                r = sv.responder(cid, t)
            except Exception:
                print(f"Error atendiendo a {enmascarar(cid)}")  # sin teléfono completo ni mensajes
                traceback.print_exc()
                r = GENERICA
            if cid in ubic_pend:  # ubicación enviada antes de que hubiera pedido
                self._aplicar_ubicacion(cid, ubic_pend[cid])
            # pedido más reciente del cliente que se creó o cambió en este turno
            cambiados = [p for p in pedidos_de(cid) if antes.get(p["id"]) != p]
            self._enviar(200, {"respuesta": r, "pedido": formato_pedido(cambiados[-1]) if cambiados else None})

    return ThreadingHTTPServer(("0.0.0.0", puerto), H)


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


def servir(sv, puerto=8080):
    iniciar_purga(sv)
    srv = crear_servidor(sv, puerto)
    p = srv.server_address[1]
    print(f"Canal web de '{sv.cfg.get('nombre')}': http://localhost:{p}/  ·  http://{_ip_local()}:{p}/")
    srv.serve_forever()
