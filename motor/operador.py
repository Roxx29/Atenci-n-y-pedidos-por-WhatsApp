"""Consola del operador: panel APARTE solo para el dueño del sistema (lista de clientes, alta, pausa, copias).
Solo agregados: nunca teléfonos, nombres ni datos personales de los clientes finales (Ley 81).
Uso: OPERADOR_CLAVE=... python -m motor operador [clientes.yaml] [puerto]"""
import contextlib
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
from urllib.parse import parse_qs, urlsplit

import yaml

from . import clientes, panel_web, temas, web
from .nucleo import Servicio

SLUG = re.compile(r"[a-z0-9-]{1,40}")
HTML = Path(__file__).with_name("operador.html")
SESION = 8 * 3600
MAX_FALLOS, BLOQUEO = 5, 300
CATALOGO = ("estadisticas",)  # módulos que el operador puede activar; añade aquí los nuevos
AVISO_DIAS = 5
COLOR = temas.COLOR
MARCADOR = re.compile(r"\{(nombre|marca|slug)\}")  # los que rellena el alta en la plantilla
TOPE_PRUEBA = 300 # tope IA diario por defecto en prueba (el contrato lo aplica clientes.py)
CLAVE_MIN = 12
MAX_CUERPO_CSS = 48_000  # cuerpo JSON de …/personalizado: 20 KB de CSS ya escapado (las demás rutas, 4 KB)
MAX_REGISTRO = 1_000_000  # bytes de registro.jsonl antes de rotarlo a registro.jsonl.1 (se guarda una sola generación)


class Conflicto(Exception):
    """La petición es válida pero el estado no la admite (409)."""


