"""Consola del operador: panel APARTE solo para el dueño del sistema (lista de clientes, alta, pausa, copias).
Solo agregados: nunca teléfonos, nombres ni datos personales de los clientes finales (Ley 81).
Uso: OPERADOR_CLAVE=... python -m motor operador [clientes.yaml] [puerto]"""
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from . import clientes, panel_web
from .nucleo import Servicio

SLUG = re.compile(r"[a-z0-9-]{1,40}")
HTML = Path(__file__).with_name("operador.html")
SESION = 8 * 3600
MAX_FALLOS, BLOQUEO = 5, 300
CATALOGO = ("estadisticas",)  # módulos que el operador puede activar; añade aquí los nuevos
AVISO_DIAS = 5
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
MARCADOR = re.compile(r"\{(nombre|marca|slug)\}")  # los que rellena el alta en la plantilla
TEMA = Path(__file__).with_name("estatico") / "tema.css"
TOPE_PRUEBA = 300 # tope IA diario por defecto en prueba (el contrato lo aplica clientes.py)


class Operador:
    def __init__(self, ruta_clientes, clave, raiz=None, plantilla=None, seguro=False):
        if not clave or len(clave) < 16:
            raise SystemExit("OPERADOR_CLAVE es obligatoria (mínimo 16 caracteres): la consola no arranca sin ella.")
        # plantilla: del repo (/app/servicios/_plantilla-restaurante en Docker), no del volumen de datos
        self.plantilla = Path(plantilla) if plantilla else Path(__file__).resolve().parent.parent / "servicios" / "_plantilla-restaurante"
        self.seguro = seguro  # cookie Secure (consola detrás de TLS)
        self.ruta = Path(ruta_clientes).resolve()
        self.raiz = Path(raiz).resolve() if raiz else self.ruta.parent
        self.cargar = clientes.cargar
        self.clave = clave.encode()
        # secreto de firma: WEB_SECRET o aleatorio por arranque (las sesiones caducan al reiniciar)
        self.firma_key = (os.environ.get("WEB_SECRET") or "").encode() or os.urandom(32)
        self.fallos = {}  # ip -> [fallos, bloqueado_hasta]
        self.lock = threading.Lock()

    # --- acceso ---
    def entrar(self, clave, ip=""):
        """-> cookie o None; LookupError si esa IP está bloqueada."""
        with self.lock:
            ahora = time.time()
            self.fallos = {k: v for k, v in self.fallos.items() if v[1] > ahora or v[0]}  # sin entradas muertas
            f = self.fallos.setdefault(ip, [0, 0.0])
            if ahora < f[1]:
                raise LookupError("bloqueado")
            if hmac.compare_digest(hashlib.sha256(clave.encode()).digest(), hashlib.sha256(self.clave).digest()):
                self.fallos.pop(ip, None)
                return self._cookie()
            f[0] += 1
            if f[0] >= MAX_FALLOS:
                f[0], f[1] = 0, ahora + BLOQUEO
            return None

    def _atrib(self, v, vida):
        return f"{v}; HttpOnly; SameSite=Strict; Path=/; Max-Age={vida}" + ("; Secure" if self.seguro else "")

    def cookie_salir(self):
        return self._atrib("operador=", 0)

    def _firma(self, exp):
        return hmac.new(self.firma_key, str(exp).encode(), hashlib.sha256).hexdigest()

    def _cookie(self):
        exp = int(time.time()) + SESION
        return self._atrib(f"operador={exp}.{self._firma(exp)}", SESION)

    def valida(self, cabecera):
        for par in (cabecera or "").split(";"):
            k, _, v = par.strip().partition("=")
            if k == "operador":
                exp, _, f = v.partition(".")
                return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(f, self._firma(exp))
        return False

    # --- YAML ---
    def _yaml(self):
        """clientes.yaml como dict con `clientes: {slug: {carpeta, dominio, activo}}` (mapa, como clientes.cargar)."""
        d = yaml.safe_load(self.ruta.read_text(encoding="utf-8")) if self.ruta.exists() else None
        d = d if isinstance(d, dict) else {}
        if not isinstance(d.get("clientes"), dict):
            d["clientes"] = {}
        return d

    def _guardar(self, d):
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def _carpeta(self, slug):
        """Carpeta del cliente solo si el slug es válido y está dentro de la raíz."""
        if not SLUG.fullmatch(slug or ""):
            raise ValueError("slug inválido: usa a-z, 0-9 y guiones")
        c = next((c for c in self.cargar(self.ruta) if c.slug == slug), None)
        if c is None:
            raise LookupError("cliente desconocido")
        return Path(c.carpeta).resolve()

    # --- datos (solo lectura, solo agregados) ---
    def _metricas(self, carpeta):
        m = {"hoy": 0, "ultimo": None, "ventas_mes": 0, "bytes": 0, "avisos": 0, "modulos": [], "error": None}
        try:
            cfg = yaml.safe_load((carpeta / "servicio.yaml").read_text(encoding="utf-8")) or {}
            m["modulos"] = [str(x) for x in (cfg.get("modulos") or [])]
        except (OSError, yaml.YAMLError):
            m["error"] = "servicio.yaml"
        bd = carpeta / "datos.sqlite"
        if not bd.exists():
            return m
        m["bytes"] = bd.stat().st_size
        hoy = dt.date.today().isoformat()
        try:
            con = sqlite3.connect(f"{bd.as_uri()}?mode=ro", uri=True, timeout=3)
            try:
                for tipo, d in con.execute("SELECT tipo, datos FROM registros WHERE tipo IN ('pedido','escalado')"):
                    p = json.loads(d)
                    if tipo == "escalado":
                        m["avisos"] += p.get("estado") == "abierto"
                        continue
                    creado = p.get("creado_at") or ""
                    m["hoy"] += creado[:10] == hoy
                    m["ultimo"] = max(m["ultimo"] or "", creado) or None
                    if p.get("estado") == "entregado" and (p.get("estado_at") or creado)[:7] == hoy[:7]:
                        m["ventas_mes"] += int(p.get("total_c") or 0)
            finally:
                con.close()
        except (sqlite3.Error, ValueError):
            m["error"] = "base de datos"
        return m

    def _pagos(self):
        """slug -> últimos 12 pagos (más reciente primero) de pagos.jsonl."""
        out = {}
        try:
            lineas = (self.ruta.parent / "pagos.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for ln in lineas:
            try:
                p = json.loads(ln)
                out.setdefault(p["slug"], []).append(p)
            except (ValueError, KeyError, TypeError):
                continue
        return {k: v[-12:][::-1] for k, v in out.items()}

    @staticmethod
    def _fecha(v):
        try:
            return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))
        except ValueError:
            return None

    @staticmethod
    def estado_pago(hasta, hoy=None):
        """-> (estado, días, etiqueta). estado: al_dia | por_vencer | vencido | sin_pago."""
        hoy = hoy or dt.date.today()
        if hasta is None:
            return "sin_pago", None, "Sin pago registrado"
        n = (hasta - hoy).days
        if n < 0:
            return "vencido", n, f"Vencido hace {-n} días"
        if n <= AVISO_DIAS:
            return "por_vencer", n, f"Vence en {n} días"
        return "al_dia", n, "Al día"

    @staticmethod
    def _uso(carpeta):
        """Mensajes de IA de hoy (uso_ia, solo lectura); 0 si no hay BD o tabla."""
        bd = carpeta / "datos.sqlite"
        if not bd.exists():
            return 0
        try:
            con = sqlite3.connect(f"{bd.as_uri()}?mode=ro", uri=True, timeout=3)
            try:
                r = con.execute("SELECT mensajes FROM uso_ia WHERE dia=?", (dt.date.today().isoformat(),)).fetchone()
            finally:
                con.close()
            return int(r[0]) if r else 0
        except (sqlite3.Error, ValueError, TypeError):
            return 0

    @staticmethod
    def estado_prueba(hasta, hoy=None):
        """-> (dias, etiqueta); (None, '') si no está en prueba."""
        if hasta is None:
            return None, ""
        n = (hasta - (hoy or dt.date.today())).days
        return n, (f"Prueba: quedan {n} días" if n >= 0 else f"Prueba terminada hace {-n} días (pausado)")

    def listar(self):
        """-> (clientes, resumen). Resumen: activos, ingresos (suma de precio de los no vencidos), vencidos."""
        out, entradas, pagos = [], self._yaml()["clientes"], self._pagos()
        for c in self.cargar(self.ruta):
            e = entradas.get(c.slug) or {}
            m = self._metricas(Path(c.carpeta))
            if isinstance(e.get("modulos"), list):
                m["modulos"] = [str(x) for x in e["modulos"]]
            hasta = self._fecha(e.get("pagado_hasta"))
            est, dias, et = self.estado_pago(hasta)
            ph = self._fecha(e.get("prueba_hasta"))
            pd, pet = self.estado_prueba(ph)
            tope = e.get("tope_ia_dia")
            tope = tope if isinstance(tope, int) and not isinstance(tope, bool) else (TOPE_PRUEBA if ph else 0)
            precio = e.get("precio") if isinstance(e.get("precio"), (int, float)) else None
            out.append({"slug": c.slug, "dominio": c.dominio or "", "activo": bool(c.activo), **m,
                        "pagado_hasta": hasta.isoformat() if hasta else None, "estado_pago": est, "dias": dias, "etiqueta_pago": et,
                        "prueba_hasta": ph.isoformat() if ph else None, "prueba_dias": pd, "etiqueta_prueba": pet,
                        "uso_ia": self._uso(Path(c.carpeta)), "tope_ia": tope,
                        "precio": precio, "notas": str(e.get("notas") or ""), "pagos": pagos.get(c.slug, [])})
        resumen = {"activos": sum(c["activo"] for c in out),
                   "ingresos": sum(c["precio"] or 0 for c in out if c["estado_pago"] in ("al_dia", "por_vencer")),
                   "vencidos": sum(c["estado_pago"] == "vencido" for c in out),
                   "en_prueba": sum(c["prueba_dias"] is not None for c in out),
                   "pruebas_por_vencer": sum(c["prueba_dias"] is not None and 0 <= c["prueba_dias"] <= 3 for c in out)}
        return out, resumen

    def _entrada(self, slug):
        self._carpeta(slug)
        d = self._yaml()
        return d, d["clientes"][slug]

    def configurar(self, slug, d):
        """Escribe modulos / precio / notas (solo las claves presentes); modulos=null quita el override."""
        datos, e = self._entrada(slug)
        if "modulos" in d:
            m = d["modulos"]
            if m is None:
                e.pop("modulos", None)
            elif isinstance(m, list) and all(isinstance(x, str) and x in CATALOGO for x in m):
                e["modulos"] = [x for x in CATALOGO if x in m]
            else:
                raise ValueError("módulo fuera del catálogo")
        if "precio" in d:
            p = d["precio"]
            if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1e6:
                raise ValueError("precio inválido")
            e["precio"] = p
        if "notas" in d:
            if not isinstance(d["notas"], str) or len(d["notas"]) > 500:
                raise ValueError("notas: máximo 500 caracteres")
            e["notas"] = d["notas"]
        if "tope_ia_dia" in d:
            t = d["tope_ia_dia"]
            if isinstance(t, bool) or not isinstance(t, int) or not 0 <= t <= 100000:
                raise ValueError("tope IA: entero entre 0 y 100000")
            e["tope_ia_dia"] = t
        self._guardar(datos)

    def prueba(self, slug, accion, dias=15, meses=1):
        """iniciar: hoy+dias | extender: max(hoy, prueba_hasta)+dias | pagar: quita la prueba y registra el pago (meses)."""
        if accion not in ("iniciar", "extender", "pagar"):
            raise ValueError("acción inválida")
        if isinstance(dias, bool) or dias not in (7, 15, 30):
            raise ValueError("días: 7, 15 o 30")
        datos, e = self._entrada(slug)
        hoy = dt.date.today()
        if accion == "pagar":
            if isinstance(meses, bool) or meses not in (1, 3, 12):
                raise ValueError("meses: 1, 3 o 12")
            e.pop("prueba_hasta", None)
            self._guardar(datos)
            return self.pagar(slug, meses)
        base = hoy if accion == "iniciar" else max(hoy, self._fecha(e.get("prueba_hasta")) or hoy)
        e["prueba_hasta"] = (base + dt.timedelta(days=dias)).isoformat()
        self._guardar(datos)
        return e["prueba_hasta"]

    def pagar(self, slug, meses):
        """Marcó pagado: pagado_hasta = max(hoy, pagado_hasta) + 30 días x meses; anota la línea en pagos.jsonl."""
        if isinstance(meses, bool) or meses not in (1, 3, 12):
            raise ValueError("meses: 1, 3 o 12")
        datos, e = self._entrada(slug)
        hoy = dt.date.today()
        nuevo = max(hoy, self._fecha(e.get("pagado_hasta")) or hoy) + dt.timedelta(days=30 * meses)
        e["pagado_hasta"] = nuevo.isoformat()
        self._guardar(datos)
        with (self.ruta.parent / "pagos.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fecha": hoy.isoformat(), "slug": slug, "monto": (e.get("precio") or 0) * meses, "meses": meses}) + "\n")
        return nuevo.isoformat()

    # --- acciones ---
    @staticmethod
    def _validar_alta(d):
        """-> (nombre, slug, dominio, pin, color, días) ya limpios. ValueError en español; el PIN nunca va en el mensaje."""
        nombre, slug, dominio, pin, color, dias = (d.get(k) for k in ("nombre", "slug", "dominio", "pin", "color", "prueba_dias"))
        if not (isinstance(slug, str) and SLUG.fullmatch(slug)):
            raise ValueError("slug inválido: usa a-z, 0-9 y guiones")
        nombre = nombre.strip() if isinstance(nombre, str) else ""
        if not 1 <= len(nombre) <= 60 or any(unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") for c in nombre):
            raise ValueError("nombre: de 1 a 60 caracteres, en una sola línea y sin caracteres de control")
        dominio = dominio.strip().lower() if isinstance(dominio, str) else ""
        if len(dominio) > 100 or not clientes.DOMINIO.fullmatch(dominio) or dominio.endswith(".localhost"):
            raise ValueError("dominio inválido: sin https://, puerto ni ruta (p. ej. restaurante.com)")
        if color is not None and not (isinstance(color, str) and COLOR.fullmatch(color)):
            raise ValueError("color inválido: usa el formato #rrggbb")
        if dias is None:
            dias = 0
        if type(dias) is not int or dias not in (0, 7, 15, 30):
            raise ValueError("días de prueba: 0, 7, 15 o 30")
        if pin is None:
            while (pin := f"{secrets.randbelow(10**6):06d}") in panel_web.PIN_DEBILES:
                pass
        elif not (isinstance(pin, str) and pin.isascii() and pin.isdigit() and len(pin) == 6 and pin not in panel_web.PIN_DEBILES):
            raise ValueError("El PIN debe tener 6 números y no ser trivial")
        return nombre, slug, dominio, pin, color and color.lower(), dias

    def _rellenar(self, destino, nombre, slug, color):
        """Pone nombre, marca y slug en la copia de la plantilla. En YAML el nombre entra como escalar entre comillas de safe_dump (nunca texto pegado)."""
        y = lambda v: yaml.safe_dump(v, default_style='"', allow_unicode=True, width=float("inf")).strip()
        for f, v in (("servicio.yaml", {"nombre": y(nombre), "marca": y(nombre[:40].strip()), "slug": slug}),  # marca: 40 es el tope del panel
                     ("conocimiento.md", {"nombre": nombre, "slug": slug}), ("aviso.md", {"nombre": nombre, "slug": slug})):
            if (destino / f).is_file():
                t = (destino / f).read_text(encoding="utf-8")
                (destino / f).write_text(MARCADOR.sub(lambda m: v.get(m[1], m[0]), t), encoding="utf-8")  # una pasada: un «{slug}» escrito en el nombre queda literal
        if color:  # ponytail: copia del tema del motor + el color; no sigue los cambios futuros de motor/estatico/tema.css (regenerar si hace falta)
            (destino / "tema.css").write_text(TEMA.read_text(encoding="utf-8") + f"\n:root{{--primary:{color};--primary-container:{color}}}\n", encoding="utf-8")

    def nuevo(self, d):
        """Alta completa: carpeta desde la plantilla, usuario Dueno (solo el hash del PIN) en la BD del cliente y entrada en clientes.yaml.
        Valida todo antes de tocar el disco; si algo falla a mitad, deshace carpeta, usuario y YAML. El PIN en claro solo sale en lo que devuelve."""
        nombre, slug, dominio, pin, color, dias = self._validar_alta(d)
        y = self._yaml()
        destino = self.raiz / "servicios" / slug
        if slug in y["clientes"] or destino.exists():
            raise FileExistsError("Ya existe un cliente con ese identificador")
        if self.ruta.exists() and dominio in {c.dominio for c in self.cargar(self.ruta)}:
            raise FileExistsError("Ese dominio ya es de otro cliente")
        hasta = (dt.date.today() + dt.timedelta(days=dias)).isoformat() if dias else None
        y["clientes"][slug] = {"carpeta": f"servicios/{slug}", "dominio": dominio, "activo": True, **({"prueba_hasta": hasta} if hasta else {})}
        orig = self.ruta.read_bytes() if self.ruta.exists() else None
        sv = sembrado = None
        try:
            shutil.copytree(self.plantilla, destino)
            self._rellenar(destino, nombre, slug, color)
            sv = Servicio(destino, env=clientes.entorno(slug))  # crea su BD (SQLite en la carpeta, o su Turso si tiene variables propias)
            if panel_web._todos(sv):  # una BD de Turso reutilizada: no se pisa a su dueño
                raise ValueError("La base de datos de ese cliente ya tiene usuarios")
            panel_web._poner(sv, {"nombre": "Dueno", "rol": "dueno", "pin_hash": panel_web.hash_pin(pin), "activo": True})
            sembrado = True
            self._guardar(y)
            self.cargar(self.ruta)  # mismas reglas que al arrancar (dominio único, carpeta, variables...)
        except Exception:
            if sembrado:  # en SQLite se va con la carpeta; en Turso hay que quitarlo (el menú de ejemplo sembrado allí se queda: no da acceso a nada)
                try:
                    sv._q("DELETE FROM usuarios WHERE nombre='Dueno'")
                except Exception:
                    pass
            if sv:
                sv.cerrar()
            shutil.rmtree(destino, ignore_errors=True)
            self.ruta.write_bytes(orig) if orig is not None else self.ruta.unlink(missing_ok=True)
            self.ruta.with_suffix(".tmp").unlink(missing_ok=True)
            raise
        sv.cerrar()
        return {"slug": slug, "url_web": f"https://{dominio}", "url_panel": f"https://{dominio}/panel", "prueba_hasta": hasta, "pin": pin}

    def pausar(self, slug, activo):
        self._carpeta(slug)
        d = self._yaml()
        d["clientes"][slug]["activo"] = bool(activo)
        self._guardar(d)

    def respaldo(self, slug):
        bd = self._carpeta(slug) / "datos.sqlite"
        if not bd.exists():
            raise FileNotFoundError("sin base de datos")
        dest = self.raiz / "backups" / slug
        dest.mkdir(parents=True, exist_ok=True)
        fin = dest / f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.sqlite"
        src = sqlite3.connect(f"{bd.as_uri()}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(fin)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        return fin.name


def handler(op):
    class H(BaseHTTPRequestHandler):
        server_version = "operador"
        timeout = 15  # conexión lenta/colgada: se suelta

        def _ip(self):
            """IP real: con PROXY=1 el último valor de X-Forwarded-For (lo añade Caddy; el primero lo escribe el cliente)."""
            if os.environ.get("PROXY") == "1":
                try:
                    return str(ipaddress.ip_address((self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()))
                except ValueError:
                    pass
            return self.client_address[0]

        def log_message(self, *a):
            pass

        def _send(self, st, cuerpo, tipo="application/json", extra=()):
            b = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo, ensure_ascii=False).encode()
            self.send_response(st)
            for k, v in (("Content-Type", tipo + "; charset=utf-8"), ("Content-Length", str(len(b))), ("Cache-Control", "no-store"),
                         ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                         ("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'"), *extra):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _json(self):
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 4096))
                d = json.loads(self.rfile.read(n) or b"{}")
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}

        def do_GET(self):
            if self.path == "/":
                return self._send(200, HTML.read_bytes(), "text/html")
            if not op.valida(self.headers.get("Cookie")):
                return self._send(401, {"error": "no autorizado"})
            if self.path == "/api/clientes":
                cl, res = op.listar()
                return self._send(200, {"clientes": cl, "resumen": res, "catalogo": list(CATALOGO)})
            self._send(404, {"error": "no existe"})

        def do_POST(self):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(415, {"error": "json"})
            d = self._json()
            if self.path == "/api/entrar":
                try:
                    ck = op.entrar(str(d.get("clave", "")), self._ip())
                except LookupError:
                    return self._send(429, {"error": "demasiados intentos, espera unos minutos"})
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", ck)]) if ck else self._send(401, {"error": "clave incorrecta"})
            if self.path == "/api/salir":
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", op.cookie_salir())])
            if not op.valida(self.headers.get("Cookie")):
                return self._send(401, {"error": "no autorizado"})
            partes = self.path.strip("/").split("/")
            try:
                if partes == ["api", "clientes"]:
                    try:
                        return self._send(200, {"ok": True, **op.nuevo(d)})  # única respuesta que lleva el PIN en claro
                    except (ValueError, LookupError, FileExistsError):
                        raise
                    except Exception:  # fallo interno a mitad del alta (ya deshecha): sin detalle, que podría arrastrar datos de la petición
                        return self._send(500, {"error": "No se pudo crear el cliente; no se guardó nada"})
                if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "pausa":
                    op.pausar(partes[2], d.get("activo"))
                    return self._send(200, {"ok": True})
                if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "config":
                    op.configurar(partes[2], d)
                    return self._send(200, {"ok": True})
                if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "pago":
                    return self._send(200, {"ok": True, "pagado_hasta": op.pagar(partes[2], d.get("meses", 1))})
                if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "prueba":
                    hasta = op.prueba(partes[2], d.get("accion"), d.get("dias", 15), d.get("meses", 1))
                    return self._send(200, {"ok": True, "hasta": hasta})
                if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "respaldo":
                    return self._send(200, {"ok": True, "archivo": op.respaldo(partes[2])})
            except FileExistsError as e:
                return self._send(409, {"error": str(e)})
            except (ValueError, LookupError, FileNotFoundError) as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "no existe"})
    return H


def servir(ruta_clientes="clientes.yaml", puerto=8081):
    host = os.environ.get("OPERADOR_HOST", "127.0.0.1")  # 0.0.0.0 solo dentro de Docker, detrás de Caddy (TLS)
    op = Operador(ruta_clientes, os.environ.get("OPERADOR_CLAVE", ""), seguro=host != "127.0.0.1")
    srv = ThreadingHTTPServer((host, int(puerto)), handler(op))
    print(f"Consola del operador en http://{host}:{puerto}")
    srv.serve_forever()
