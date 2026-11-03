"""Canal web: página de pedidos con chat (solo stdlib). Cookie firmada = identidad del cliente (cid = teléfono)."""
import hashlib
import hmac
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

from .herramientas import dinero, limpiar_imagen, purgar_comprobantes, subir_comprobante, tipo_imagen

PAGINA = Path(__file__).with_name("web_index.html")
MAX_CUERPO = 8192
MAX_IMAGEN = 5 * 1024 * 1024
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
            "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], "precio": dinero(l["precio_c"])} for l in p["lineas"]],
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
    limite, nombres = Limite(), {}  # nombres: cid -> nombre pendiente de anteponer al primer mensaje
    menu = [{"id": i["id"], "nombre": i["nombre"], "precio": i["precio_c"] / 100, "categoria": i.get("categoria", ""),
             "disponible": i.get("disponible", True), **({"opciones": i["opciones"]} if "opciones" in i else {})} for i in sv.menu.values()]

    def pedidos_de(cid):
        return [p for p in sv.listar("pedido") if p["telefono"] == cid]

    class H(BaseHTTPRequestHandler):
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
            self.close_connection = True  # puede quedar cuerpo sin leer
            self._enviar(codigo, {"error": msg})

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
                d = json.loads(self.rfile.read(largo))
            except ValueError:
                return self._err(400, "JSON inválido")
            return d if isinstance(d, dict) else self._err(400, "JSON inválido")

        def do_GET(self):
            if limite.excede(("ip", self.client_address[0]), 60):
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
            ip = self.client_address[0]
            ruta = self.path.split("?")[0]
            if limite.excede(("ip", ip), 60) or (ruta == "/entrar" and limite.excede(("entrar", ip), 10)):
                return self._err(429, "Demasiadas peticiones")
            if ruta == "/entrar":
                return self._entrar()
            if ruta == "/chat":
                return self._chat()
            if ruta == "/comprobante":
                return self._comprobante()
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
            cookie = f"sesion={firmar(secreto, tel, int(time.time()) + VIDA_S)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={VIDA_S}"
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
            datos = self.rfile.read(largo)
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


def servir(sv, puerto=8080):
    purgar_comprobantes(sv)
    srv = crear_servidor(sv, puerto)
    p = srv.server_address[1]
    print(f"Canal web de '{sv.cfg.get('nombre')}': http://localhost:{p}/  ·  http://{_ip_local()}:{p}/")
    srv.serve_forever()