class Operador:
    def __init__(self, ruta_clientes, clave, raiz=None, plantilla=None, seguro=False):
        if not clave or len(clave) < CLAVE_MIN:
            raise SystemExit(f"OPERADOR_CLAVE es obligatoria y debe tener al menos {CLAVE_MIN} caracteres (tiene {len(clave or '')}): la consola no arranca sin ella.")
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

    # --- registro de actividad ---
    def anotar(self, accion, slug, ip, resultado):
        """Una línea en registro.jsonl (junto a clientes.yaml). Solo estos cinco campos: nunca PINs, claves ni datos de personas."""
        f = self.ruta.parent / "registro.jsonl"
        linea = json.dumps({"fecha": dt.datetime.now().isoformat(timespec="seconds"), "accion": accion, "slug": slug, "ip": ip, "resultado": resultado})
        with self.lock:
            try:
                if f.exists() and f.stat().st_size > MAX_REGISTRO:
                    os.replace(f, f.with_name(f.name + ".1"))
                with f.open("a", encoding="utf-8") as h:
                    h.write(linea + "\n")
            except OSError as e:  # la acción ya ocurrió: no se le devuelve un error por no poder anotarla
                print(f"[operador] no se pudo escribir el registro: {type(e).__name__}")

    def registro(self, n=100):
        """Las últimas n líneas (la más reciente primero), contando la generación rotada."""
        f, lineas = self.ruta.parent / "registro.jsonl", []
        for g in (f.with_name(f.name + ".1"), f):
            try:
                lineas += g.read_text(encoding="utf-8").splitlines()
            except OSError:
                pass
        out = []
        for ln in lineas[-n:][::-1]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

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
                        "precio": precio, "notas": str(e.get("notas") or ""), "pagos": pagos.get(c.slug, []),
                        "archivado": c.archivado.isoformat() if c.archivado else None,
                        **self._tema_de(Path(c.carpeta)), "personalizado": (Path(c.carpeta) / "personalizado.css").is_file()})
        vivos = [c for c in out if not c["archivado"]]  # los archivados se cuentan aparte: ni ingresos ni vencidos ni pruebas
        resumen = {"activos": sum(c["activo"] for c in vivos),
                   "ingresos": sum(c["precio"] or 0 for c in vivos if c["estado_pago"] in ("al_dia", "por_vencer")),
                   "vencidos": sum(c["estado_pago"] == "vencido" for c in vivos),
                   "en_prueba": sum(c["prueba_dias"] is not None for c in vivos),
                   "pruebas_por_vencer": sum(c["prueba_dias"] is not None and 0 <= c["prueba_dias"] <= 3 for c in vivos),
                   "archivados": len(out) - len(vivos)}
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
        """-> (nombre, slug, dominio, pin, color, días, tema) ya limpios. ValueError en español; el PIN nunca va en el mensaje."""
        nombre, slug, dominio, pin, color, dias = (d.get(k) for k in ("nombre", "slug", "dominio", "pin", "color", "prueba_dias"))
        tema = d.get("tema", "clasico")
        if not (isinstance(slug, str) and SLUG.fullmatch(slug)):
            raise ValueError("slug inválido: usa a-z, 0-9 y guiones")
        nombre, dominio, color = Operador._nombre(nombre), Operador._dominio(dominio), Operador._color(color)
        if dias is None:
            dias = 0
        if type(dias) is not int or dias not in (0, 7, 15, 30):
            raise ValueError("días de prueba: 0, 7, 15 o 30")
        temas.generar_tema(tema, color)  # ValueError si el tema no está en el catálogo
        return nombre, slug, dominio, Operador._pin(pin), color, dias, tema

    @staticmethod
    def _nombre(nombre):
        nombre = nombre.strip() if isinstance(nombre, str) else ""
        if not 1 <= len(nombre) <= 60 or any(unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") for c in nombre):
            raise ValueError("nombre: de 1 a 60 caracteres, en una sola línea y sin caracteres de control")
        return nombre

    @staticmethod
    def _dominio(dominio):
        dominio = dominio.strip().lower() if isinstance(dominio, str) else ""
        if len(dominio) > 100 or not clientes.DOMINIO.fullmatch(dominio) or dominio.endswith(".localhost"):
            raise ValueError("dominio inválido: sin https://, puerto ni ruta (p. ej. restaurante.com)")
        return dominio

    @staticmethod
    def _color(color):
        if color is not None and not (isinstance(color, str) and COLOR.fullmatch(color)):
            raise ValueError("color inválido: usa el formato #rrggbb")
        return color and color.lower()

    @staticmethod
    def _pin(pin):
        """El PIN dado si cumple las reglas del panel; None = uno aleatorio (secrets)."""
        if pin is None:
            while (pin := f"{secrets.randbelow(10**6):06d}") in panel_web.PIN_DEBILES:
                pass
        elif not (isinstance(pin, str) and pin.isascii() and pin.isdigit() and len(pin) == 6 and pin not in panel_web.PIN_DEBILES):
            raise ValueError("El PIN debe tener 6 números y no ser trivial")
        return pin

    @staticmethod
    def _poner(f, texto):
        """Escritura atómica: o queda el archivo entero o queda el de antes."""
        tmp = f.with_name(f.name + ".tmp")
        try:
            tmp.write_bytes(texto.encode())  # bytes: en Windows write_text cambiaría los saltos de línea
            os.replace(tmp, f)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _foto(*archivos):
        return [(f, f.read_bytes() if f.exists() else None) for f in archivos]

    @staticmethod
    def _volver(antes):
        for f, b in antes:
            f.write_bytes(b) if b is not None else f.unlink(missing_ok=True)

    @staticmethod
    def _tema(destino, tema, color):
        # ponytail: tema.css es una copia generada (motor + tokens + color); no sigue los cambios futuros del motor hasta volver a aplicar el tema
        Operador._poner(destino / "tema.css", temas.generar_tema(tema, color))
        Operador._poner(destino / "tema.json", json.dumps({"tema": tema, "color": color}))

    @staticmethod
    def _tema_de(carpeta):
        return temas.tema_de(carpeta)

    def tema(self, slug, d):
        """Aplica un tema del catálogo (y, opcional, un color primario) SOLO a ese cliente: regenera su tema.css y anota el tema en su tema.json.
        Sin `color` quedan los colores del tema. Si falla a mitad, los dos archivos vuelven a como estaban."""
        carpeta = self._carpeta(slug)
        if set(d) - {"tema", "color"}:
            raise ValueError("tema: indica tema y, si quieres, color")
        tema, color = d.get("tema"), self._color(d.get("color"))
        temas.generar_tema(tema, color)  # valida antes de tocar el disco
        antes = self._foto(carpeta / "tema.css", carpeta / "tema.json")
        try:
            self._tema(carpeta, tema, color)
        except Exception:
            self._volver(antes)
            raise
        return {"tema": tema, "color": color}

    def personalizado(self, slug, d):
        """CSS propio de un cliente (personalizado.css; web.armar_pagina lo pone al final de su tema). Vacío = lo borra."""
        f = self._carpeta(slug) / "personalizado.css"
        css = temas.sanear_css(d.get("css"))
        self._poner(f, css) if css else f.unlink(missing_ok=True)
        return {"bytes": len(css.encode())}

    def personalizado_actual(self, slug):
        f = self._carpeta(slug) / "personalizado.css"
        return {"css": f.read_text(encoding="utf-8") if f.is_file() else ""}

    def _rellenar(self, destino, nombre, slug, color, tema="clasico"):
        """Pone nombre, marca y slug en la copia de la plantilla. En YAML el nombre entra como escalar entre comillas de safe_dump (nunca texto pegado)."""
        y = lambda v: yaml.safe_dump(v, default_style='"', allow_unicode=True, width=float("inf")).strip()
        for f, v in (("servicio.yaml", {"nombre": y(nombre), "marca": y(nombre[:40].strip()), "slug": slug}),  # marca: 40 es el tope del panel
                     ("conocimiento.md", {"nombre": nombre, "slug": slug}), ("aviso.md", {"nombre": nombre, "slug": slug})):
            if (destino / f).is_file():
                t = (destino / f).read_text(encoding="utf-8")
                (destino / f).write_text(MARCADOR.sub(lambda m: v.get(m[1], m[0]), t), encoding="utf-8")  # una pasada: un «{slug}» escrito en el nombre queda literal
        if color or tema != "clasico":  # el clásico sin color no necesita archivo: se sirve el del motor
            self._tema(destino, tema, color)

    def nuevo(self, d):
        """Alta completa: carpeta desde la plantilla, usuario Dueno (solo el hash del PIN) en la BD del cliente y entrada en clientes.yaml.
        Valida todo antes de tocar el disco; si algo falla a mitad, deshace carpeta, usuario y YAML. El PIN en claro solo sale en lo que devuelve."""
        nombre, slug, dominio, pin, color, dias, tema = self._validar_alta(d)
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
            self._rellenar(destino, nombre, slug, color, tema)
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
        d, e = self._entrada(slug)
        if e.get("archivado"):
            raise Conflicto("Está archivado: restáuralo primero")
        e["activo"] = bool(activo)
        self._guardar(d)

    def respaldo(self, slug, nombre=None):
        bd = self._carpeta(slug) / "datos.sqlite"
        if not bd.exists():
            raise FileNotFoundError("sin base de datos")
        dest = self.raiz / "backups" / slug
        dest.mkdir(parents=True, exist_ok=True)
        fin = dest / (nombre or f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.sqlite")
        fin.unlink(missing_ok=True)
        src = sqlite3.connect(f"{bd.as_uri()}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(fin)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        return fin.name

    # --- equipo del cliente (su tabla `usuarios`; las reglas, el hash y la auditoría son los del panel) ---
    @contextlib.contextmanager
    def _sv(self, slug):
        sv = Servicio(self._carpeta(slug), env=clientes.entorno(slug))
        try:
            yield sv
        finally:
            sv.cerrar()

    @staticmethod
    def _usuario(sv, nombre):
        if not (isinstance(nombre, str) and panel_web.NOMBRE.fullmatch(nombre)):
            raise ValueError("usuario inválido")
        if not any(u["nombre"] == nombre for u in panel_web._todos(sv)):
            raise LookupError("usuario desconocido")
        return nombre

    def equipo(self, slug):
        with self._sv(slug) as sv:
            return [{k: u[k] for k in ("nombre", "rol", "activo")} for u in panel_web._todos(sv)]  # nunca pin_hash

    def equipo_activo(self, slug, d):
        """Activa o desactiva a una persona. Desactivar cierra sus sesiones (deja de estar entre los usuarios que firman cookies);
        reactivar exige `pin` nuevo, como en el panel (el viejo puede ser ya el de otra persona)."""
        if not isinstance(d.get("activo"), bool):
            raise ValueError("activo debe ser true o false")
        with self._sv(slug) as sv:
            u = {"nombre": self._usuario(sv, d.get("nombre")), "activo": d["activo"], **({"pin": d["pin"]} if d.get("pin") is not None else {})}
            return panel_web.guardar_usuario(sv, u, "operador")

    def pin(self, slug, d):
        """Restablece el PIN de una persona (por defecto Dueno). En la BD solo queda el hash; al cambiar el hash, sus cookies del panel
        dejan de validar (la firma lo incluye). El PIN en claro solo sale en lo que devuelve."""
        dado = d.get("pin")
        pin = self._pin(dado)
        with self._sv(slug) as sv:
            usuario = self._usuario(sv, d.get("usuario", "Dueno"))
            while panel_web._pin_en_uso(sv, pin, excepto=usuario):
                if dado is not None:
                    raise Conflicto("Ese PIN ya lo usa otra persona")
                pin = self._pin(None)
            panel_web.guardar_usuario(sv, {"nombre": usuario, "pin": pin}, "operador")
        return {"usuario": usuario, "pin": pin}

    # --- ajustes ---
    def ajustes(self, slug, d):
        """Cambia dominio (clientes.yaml), nombre/marca (su servicio.yaml) y color (su tema.css). Valida todo antes de escribir y,
        si algo falla (también lo que rechace clientes.cargar), deja los tres archivos como estaban."""
        carpeta = self._carpeta(slug)
        if not d or set(d) - {"dominio", "nombre", "color"}:
            raise ValueError("ajustes: indica dominio, nombre o color")
        dominio = self._dominio(d["dominio"]) if "dominio" in d else None
        nombre = self._nombre(d["nombre"]) if "nombre" in d else None
        color = self._color(d["color"]) if "color" in d else None
        if "color" in d and not color:
            raise ValueError("color inválido: usa el formato #rrggbb")
        if dominio and any(c.dominio == dominio and c.slug != slug for c in self.cargar(self.ruta)):
            raise FileExistsError("Ese dominio ya es de otro cliente")
        antes = self._foto(self.ruta, carpeta / "servicio.yaml", carpeta / "tema.css", carpeta / "tema.json")
        try:
            if nombre:
                # ponytail: safe_dump conserva los datos pero no los comentarios del servicio.yaml
                cfg = yaml.safe_load(antes[1][1].decode("utf-8"))
                if cfg.get("negocio") == cfg.get("nombre"):
                    cfg["negocio"] = nombre
                cfg["nombre"], cfg["marca"] = nombre, nombre[:40].strip()  # marca: 40 es el tope del panel
                (carpeta / "servicio.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            if color:
                self._tema(carpeta, self._tema_de(carpeta)["tema"], color)  # su tema, con el color nuevo; personalizado.css no se toca
            if dominio:
                y = self._yaml()
                y["clientes"][slug]["dominio"] = dominio
                self._guardar(y)
            self.cargar(self.ruta)  # mismas reglas que al arrancar
        except Exception:
            self._volver(antes)
            self.ruta.with_suffix(".tmp").unlink(missing_ok=True)
            raise

    # --- archivar / restaurar / eliminar ---
    def archivar(self, slug, confirmar):
        """Copia de seguridad de su BD y `activo: false` + `archivado: hoy`. No borra nada. -> nombre de la copia (None sin BD local)."""
        d, e = self._entrada(slug)
        if confirmar != slug:
            raise ValueError("Para confirmar, escribe el identificador del cliente")
        if e.get("archivado"):
            raise Conflicto("Ya está archivado")
        try:
            copia = self.respaldo(slug)
        except FileNotFoundError:
            copia = None
        e["activo"], e["archivado"] = False, dt.date.today().isoformat()
        self._guardar(d)
        return copia

    def restaurar(self, slug):
        d, e = self._entrada(slug)
        if not e.get("archivado"):
            raise Conflicto("No está archivado")
        del e["archivado"]
        e["activo"] = True
        self._guardar(d)

    @staticmethod
    def _triturar(carpeta):
        """Sobrescribe con ceros cada archivo (BD, -wal/-shm, comprobantes, tema...) y borra la carpeta.
        ponytail: una pasada de ceros; en SSD o sistemas de archivos con copia en escritura no garantiza que no queden bloques (para eso, disco cifrado)."""
        for f in carpeta.rglob("*"):
            try:
                if f.is_file() and not f.is_symlink():
                    with f.open("r+b") as h:
                        h.write(bytes(f.stat().st_size))
                        h.flush(); os.fsync(h.fileno())
            except OSError:
                pass
        shutil.rmtree(carpeta, ignore_errors=True)

    def eliminar(self, slug, confirmar):
        """Borrado definitivo, SOLO de un archivado: copia final a backups/<slug>/ELIMINADO-<fecha>.sqlite, fuera su carpeta y su entrada.
        -> {copia, pendiente}: `pendiente` si algún archivo no se pudo borrar (queda en servicios/.eliminando-<slug> y se reintenta en el siguiente eliminar)."""
        d, e = self._entrada(slug)
        carpeta = self._carpeta(slug)
        if confirmar != slug:
            raise ValueError("Para confirmar, escribe el identificador del cliente")
        if not e.get("archivado"):
            raise Conflicto("Solo se puede eliminar un cliente archivado")
        base = (self.raiz / "servicios").resolve()
        if carpeta.parent != base or carpeta == self.plantilla.resolve():  # solo una carpeta hija directa de servicios/ (ya resuelta: sin enlaces)
            raise ValueError("Su carpeta no está en servicios/: no se borra desde la consola")
        for resto in base.glob(".eliminando-*"):  # de un intento anterior
            self._triturar(resto)
        try:
            copia = self.respaldo(slug, f"ELIMINADO-{dt.date.today().isoformat()}.sqlite")
        except FileNotFoundError:
            copia = None
        tumba, orig = base / f".eliminando-{slug}", self.ruta.read_bytes()
        try:
            os.rename(carpeta, tumba)  # o todo o nada: si no se puede retirar la carpeta, no se toca el YAML
        except OSError:
            raise Conflicto("No se pudo retirar su carpeta (¿la tiene abierta el servidor?): reinicia `alojar` y reintenta") from None
        try:
            del d["clientes"][slug]
            self._guardar(d)
        except Exception:
            os.rename(tumba, carpeta)
            self.ruta.write_bytes(orig)
            raise
        self._triturar(tumba)
        return {"copia": copia, "pendiente": tumba.exists()}


# ruta tras /api/clientes/<slug>/ -> (acción en el registro, función(op, slug, cuerpo) -> dict que se suma a {ok: true})
RUTAS = {
    "pausa": ("pausa", lambda op, s, d: op.pausar(s, d.get("activo"))),
    "config": ("config", lambda op, s, d: op.configurar(s, d)),
    "pago": ("pago", lambda op, s, d: {"pagado_hasta": op.pagar(s, d.get("meses", 1))}),
    "prueba": ("prueba", lambda op, s, d: {"hasta": op.prueba(s, d.get("accion"), d.get("dias", 15), d.get("meses", 1))}),
    "respaldo": ("backup", lambda op, s, d: {"archivo": op.respaldo(s)}),
    "pin": ("pin", lambda op, s, d: op.pin(s, d)),  # única, con el alta, que responde un PIN en claro
    "equipo/activo": ("equipo", lambda op, s, d: op.equipo_activo(s, d)),
    "ajustes": ("ajustes", lambda op, s, d: op.ajustes(s, d)),
    "tema": ("tema", lambda op, s, d: op.tema(s, d)),
    "personalizado": ("personalizado", lambda op, s, d: op.personalizado(s, d)),  # al registro va la acción, nunca el CSS
    "archivar": ("archivar", lambda op, s, d: {"copia": op.archivar(s, d.get("confirmar"))}),
    "restaurar": ("restaurar", lambda op, s, d: op.restaurar(s)),
    "eliminar": ("eliminar", lambda op, s, d: op.eliminar(s, d.get("confirmar"))),
}


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
            cab = {"Content-Type": tipo + "; charset=utf-8", "Content-Length": str(len(b)), "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                   "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'"}
            for k, v in {**cab, **dict(extra)}.items():  # extra sustituye (CSP de la vista previa) o añade (Set-Cookie)
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _json(self, tope=4096):
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), tope))
                d = json.loads(self.rfile.read(n) or b"{}")
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}

        def _hacer(self, f):
            """-> (estado, cuerpo) de una acción: errores {error} en español, sin detalle interno en el 500."""
            try:
                r = f()
                return 200, r if isinstance(r, list) else {"ok": True, **(r or {})}
            except (FileExistsError, Conflicto) as e:
                return 409, {"error": str(e)}
            except LookupError as e:
                return 404, {"error": str(e.args[0]) if e.args else "no existe"}
            except (ValueError, FileNotFoundError) as e:
                return 400, {"error": str(e)}
            except Exception as e:  # el texto podría arrastrar datos de la petición: solo el tipo, y solo a la salida del servidor
                print(f"[operador] error interno: {type(e).__name__}")
                return 500, {"error": "Error interno: no se guardó nada"}

        def do_GET(self):
            u = urlsplit(self.path)
            partes = u.path.strip("/").split("/")
            if u.path == "/":
                return self._send(200, HTML.read_bytes(), "text/html")
            if not op.valida(self.headers.get("Cookie")):
                return self._send(401, {"error": "no autorizado"})
            if u.path == "/api/clientes":
                cl, res = op.listar()
                return self._send(200, {"clientes": cl, "resumen": res, "catalogo": list(CATALOGO)})
            if u.path == "/api/registro":
                n = parse_qs(u.query).get("n", ["100"])[-1]
                if not (n.isascii() and n.isdigit() and len(n) <= 6 and int(n) >= 1):
                    return self._send(400, {"error": "n: un entero de 1 a 500"})
                return self._send(200, op.registro(min(int(n), 500)))
            if u.path == "/api/temas":  # sin las reglas css (solo las usa la página servida)
                return self._send(200, [{k: v for k, v in t.items() if k != "css"} for t in temas.catalogo()])
            if len(partes) == 3 and partes[0] == "vista":  # /vista/<tema>/<web|panel|repartidor>: página real, datos de ejemplo
                try:
                    h = web.armar_vista(partes[1], partes[2])
                except ValueError:
                    return self._send(404, {"error": "no existe"})
                return self._send(200, h, "text/html", [("Content-Security-Policy", web.CSP_VISTA), ("X-Frame-Options", "SAMEORIGIN")])
            if u.path.startswith("/estatico/") and u.path[10:] in web.ESTATICOS:  # fuentes de la vista previa (lista blanca)
                return self._send(200, (web.ESTATICO / u.path[10:]).read_bytes(), web.ESTATICOS[u.path[10:]], [("Cache-Control", "max-age=86400")])
            if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "equipo":
                return self._send(*self._hacer(lambda: op.equipo(partes[2])))
            if len(partes) == 4 and partes[:2] == ["api", "clientes"] and partes[3] == "personalizado":
                return self._send(*self._hacer(lambda: op.personalizado_actual(partes[2])))
            self._send(404, {"error": "no existe"})

        def do_POST(self):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(415, {"error": "json"})
            d = self._json(MAX_CUERPO_CSS if self.path.endswith("/personalizado") else 4096)
            if self.path == "/api/entrar":
                try:
                    ck = op.entrar(str(d.get("clave", "")), self._ip())
                except LookupError:
                    op.anotar("login", "", self._ip(), "bloqueado")
                    return self._send(429, {"error": "demasiados intentos, espera unos minutos"})
                op.anotar("login", "", self._ip(), "ok" if ck else "fallido")
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", ck)]) if ck else self._send(401, {"error": "clave incorrecta"})
            if self.path == "/api/salir":
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", op.cookie_salir())])
            if not op.valida(self.headers.get("Cookie")):
                return self._send(401, {"error": "no autorizado"})
            partes = self.path.strip("/").split("/")
            if partes == ["api", "clientes"]:
                accion, slug, f = "alta", d.get("slug"), lambda: op.nuevo(d)
            elif len(partes) >= 4 and partes[:2] == ["api", "clientes"] and "/".join(partes[3:]) in RUTAS:
                accion, g = RUTAS["/".join(partes[3:])]
                slug, f = partes[2], lambda: g(op, partes[2], d)
            else:
                return self._send(404, {"error": "no existe"})
            st, cuerpo = self._hacer(f)
            # el slug solo se anota si tiene forma de slug: lo demás es texto de quien llama
            op.anotar(accion, slug if isinstance(slug, str) and SLUG.fullmatch(slug) else "", self._ip(), "ok" if st == 200 else f"error {st}")
            self._send(st, cuerpo)
    return H


def servir(ruta_clientes="clientes.yaml", puerto=8081):
    host = os.environ.get("OPERADOR_HOST", "127.0.0.1")  # 0.0.0.0 solo dentro de Docker, detrás de Caddy (TLS)
    op = Operador(ruta_clientes, os.environ.get("OPERADOR_CLAVE", ""), seguro=host != "127.0.0.1")
    srv = ThreadingHTTPServer((host, int(puerto)), handler(op))
    print(f"Consola del operador en http://{host}:{puerto}")
    srv.serve_forever()
