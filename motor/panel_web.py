"""Panel web del restaurante (rutas /panel* del servidor `motor web`). Solo stdlib.

Acceso del personal (.env): PANEL_PIN (6 dígitos, rol dueno) o PANEL_PIN_HASH ("pbkdf2$iter$sal$hash"),
y opcional PANEL_PIN_PERSONAL / PANEL_PIN_PERSONAL_HASH (rol personal). Sin PIN válido, /panel está deshabilitado (404).
Las reglas de estado son las de herramientas.marcar_pedido / cancelar_pedido.

  python -m motor.panel_web 123456      # imprime un PANEL_PIN_HASH
"""
import collections
import datetime as dt
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
from collections.abc import Mapping

from . import mesa, clientes
from . import herramientas, pedidos_admin, seguridad
from . import ruta as reparto
from . import ubicacion
from .herramientas import dinero

VIDA_S = 12 * 3600       # tope absoluto de la sesión
INACTIVO_S = 30 * 60     # se renueva con cada llamada
TERMINADOS = {"entregado", "cancelado", "caducado"}
PIN_DEBILES = {"000000", "123456", "111111", "654321", "123123"}
CT_IMAGEN = {"jpg": "image/jpeg", "png": "image/png"}
PAGO_PENDIENTE = {"confirmado", "pago_reportado"}


def hash_pin(pin, iteraciones=200_000):
    sal = secrets.token_hex(16)
    return f"pbkdf2${iteraciones}${sal}${hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(sal), iteraciones).hex()}"


def _coincide(pin, texto, hash_):
    if hash_:
        try:
            _, it, sal, h = hash_.split("$")
            return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(sal), int(it)).hex(), h)
        except ValueError:
            return False
    return bool(texto) and hmac.compare_digest(pin.encode(), texto.encode())


def es_lan(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback


def pin_valido(texto, hash_, etiqueta):
    """Un PIN en texto debe tener 6 dígitos y no ser trivial; un hash vale siempre."""
    if texto and not hash_ and not (texto.isascii() and texto.isdigit() and len(texto) == 6 and texto not in PIN_DEBILES):
        print(f"[panel] {etiqueta} ignorado: debe tener 6 dígitos y no ser trivial")
        return False
    return bool(texto or hash_)


def env_panel(env=None):
    """`env`: de dónde salen los PIN (por defecto os.environ; en `motor alojar`, solo los del cliente)."""
    roles, env = {}, os.environ if env is None else env
    for rol, p, h in (("dueno", "PANEL_PIN", "PANEL_PIN_HASH"), ("personal", "PANEL_PIN_PERSONAL", "PANEL_PIN_PERSONAL_HASH")):
        texto, hash_ = env.get(p) or None, env.get(h) or None
        if pin_valido(texto, hash_, p):
            roles[rol] = (texto, hash_)
    return roles


def env_repartidores(env=None):
    """{nombre: (pin, hash)} de REPARTIDORES="Juan:482913,Pedro:pbkdf2$..." (y REPARTIDOR_PIN = un repartidor llamado Repartidor). Nombres A-Za-z0-9_-; PIN únicos."""
    env = os.environ if env is None else env
    crudo = [x.strip().partition(":")[::2] for x in env.get("REPARTIDORES", "").split(",") if ":" in x]
    if env.get("REPARTIDOR_PIN"):
        crudo.append(("Repartidor", env["REPARTIDOR_PIN"]))
    out, vistos = {}, set()
    for nombre, pin in crudo:
        nombre, pin = nombre.strip(), pin.strip()
        hash_ = pin if pin.startswith("pbkdf2$") else None
        if not (re.fullmatch(r"[A-Za-z0-9_-]{1,30}", nombre) and nombre not in out and pin not in vistos and pin_valido(None if hash_ else pin, hash_, f"repartidor {nombre}")):
            print(f"[panel] repartidor {nombre!r} ignorado (nombre o PIN repetido o no válido)")
            continue
        vistos.add(pin)
        out[nombre] = (None if hash_ else pin, hash_)
    return out


class Acceso:
    """Roles por PIN y sesiones firmadas (cookie `panel`, distinta de la del cliente). Cambiar un PIN invalida las sesiones."""

    def __init__(self, secreto, roles=None, cookie="panel", ruta="/panel", inactivo=INACTIVO_S):
        self.roles = env_panel() if roles is None else roles
        self.nombre, self.ruta, self.inactivo = cookie, ruta, inactivo
        base = "|".join(f"{r}:{t}:{h}" for r, (t, h) in sorted(self.roles.items()))
        self.clave = hashlib.sha256(secreto.encode() + f"|{cookie}|".encode() + base.encode()).digest()

    @property
    def activo(self):
        return bool(self.roles)

    def rol_de_pin(self, pin):
        # se comprueban todos los roles (sin cortar) para no filtrar cuál acertó por el tiempo
        hallado = None
        for rol, (t, h) in self.roles.items():
            if _coincide(pin, t, h):
                hallado = hallado or rol
        return hallado

    def _firma(self, rol, exp, lim):
        # incluye el PIN (hash) vigente: cambiarlo o desactivar al usuario invalida sus sesiones
        return hmac.new(self.clave, f"{rol}.{exp}.{lim}.{self.roles.get(rol)}".encode(), hashlib.sha256).hexdigest()

    def cookie(self, rol, lim=None, seguro=False):
        """Cookie con inactividad de 30 min (exp) y tope absoluto de 12 h (lim)."""
        lim = lim or int(time.time()) + VIDA_S
        exp = min(int(time.time()) + self.inactivo, lim)
        return f"{self.nombre}={rol}.{exp}.{lim}.{self._firma(rol, exp, lim)}; HttpOnly; SameSite=Strict; Path={self.ruta}; Max-Age={exp - int(time.time())}{'; Secure' if seguro else ''}"

    def borrar_cookie(self, seguro=False):
        return f"{self.nombre}=; HttpOnly; SameSite=Strict; Path={self.ruta}; Max-Age=0{'; Secure' if seguro else ''}"

    def sesion(self, cabecera):
        """(rol, lim) si hay sesión válida; si no, None."""
        for parte in (cabecera or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == self.nombre:
                try:
                    rol, exp, lim, firma = v.split(".")
                    if rol in self.roles and int(exp) > time.time() and hmac.compare_digest(firma, self._firma(rol, exp, lim)):
                        return rol, int(lim)
                except ValueError:
                    pass
        return None


PANEL, REPARTO = ("dueno", "personal"), ("repartidor",)
NOMBRE = re.compile(r"[A-Za-z0-9_-]{1,30}")  # va en la cookie (sin puntos)


class Usuarios(Mapping):
    """Usuarios activos de esos roles (tabla usuarios: nombre, rol, pin_hash, activo), leídos en vivo: {nombre: (None, pin_hash)}."""

    def __init__(self, sv, roles, env=None):
        self.sv, self.de = sv, set(roles)
        sembrar_usuarios(sv, env)

    def _d(self):
        return {u["nombre"]: (None, u["pin_hash"]) for u in _todos(self.sv) if u["activo"] and u["rol"] in self.de}

    def __getitem__(self, k):
        return self._d()[k]

    def __iter__(self):
        return iter(self._d())

    def __len__(self):
        return len(self._d())

    def items(self):
        return self._d().items()

    def rol(self, nombre):
        return next((u["rol"] for u in _todos(self.sv) if u["nombre"] == nombre and u["activo"]), None)


def _todos(sv):
    return [{"nombre": n, "rol": r, "pin_hash": h, "activo": bool(a)} for n, r, h, a in sv._q("SELECT nombre, rol, pin_hash, activo FROM usuarios ORDER BY rowid")]


def _poner(sv, u):
    sv._q("INSERT OR REPLACE INTO usuarios(nombre, rol, pin_hash, activo) VALUES (?,?,?,?)", (u["nombre"], u["rol"], u["pin_hash"], int(u["activo"])))


def _pin_en_uso(sv, pin, excepto=None):
    """¿Algún otro usuario activo tiene ese PIN? (se comprueban todos, sin cortar)."""
    return any([_coincide(pin, None, u["pin_hash"]) for u in _todos(sv) if u["activo"] and u["nombre"] != excepto])


def sembrar_usuarios(sv, env=None):
    """Importa PANEL_PIN* (usuarios Dueno / Personal) y REPARTIDORES del .env si ese nombre aún no está en la BD; nunca pisa la BD. Solo guarda el hash."""
    semillas = [(n, r, *env_panel(env).get(r, (None, None))) for n, r in (("Dueno", "dueno"), ("Personal", "personal"))]
    semillas += [(n, "repartidor", t, h) for n, (t, h) in env_repartidores(env).items()]
    with sv.pedidos_lock:
        existentes = {u["nombre"]: u for u in _todos(sv)}
        for nombre, rol, texto, hash_ in semillas:
            if not (texto or hash_):
                continue
            if nombre in existentes:
                if texto and not _coincide(texto, None, existentes[nombre]["pin_hash"]) or hash_ and hash_ != existentes[nombre]["pin_hash"]:
                    print(f"[panel] aviso: el PIN de {nombre!r} en .env no coincide con el de la BD; manda la BD (cámbielo desde Ajustes)")
                continue
            if texto and _pin_en_uso(sv, texto):
                print(f"[panel] usuario {nombre!r} no importado: PIN repetido")
                continue
            _poner(sv, {"nombre": nombre, "rol": rol, "pin_hash": hash_ or hash_pin(texto), "activo": True})


def guardar_usuario(sv, d, quien):
    """Crea o edita (por nombre) un usuario: {nombre, rol?, pin?, activo?}. PIN de 6 dígitos no trivial y único entre activos. Audita sin PIN."""
    nombre, rol, pin, activo = d.get("nombre"), d.get("rol"), d.get("pin"), d.get("activo")
    if not (isinstance(nombre, str) and NOMBRE.fullmatch(nombre)):
        raise ValueError("Nombre inválido: 1 a 30 letras sin tildes, números, - o _")
    if rol is not None and rol not in PANEL + REPARTO:
        raise ValueError("Rol inválido")
    if pin is not None and not (isinstance(pin, str) and pin.isascii() and pin.isdigit() and len(pin) == 6 and pin not in PIN_DEBILES):
        raise ValueError("El PIN debe tener 6 números y no ser trivial")
    if activo is not None and not isinstance(activo, bool):
        raise ValueError("Activo debe ser sí o no")
    email = d.get("email")  # correo de Google autorizado para entrar sin PIN ("" lo quita)
    if email is not None and not (isinstance(email, str) and (not email.strip() or seguridad.email_valido(email))):
        raise ValueError("Correo no válido")
    with sv.pedidos_lock:
        u = next((u for u in _todos(sv) if u["nombre"] == nombre), None)
        if not u and not (rol and pin):
            raise ValueError("Para crear un usuario hacen falta rol y PIN")
        if u and activo and not u["activo"] and not pin:
            raise ValueError("Para reactivar a un usuario ponga un PIN nuevo")
        if pin and _pin_en_uso(sv, pin, excepto=nombre):
            raise ValueError("Ese PIN ya lo usa otra persona")
        nuevo = {**(u or {"nombre": nombre, "activo": True}), **({"rol": rol} if rol else {}), **({"activo": activo} if activo is not None else {}),
                 **({"pin_hash": hash_pin(pin)} if pin else {})}
        if u and u["rol"] == "dueno" and u["activo"] and not (nuevo["rol"] == "dueno" and nuevo["activo"]) and \
                not any(x["rol"] == "dueno" and x["activo"] and x["nombre"] != nombre for x in _todos(sv)):
            raise ValueError("Debe quedar al menos un dueño activo")
        if email is not None:  # antes de guardar: si el correo es de otra persona no cambia nada
            seguridad.poner_email(sv, nombre, email)
        _poner(sv, nuevo)
    cambios = ["usuario_crear"] if not u else [a for a, si in (("usuario_pin", pin), ("usuario_rol", rol and rol != u["rol"]),
                                                               ("usuario_activo" if activo else "usuario_desactivar", activo is not None and activo != u["activo"])) if si]
    cambios += ["usuario_google"] if email is not None else []  # sin el correo
    for a in cambios:
        auditar(sv, quien, f"{a}:{nombre}")
    return {"nombre": nombre, "rol": nuevo["rol"], "activo": nuevo["activo"]}


def borrar_usuario(sv, nombre, quien):
    """Borra a una persona (y su aviso de ubicación aceptado). LookupError si no existe; debe quedar un dueño activo."""
    with sv.pedidos_lock:
        u = next((u for u in _todos(sv) if u["nombre"] == nombre), None)
        if not u:
            raise LookupError(nombre)
        if u["rol"] == "dueno" and u["activo"] and not any(x["rol"] == "dueno" and x["activo"] and x["nombre"] != nombre for x in _todos(sv)):
            raise ValueError("Debe quedar al menos un dueño activo")
        sv._q("DELETE FROM usuarios WHERE nombre=?", (nombre,))
        sv._q("DELETE FROM aviso_repartidor WHERE nombre=?", (nombre,))
        sv._q("DELETE FROM google WHERE nombre=?", (nombre,))
    auditar(sv, quien, f"usuario_borrar:{nombre}")
    return {"ok": True}


ID_PLATO = re.compile(r"[a-z0-9-]{1,40}")


def _id_de_nombre(sv, nombre):
    """Id libre a partir del nombre: "Arroz con pollo" -> arroz-con-pollo (y -2, -3… si ya existe)."""
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()).strip("-")[:36].strip("-") or "plato"
    iid, n = base, 2
    while iid in sv.menu:
        iid, n = f"{base}-{n}", n + 1
    return iid


def guardar_plato(sv, d, quien):
    """Crea o edita (por id) un plato: {id?, nombre?, precio?, categoria?, disponible?}. Sin id se crea uno nuevo con id sacado del nombre.
    Precio >= 0 en B/. sin fracción de centavo."""
    iid, nombre, precio, cat, disp, desc = d.get("id"), d.get("nombre"), d.get("precio"), d.get("categoria"), d.get("disponible"), d.get("descripcion")
    comp = d.get("complemento")  # el dueño lo marca para la venta sugerida cuando aún no hay historial
    if comp is not None and not isinstance(comp, bool):
        raise ValueError("Complemento debe ser sí o no")
    if desc is not None and not (isinstance(desc, str) and len(desc) <= 200):
        raise ValueError("Descripción demasiado larga (máx. 200)")
    if iid is not None and not (isinstance(iid, str) and ID_PLATO.fullmatch(iid)):
        raise ValueError("Id inválido: 1 a 40 minúsculas, números o -")
    if nombre is not None and not (isinstance(nombre, str) and 1 <= len(nombre.strip()) <= 80):
        raise ValueError("El nombre no puede estar vacío (máx. 80)")
    if cat is not None and not (isinstance(cat, str) and len(cat) <= 30):
        raise ValueError("Categoría demasiado larga (máx. 30)")
    if disp is not None and not isinstance(disp, bool):
        raise ValueError("Disponible debe ser sí o no")
    cambios = {k: v for k, v in (("nombre", nombre and nombre.strip()), ("categoria", cat), ("disponible", disp), ("descripcion", desc and " ".join(desc.split())),
                                 ("complemento", comp)) if v is not None}
    if precio is not None:
        if isinstance(precio, bool) or not isinstance(precio, (int, float, str)):
            raise ValueError("Precio inválido")
        cambios["precio_c"] = herramientas._c(precio)
        if not 0 <= cambios["precio_c"] <= 99_999:
            raise ValueError("El precio debe estar entre 0 y 999.99")
    with sv.pedidos_lock:
        p = sv.menu.get(iid) if iid else None
        if not p and not ("nombre" in cambios and "precio_c" in cambios):
            raise ValueError("Para crear un plato hacen falta nombre y precio")
        iid = iid or _id_de_nombre(sv, cambios["nombre"])
        sv.guardar_plato(iid, {**{k: v for k, v in (p or {"categoria": "", "disponible": True}).items() if k not in ("id", "precio")}, **cambios})
    auditar(sv, quien, f"plato_{'editar' if p else 'crear'}:{iid}")
    return {"ok": True, "plato": next(x for x in menu_json(sv) if x["id"] == iid)}


def borrar_plato(sv, quien, iid):
    """Quita un plato del menú (con su foto). Los pedidos ya hechos guardan su propio nombre y precio."""
    if not (isinstance(iid, str) and ID_PLATO.fullmatch(iid) and sv.borrar_plato(iid)):
        raise LookupError(iid)
    auditar(sv, quien, f"plato_borrar:{iid}")
    return {"ok": True}


def poner_disponible(sv, quien, iid, disponible):
    """Agotado / disponible con un toque: lo puede hacer cualquiera del panel (la cocina sabe qué se acabó)."""
    if not (isinstance(iid, str) and iid in sv.menu):
        raise LookupError(iid)
    if not isinstance(disponible, bool):
        raise ValueError("Disponible debe ser sí o no")
    return guardar_plato(sv, {"id": iid, "disponible": disponible}, quien)


def urls_foto(iid, v):
    """{foto, foto2x}: URL de 160 y 320 px (con versión para caché immutable) o None."""
    u = v and f"/menu/foto/{iid}.webp?v={v}"
    return {"foto": u or None, "foto2x": u and u + "&s=2" or None}


def menu_json(sv):
    """Menú para la web pública y el panel (precio en B/. como número; foto/foto2x URL o null)."""
    fotos = sv.fotos()
    return [{"id": i["id"], "nombre": i["nombre"], "precio": i["precio_c"] / 100, "categoria": i.get("categoria", ""),
             "disponible": i.get("disponible", True), **({"opciones": i["opciones"]} if "opciones" in i else {}),
             **({"descripcion": i["descripcion"]} if i.get("descripcion") else {}), **({"complemento": True} if i.get("complemento") else {}),
             **urls_foto(i["id"], fotos.get(i["id"]))} for i in sv.menu.values()]


MAX_FOTO = 3 * 1024 * 1024  # bytes de subida
MAX_PX = 40_000_000  # píxeles de la imagen de origen (anti decompression bomb)
TIPOS_FOTO = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class TipoNoValido(ValueError):
    """→ 415."""


def procesar_foto(datos):
    """(webp 160, webp 320) cuadrados y centrados, sin metadatos. El formato se detecta con Pillow por contenido.
    TipoNoValido si no es JPEG/PNG/WebP; ValueError si está corrupta o es demasiado grande en píxeles."""
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(datos), formats=list(TIPOS_FOTO.values()))
        if im.width * im.height > MAX_PX:
            raise ValueError("Imagen demasiado grande (máx. 40 Mpx)")
        im.draft("RGB", (640, 640))  # JPEG: decodifica ya reducida
        im = ImageOps.exif_transpose(im)  # gira según EXIF antes de descartarlo
        if im.mode in ("RGBA", "LA", "P", "PA"):  # transparencia → fondo blanco
            im = im.convert("RGBA")
            fondo = Image.new("RGB", im.size, "white")
            fondo.paste(im, mask=im.getchannel("A"))
            im = fondo
        im = ImageOps.fit(im.convert("RGB"), (320, 320), Image.Resampling.LANCZOS)
    except Image.UnidentifiedImageError:
        raise TipoNoValido("Solo se aceptan imágenes JPEG, PNG o WebP")
    except Exception:  # corrupta, bomba (DecompressionBombError/Warning) o > MAX_PX; Pillow lanza de todo
        raise ValueError("Imagen no válida o demasiado grande (máx. 40 Mpx)")

    def webp(img, tope):
        for q in (80, 65, 50, 35):
            b = io.BytesIO()
            Image.frombytes("RGB", img.size, img.tobytes()).save(b, "WEBP", quality=q, method=6)  # imagen nueva: sin EXIF/ICC/XMP
            if b.tell() <= tope:
                break
        return b.getvalue()
    return webp(im.resize((160, 160), Image.Resampling.LANCZOS), 12_000), webp(im, 40_000)


def guardar_foto(sv, quien, iid, datos):
    """Procesa y guarda la foto de un plato. LookupError si el plato no existe."""
    if not (isinstance(iid, str) and ID_PLATO.fullmatch(iid) and iid in sv.menu):
        raise LookupError(iid)
    p1, p2 = procesar_foto(datos)
    v = hashlib.sha256(p1 + p2).hexdigest()[:12]
    if not sv.poner_foto(iid, p1, p2, v):
        raise LookupError(iid)
    auditar(sv, quien, f"foto_subir:{iid}")
    return {"ok": True, **urls_foto(iid, v)}


def borrar_foto(sv, quien, iid):
    if not (isinstance(iid, str) and ID_PLATO.fullmatch(iid) and sv.poner_foto(iid)):
        raise LookupError(iid)
    auditar(sv, quien, f"foto_borrar:{iid}")
    return {"ok": True}


def lista_usuarios(sv):
    g = seguridad.emails(sv)
    return [{"nombre": u["nombre"], "rol": u["rol"], "activo": u["activo"], "email": g.get(u["nombre"])} for u in _todos(sv)]


class Bloqueo:
    """5 fallos de PIN desde una IP (IPv6: desde su /64, que es lo que tiene un solo abonado) → bloqueo de 5 min.
    Además, TOPE fallos entre TODAS las IP en VENTANA_S → cada intento espera `retraso()` s antes de comprobarse, y TOPE_HORA fallos
    entre todas en una hora → nadie entra (ni con el PIN bueno) hasta que bajen: frena el ataque repartido entre muchas IP.
    Las sesiones ya abiertas siguen; el dueño recibe UN aviso por episodio. `cerrojo` serializa el PBKDF2 (no se satura la CPU)."""
    TOPE, VENTANA_S, RETRASO_S = 20, 300, 2
    TOPE_HORA, HORA_S, MAX_CLAVES = 30, 3600, 1000

    def __init__(self):
        self.fallos, self.hasta, self.recientes = {}, {}, collections.deque(maxlen=self.TOPE)  # fallos: clave -> [n, último]
        self.hora, self.avisado = collections.deque(), False
        self.lock, self.cerrojo = threading.Lock(), threading.Lock()

    @staticmethod
    def _clave(ip):
        try:
            return str(ipaddress.ip_network(ip + "/64", strict=False).network_address) if ":" in ip else ip
        except ValueError:
            return ip

    def _limpiar(self, ahora):
        """Con `lock`. Olvida lo viejo: los diccionarios no crecen sin fin aunque el ataque use millones de IP."""
        while self.hora and self.hora[0] <= ahora - self.HORA_S:
            self.hora.popleft()
        if len(self.hora) < self.TOPE_HORA:
            self.avisado = False
        if len(self.fallos) + len(self.hasta) > self.MAX_CLAVES:
            self.hasta = {k: v for k, v in self.hasta.items() if v > ahora}
            self.fallos = {k: v for k, v in self.fallos.items() if v[1] > ahora - self.HORA_S}
            if len(self.fallos) > self.MAX_CLAVES:  # ponytail: aun así lleno (ataque masivo): se vacía; el tope global sigue frenando
                self.fallos.clear()

    def saturado(self):
        """¿Se pasó el tope global de fallos de la última hora?"""
        with self.lock:
            self._limpiar(time.time())
            return len(self.hora) >= self.TOPE_HORA

    def bloqueada(self, ip):
        return self.saturado() or self.hasta.get(self._clave(ip), 0) > time.time()

    def retraso(self):
        """Segundos que debe esperar un intento ahora (0 si no hay ataque)."""
        r = self.recientes
        return self.RETRASO_S if len(r) == self.TOPE and r[0] > time.time() - self.VENTANA_S else 0

    def fallo(self, ip):
        """Anota un fallo. True solo la primera vez que se pasa el tope global (para avisar al dueño una vez)."""
        ip, ahora = self._clave(ip), time.time()
        with self.lock:
            self._limpiar(ahora)
            self.recientes.append(ahora)
            self.hora.append(ahora)
            n = self.fallos.get(ip, [0, 0])[0] + 1
            self.fallos[ip] = [n, ahora]
            if n >= 5:
                self.fallos.pop(ip)
                self.hasta[ip] = ahora + 300
            if len(self.hora) >= self.TOPE_HORA and not self.avisado:
                self.avisado = True
                return True
            return False

    def acierto(self, ip):
        with self.lock:
            self.fallos.pop(self._clave(ip), None)


def acciones_de(p):
    """Acciones válidas ahora (mismas reglas que marcar_pedido / cancelar_pedido)."""
    a = [e for e, previos in herramientas.PREVIOS.items() if p["estado"] in previos and not (e == "en_camino" and p.get("mesa"))]
    if p["estado"] == "pago_reportado":
        a.append("pago_rechazado")
    if p["estado"] in ("por_confirmar", "confirmado"):
        a.append("cancelado")
    return a


def _mask(tel, con_prefijo=False):
    """Panamá: 6***-**12 (con_prefijo: +507 6***-**12, para logs). Otros: +prefijo, primer dígito, * y 2 últimos. Sin teléfono (anonimizado): —."""
    t = "".join(c for c in str(tel or "") if c.isdigit())
    if not t:
        return "—"
    if len(t) <= 8:  # ponytail: se asume parte nacional de 8 dígitos para separar el prefijo de país
        return t[:1] + "***-**" + t[-2:] if len(t) > 4 else "***"
    pref, nac = t[:-8], t[-8:]
    if pref == "507":
        return ("+507 " if con_prefijo else "") + nac[0] + "***-**" + nac[-2:]
    return f"+{pref} {nac[0]}{'*' * 5}{nac[-2:]}"


def pedido_json(p, detalle=False):
    """Lista: teléfono enmascarado, solo zona y sin imagen. Detalle: teléfono y dirección completos solo en pedidos abiertos."""
    cerrado = p["estado"] in TERMINADOS
    mesa = p.get("mesa")  # pedido de mesa: su "teléfono" es un cid interno, no se muestra
    d = {"id": p["id"], "estado": p["estado"], "nombre": p.get("nombre"),
         "telefono": None if mesa else herramientas.tel_de(p) if detalle and not cerrado else _mask(herramientas.tel_de(p)),
         "modalidad": p.get("modalidad"), "zona": p.get("zona"), "hora_deseada": p.get("hora_deseada"), "notas": p.get("notas"),
         "items": [{"id": l["id"], "nombre": l["nombre"], "cantidad": l["cantidad"], "precio": dinero(l["precio_c"]), **({"nota": l["nota"]} if l.get("nota") else {})} for l in p["lineas"]],
         "subtotal": dinero(p["subtotal_c"]), "envio": dinero(p["envio_c"]), "total": dinero(p["total_c"]),
         "metodo": p.get("metodo"), "referencia": p.get("referencia"), "comprobante": bool(p.get("comprobante")),
         "creado_at": p.get("creado_at"), "estado_at": p.get("estado_at") or p.get("creado_at"), "acciones": acciones_de(p),
         "repartidor": p.get("repartidor"), "tiene_ubicacion": bool(p.get("ubicacion")), "por_cobrar": bool(p.get("por_cobrar")), "cobrado_por": p.get("cobrado_por"),
         "fuera_de_zona": bool((p.get("ubicacion") or {}).get("fuera_de_zona"))}
    if mesa:  # solo en pedidos de mesa: la API de los demás no cambia
        d.update(mesa=mesa, espera_personal=bool(p.get("espera_personal")))
    if detalle and not cerrado:
        d["direccion"] = p.get("direccion")
        d["mapa"] = ubicacion.url_mapa(p)
    return d


def estado_panel(sv, desde=0, repartidores=(), rutas=None):
    """Lo que el panel sondea cada 3 s: pedidos (activos + cerrados de hoy), avisos nuevos (id > desde), escalados abiertos."""
    herramientas.caducar_pedidos(sv)
    hoy = sv.ahora().date().isoformat()
    peds = [p for p in sv.listar("pedido") if p["estado"] not in TERMINADOS or (p.get("estado_at") or "").startswith(hoy)]
    espera = {p["id"] for p in peds if p["estado"] in PAGO_PENDIENTE}
    todos = sv.listar("aviso")
    avisos = [{"id": a["id"], "tipo": "pago" if a.get("pedido_id") in espera else "pedido", "texto": a["texto"], "pedido_id": a.get("pedido_id")}
              for a in reversed(todos) if a["id"] > desde]  # solo los nuevos: reenviar los pendientes en cada sondeo repetía el aviso (y el sonido) cada 3 s
    abiertos = [e for e in sv.listar("escalado") if e.get("estado") == "abierto"]
    # quien pide una persona espera que lo llamen: el panel (con PIN) ve su teléfono completo mientras el aviso está abierto; atendido, deja de salir
    nombres = {r["telefono"]: r["nombre"] for t in ("consentimiento", "pedido") for r in sv.listar(t) if r.get("telefono") and r.get("nombre")} if abiertos else {}
    esc = [{"id": e["id"], "nombre": nombres.get(e.get("telefono")) or (f"Mesa {mesa.de_cid(e.get('telefono'))}" if mesa.de_cid(e.get("telefono")) else None),
            "telefono": None if mesa.de_cid(e.get("telefono")) else herramientas.contacto_de(sv, e.get("telefono")), "motivo": e.get("motivo"), "pedido_id": None} for e in abiertos]
    return {"pedidos": [pedido_json(p) for p in sorted(peds, key=lambda p: p["id"])], "avisos": avisos,
            "ultimo": max([a["id"] for a in todos] + [0]), "escalados": esc, "repartidores": sorted(repartidores), "cerrado": herramientas.cerrado_ahora(sv), "cerrado_hasta": sv.cfg.get("cerrado_hasta") if sv.cfg.get("cerrado") else None, "modulos": modulos(sv),
            "rutas": rutas or {}}  # repartidor -> ruta (motor/ruta.py); la calcula web.py, que es quien tiene las posiciones


def marcar(sv, pedido_id, estado, rol):
    """pagado | pago_rechazado | en_preparacion | listo | entregado | cancelado. ValueError legible si no procede. Anota quién y cuándo."""
    previo = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if estado in ("cancelado", "cancelar"):
        herramientas.cancelar_pedido(sv, pedido_id)
        estado = "cancelado"
    else:
        herramientas.marcar_pedido(sv, pedido_id, estado)
    ahora = sv.ahora().isoformat()
    cambios = {f"{estado}_por": rol, f"{estado}_at": ahora, "estado_at": ahora, **({"espera_personal": False} if estado == "pagado" else {})}
    if estado == "entregado" and previo and previo.get("por_cobrar"):  # efectivo contra entrega: queda anotado quién cobró y cuánto
        cambios.update(por_cobrar=False, cobrado_por=rol, cobrado_at=ahora, cobrado_c=previo["total_c"])
        herramientas._aviso(sv, previo, pedido_id, f"{rol} cobró {dinero(previo['total_c'])} en efectivo del pedido #{pedido_id}.")
    return sv.actualizar(pedido_id, cambios, tipo="pedido")


ASIGNABLES = {"pagado", "en_preparacion", "listo"}


def asignar(sv, pedido_id, repartidor, validos):
    """Asigna (o quita con "") un repartidor a una entrega pagada y no entregada. ValueError legible si no procede."""
    if repartidor and repartidor not in validos:
        raise ValueError("Repartidor desconocido")
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if not p or p["modalidad"] != "entrega":
        raise ValueError("Solo se asigna repartidor a pedidos de entrega")
    return sv.actualizar(pedido_id, {"repartidor": repartidor or None}, tipo="pedido", desde=ASIGNABLES)


# --- repartidor: solo ve lo suyo y solo mientras está abierto (sin historial) ---
EN_RUTA = {"en_preparacion", "listo", "en_camino"}
SIGUIENTE = {"listo": "en_camino", "en_camino": "entregado"}


def pedido_repartidor(p, ruta=None):
    """`parada`: su número en la ruta del repartidor (1 = la siguiente); None si aún no es parada (en preparación)."""
    return {"id": p["id"], "estado": p["estado"], "nombre": p.get("nombre"), "telefono": herramientas.tel_de(p), "direccion": p.get("direccion"), "zona": p.get("zona"),
            "notas": p.get("notas"), "hora_deseada": p.get("hora_deseada"),
            "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], **({"nota": l["nota"]} if l.get("nota") else {})} for l in p["lineas"]],
            "total": dinero(p["total_c"]), "pagado": not p.get("por_cobrar"),  # el pago va antes de preparar, salvo efectivo contra entrega: ahí cobra el repartidor
            "cobrar": dinero(p["total_c"]) if p.get("por_cobrar") else None,
            "mapa": ubicacion.url_mapa(p), "tiene_ubicacion": bool(p.get("ubicacion")), "fuera_de_zona": bool((p.get("ubicacion") or {}).get("fuera_de_zona")),
            "parada": (reparto.parada(ruta, p["id"]) or {}).get("n"),
            "acciones": [SIGUIENTE[p["estado"]]] if p["estado"] in SIGUIENTE else []}


def pedidos_repartidor(sv, nombre, ruta=None):
    herramientas.caducar_pedidos(sv)
    return [pedido_repartidor(p, ruta) for p in sv.listar("pedido") if p.get("repartidor") == nombre and p["estado"] in EN_RUTA]


def marcar_repartidor(sv, nombre, pedido_id, estado, cobrado=False):
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if not p or p.get("repartidor") != nombre or p["estado"] not in EN_RUTA:
        raise ValueError("No tiene ese pedido asignado")
    if SIGUIENTE.get(p["estado"]) != estado:
        raise ValueError("Ese cambio no corresponde al estado actual del pedido")
    if estado == "entregado" and p.get("por_cobrar") and cobrado is not True:
        raise ValueError(f"Cobra {dinero(p['total_c'])} en efectivo y confirma que ya lo cobraste.")
    return marcar(sv, pedido_id, estado, nombre)


def atender_escalado(sv, esc_id):
    sv.actualizar(esc_id, {"estado": "atendido"}, tipo="escalado", desde={"abierto"})


def comprobante(sv, pedido_id):
    """(bytes, content-type) solo mientras el pago está pendiente y el archivo existe; si no, None."""
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    img = p and p["estado"] in PAGO_PENDIENTE and herramientas.leer_comprobante(sv, p)
    return (img[0], CT_IMAGEN[img[1]]) if img else None


def auditar(sv, usuario, accion, pedido_id=None):
    """Tabla auditoria (usuario=nombre de la persona, accion, pedido_id, at): sin datos de clientes ni IP."""
    sv.insertar("auditoria", {"usuario": usuario, "accion": accion, "pedido_id": pedido_id, "at": sv.ahora().isoformat()})


HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def ajustes_json(sv):
    """Configuración general que el dueño ve y cambia en el panel."""
    c = sv.cfg
    pago = c.get("pago") or {}
    return {"marca": c.get("marca") or c.get("nombre") or "", "telefono": c.get("telefono") or "", "anuncio": c.get("anuncio") or "", "cerrado": herramientas.cerrado_ahora(sv), "cerrado_hasta": c.get("cerrado_hasta") if c.get("cerrado") else None,
            "horario": herramientas.horario_por_dia(c.get("horario")), "excepciones": excepciones_futuras(sv), "pago": {**{k: pago.get(k) or "" for k in ("yappy", "transferencia")}, "efectivo": herramientas.efectivo_ok(sv)},
            "sugerencias": bool(c.get("sugerencias", True)), "envio": envio_zonas(c), "factura": {k: str(c.get(k) or "") for k in ("razon_social", "ruc", "dv", "sucursal")}}


def envio_zonas(c):
    """Zonas de entrega para el panel: [{nombre, precio, incluye: [barrios]}] (de envio.zonas + envio.alias del servicio.yaml o los ajustes)."""
    e = (c.get("pedidos") or {}).get("envio") or {}
    z = e.get("zonas") or {}
    z = {n: e.get("costo", 0) for n in z} if isinstance(z, list) else z
    return [{"nombre": n, "precio": float(v), "incluye": list((e.get("alias") or {}).get(n) or [])} for n, v in z.items()]


def _zonas_validas(lista):
    """Valida lo que manda el panel y devuelve (zonas {nombre: precio}, alias {nombre: [barrios]})."""
    if not (isinstance(lista, list) and len(lista) <= 30):
        raise ValueError("Zonas de envío: máximo 30")
    zonas, alias, vistos = {}, {}, set()
    for z in lista:
        n, v, inc = (z.get("nombre"), z.get("precio"), z.get("incluye") or []) if isinstance(z, dict) else (None, None, None)
        if not (isinstance(n, str) and 1 <= len(n.strip()) <= 40):
            raise ValueError("Cada zona necesita un nombre (hasta 40 letras)")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
            raise ValueError(f"Precio de envío de {n.strip()}: un número entre 0 y 100")
        if not (isinstance(inc, list) and len(inc) <= 30 and all(isinstance(x, str) and 1 <= len(x.strip()) <= 40 for x in inc)):
            raise ValueError(f"Lugares de {n.strip()}: hasta 30 nombres de 40 letras")
        n, nombres = " ".join(n.split()), [" ".join(x.split()) for x in inc]
        for x in [n, *nombres]:
            k = herramientas._sin_tildes(x)
            if k in vistos:
                raise ValueError(f"«{x}» está repetido: cada lugar va en una sola zona")
            vistos.add(k)
        zonas[n] = round(float(v), 2)
        if nombres:
            alias[n] = nombres
    return zonas, alias


def excepciones_futuras(sv):
    """Días especiales de hoy en adelante: {AAAA-MM-DD: [] (cerrado) o [[desde, hasta], ...]}."""
    hoy = f"{sv.ahora():%Y-%m-%d}"
    return {k: v for k, v in sorted(((sv.cfg.get("horario") or {}).get("excepciones") or {}).items()) if k >= hoy}


def _excepciones_validas(sv, ex):
    if not (isinstance(ex, dict) and len(ex) <= 60):
        raise ValueError("Días especiales: máximo 60")
    hoy, out = sv.ahora().date(), {}
    for fecha, turnos in sorted(ex.items()):
        try:
            f = dt.date.fromisoformat(fecha) if isinstance(fecha, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", fecha) else None
        except ValueError:
            f = None
        if f is None:
            raise ValueError(f"Día especial «{fecha}»: fecha inválida")
        if f < hoy:
            raise ValueError(f"Día especial {fecha}: esa fecha ya pasó")
        out[fecha] = _turnos_validos(turnos, f"Día especial {fecha}")
    return out


def _turnos_validos(turnos, nombre):
    turnos = turnos or []
    if not (isinstance(turnos, list) and len(turnos) <= 3 and all(isinstance(t, list) and len(t) == 2 and all(isinstance(x, str) and HHMM.fullmatch(x) for x in t) for t in turnos)):
        raise ValueError(f"Horario del {nombre} inválido: horas como 11:00 (hasta 3 turnos)")
    turnos = sorted(turnos)
    if any(a >= b for a, b in turnos) or any(turnos[i][1] > turnos[i + 1][0] for i in range(len(turnos) - 1)):
        raise ValueError(f"Horario del {nombre}: cada turno debe abrir antes de cerrar y no pisar al otro")
    return turnos


def poner_cerrado(sv, quien, cerrado, hasta=None):
    """Cerrar o abrir la tienda a mano (cualquiera del panel): cerrada no entran pedidos nuevos aunque el horario diga abierto.
    `hasta` (AAAA-MM-DD HH:MM, hora local, futura y a menos de 14 días) = se reabre sola a esa hora."""
    if not isinstance(cerrado, bool):
        raise ValueError("Cerrado debe ser sí o no")
    if hasta is not None:
        if not cerrado:
            raise ValueError("La hora de reapertura solo va al cerrar")
        t = herramientas._hora(hasta, sv.tz)
        ahora = sv.ahora()
        if not ahora < t <= ahora + dt.timedelta(days=14):
            raise ValueError("La hora de reapertura debe ser futura y dentro de 14 días")
        hasta = f"{t:{herramientas.FMT}}"
    sv.poner_ajuste("cerrado", cerrado)
    sv.poner_ajuste("cerrado_hasta", hasta)
    auditar(sv, quien, ("tienda_cerrar" + (f":hasta {hasta}" if hasta else "")) if cerrado else "tienda_abrir")
    return {"ok": True, "cerrado": cerrado, "cerrado_hasta": hasta}


def guardar_ajustes(sv, d, quien):
    """Guarda lo que venga de {marca, telefono, anuncio, cerrado, horario: {dia: [[desde, hasta], ...]}, pago: {yappy, transferencia}}. ValueError legible si algo no vale."""
    def texto(k, maximo, minimo=0):
        v = d[k]
        if not (isinstance(v, str) and minimo <= len(v.strip()) <= maximo):
            raise ValueError(f"'{k}' no es válido (de {minimo} a {maximo} letras)")
        return " ".join(v.split())
    nuevo = {}
    if "marca" in d:
        nuevo["marca"] = texto("marca", 40, 1)
    if "anuncio" in d:
        nuevo["anuncio"] = texto("anuncio", 160)
    if "telefono" in d:
        t = texto("telefono", 20)
        if t and not (re.fullmatch(r"\+?[\d \-]+", t) and 7 <= sum(c.isdigit() for c in t) <= 15):
            raise ValueError("El teléfono del restaurante debe tener de 7 a 15 números")
        nuevo["telefono"] = t
    if "factura" in d:  # datos del emisor para el resumen para factura (motor/factura.py)
        f = d["factura"]
        if not (isinstance(f, dict) and set(f) <= {"razon_social", "ruc", "dv", "sucursal"} and all(isinstance(v, str) for v in f.values())):
            raise ValueError("Datos de factura inválidos")
        for k, maximo, patron in (("razon_social", 80, r"[^\x00-\x1f<>]*"), ("ruc", 20, r"[A-Za-z0-9-]*"), ("dv", 3, r"\d*"), ("sucursal", 20, r"[A-Za-z0-9 -]*")):
            if k in f:
                v = " ".join(f[k].split())
                if len(v) > maximo or not re.fullmatch(patron, v):
                    raise ValueError(f"'{k}' no es válido (hasta {maximo} caracteres; RUC y DV solo letras, números y guiones)")
                nuevo[k] = v
    if "horario" in d:
        h = d["horario"]
        if not (isinstance(h, dict) and set(h) <= set(herramientas.DIAS)):
            raise ValueError("Horario inválido")
        por_dia = {}
        for dia in herramientas.DIAS:
            por_dia[dia] = _turnos_validos(h.get(dia), dia)
        viejo = {k: v for k, v in (sv.cfg.get("horario") or {}).items() if k not in ("dias", "turnos", "desde", "hasta", "por_dia", "excepciones")}
        nuevo["horario"] = {**viejo, "dias": [x for x in herramientas.DIAS if por_dia[x]], "por_dia": por_dia}
    if "excepciones" in d:  # días especiales; si no vienen se conservan los guardados
        nuevo.setdefault("horario", {k: v for k, v in (sv.cfg.get("horario") or {}).items() if k != "excepciones"})["excepciones"] = _excepciones_validas(sv, d["excepciones"])
    elif "horario" in nuevo and (sv.cfg.get("horario") or {}).get("excepciones"):
        nuevo["horario"]["excepciones"] = (sv.cfg["horario"])["excepciones"]
    if "pago" in d:
        p = d["pago"]
        if not (isinstance(p, dict) and set(p) <= {"yappy", "transferencia", "efectivo"}
                and all(isinstance(v, bool) if k == "efectivo" else isinstance(v, str) and len(v) <= 160 for k, v in p.items())):
            raise ValueError("Datos de pago inválidos (máx. 160 letras cada uno)")
        nuevo["pago"] = {**(sv.cfg.get("pago") or {}), **{k: v if k == "efectivo" else " ".join(v.split()) or None for k, v in p.items()}}
    if "envio" in d:  # zonas de entrega con su precio; vacío = vuelve al costo fijo del servicio.yaml
        zonas, alias = _zonas_validas(d["envio"])
        ped = dict(sv.cfg.get("pedidos") or {})
        ped["envio"] = {**{k: v for k, v in (ped.get("envio") or {}).items() if k not in ("zonas", "alias")}, **({"zonas": zonas, "alias": alias} if zonas else {})}
        nuevo["pedidos"] = ped
    for k, nombre in (("cerrado", "Cerrado"), ("sugerencias", "Sugerencias")):  # sugerencias: venta sugerida en bot y web
        if k in d:
            if not isinstance(d[k], bool):
                raise ValueError(f"{nombre} debe ser sí o no")
            nuevo[k] = d[k]
            if k == "cerrado":
                nuevo["cerrado_hasta"] = None  # abrir o cerrar a mano anula una reapertura programada
    for k, v in nuevo.items():
        sv.poner_ajuste(k, v)
    if nuevo:
        auditar(sv, quien, "ajustes:" + ",".join(sorted(nuevo)))
    return {"ok": True, "ajustes": ajustes_json(sv)}


def modulos(sv):
    """Extras que el restaurante contrató. Manda el operador (`modulos` de su entrada en clientes.yaml, que `alojar` deja en
    `sv.modulos_operador` y cambia en caliente); si no dice nada, `modulos: [estadisticas]` del servicio.yaml. Nunca desde el panel:
    se lee el yaml (`_cfg`), no la tabla ajustes que escribe el dueño."""
    op = getattr(sv, "modulos_operador", None)
    return [m for m in ((sv._cfg.get("modulos") or []) if op is None else op) if m in clientes.MODULOS]


def comision_ref(sv):
    """% que cobraría una app de delivery (servicio.yaml `comision_ref`, por defecto 25): solo para estimar el ahorro del canal propio."""
    v = sv.cfg.get("comision_ref", 25)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 60 else 25


def estadisticas(sv, dias=30):
    """Módulo extra «estadisticas»: ventas y comportamiento de los últimos 7, 30 o 90 días contra el periodo anterior. Sin teléfonos ni nombres."""
    if "estadisticas" not in modulos(sv):
        raise LookupError("estadisticas")
    dias = dias if dias in (7, 30, 90) else 30
    hoy = sv.ahora().date()
    ini = hoy - dt.timedelta(days=dias - 1)
    ant = ini - dt.timedelta(days=dias)
    dia = lambda p: (p.get("estado_at") or p.get("creado_at") or "")[:10]
    todos = sv.listar("pedido")
    entregados = [p for p in todos if p["estado"] == "entregado"]
    cur = [p for p in entregados if ini.isoformat() <= dia(p) <= hoy.isoformat()]
    prev = [p for p in entregados if ant.isoformat() <= dia(p) < ini.isoformat()]
    perdidos = [p for p in todos if p["estado"] in ("cancelado", "caducado") and ini.isoformat() <= dia(p) <= hoy.isoformat()]
    suma = lambda ps: sum(p["total_c"] for p in ps)
    ventas, ventas_ant = suma(cur), suma(prev)
    por_dia = collections.OrderedDict(((ini + dt.timedelta(days=i)).isoformat(), [0, 0]) for i in range(dias))
    for p in cur:
        por_dia[dia(p)][0] += p["total_c"]; por_dia[dia(p)][1] += 1
    horas = [0] * 24
    for p in cur:
        try: horas[int((p.get("creado_at") or "")[11:13])] += 1
        except ValueError: pass
    platos = collections.defaultdict(lambda: [0, 0])
    for p in cur:
        for l in p["lineas"]:
            platos[l["nombre"]][0] += l["cantidad"]; platos[l["nombre"]][1] += l["precio_c"] * l["cantidad"]
    minutos = []
    for p in cur:
        try: minutos.append((dt.datetime.fromisoformat(p["estado_at"]) - dt.datetime.fromisoformat(p["creado_at"])).total_seconds() / 60)
        except (KeyError, ValueError, TypeError): pass
    veces = collections.Counter(herramientas.tel_de(p) or p.get("telefono") for p in entregados if p.get("telefono"))  # por teléfono: la web da una sesión nueva por navegador
    clientes = {herramientas.tel_de(p) or p.get("telefono") for p in cur if p.get("telefono")}
    cuenta = lambda clave: dict(collections.Counter(p.get(clave) or "—" for p in cur))
    zonas = collections.Counter(p.get("zona") for p in cur if p.get("modalidad") == "entrega" and p.get("zona"))
    cambio = lambda a, b: None if not b else round((a - b) * 100 / b, 1)
    ofrecidas = [p for p in cur if p.get("sugerido")]
    sumadas = [[l for l in p["lineas"] if l["id"] == p["sugerido"]["id"]] for p in ofrecidas]
    extra = sum(l["precio_c"] * l["cantidad"] for ls in sumadas for l in ls)
    return {"dias": dias, "desde": ini.isoformat(), "ventas": dinero(ventas), "ventas_cambio": cambio(ventas, ventas_ant),
            "comision": {"pct": (pct := comision_ref(sv)), "ahorro": dinero(round(ventas * pct / 100))},
            "sugerencias": {"ofrecidas": len(ofrecidas), "aceptadas": sum(1 for ls in sumadas if ls), "extra": dinero(extra),
                            "pct_ticket": round(extra * 100 / (ventas - extra), 1) if ventas > extra else None},  # cuánto subió la venta por lo sugerido
            "pedidos": len(cur), "pedidos_cambio": cambio(len(cur), len(prev)),
            "ticket": dinero(ventas // len(cur)) if cur else dinero(0), "perdidos": len(perdidos),
            "minutos_medio": round(sum(minutos) / len(minutos)) if minutos else None,
            "clientes": len(clientes), "clientes_repiten": sum(1 for t in clientes if veces[t] > 1),
            "por_dia": [{"fecha": f, "ventas": dinero(v[0]), "centavos": v[0], "pedidos": v[1]} for f, v in por_dia.items()],
            "por_hora": horas, "modalidad": cuenta("modalidad"), "metodo": cuenta("metodo"),
            "zonas": [{"zona": z, "pedidos": n} for z, n in zonas.most_common(5)],
            "platos": [{"nombre": n, "cantidad": v[0], "ingresos": dinero(v[1])} for n, v in sorted(platos.items(), key=lambda x: (-x[1][0], x[0]))[:8]]}


# --- «¿Cuánto vendí el viernes?»: el dueño pregunta en lenguaje natural; el modelo solo tiene estas consultas FIJAS de solo lectura ---
# Devuelven agregados de pedidos entregados (nunca teléfonos, nombres ni direcciones) y solo aceptan {desde, hasta} validados.
FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MAX_PREGUNTA, MAX_RANGO, VUELTAS_PREGUNTA = 300, 366, 5


def _fecha(v):
    try:
        if isinstance(v, str) and FECHA.fullmatch(v):
            return dt.date.fromisoformat(v)
    except ValueError:
        pass
    raise ValueError("Fecha inválida: usa AAAA-MM-DD")


def _entregados(sv, desde, hasta):
    a, b = desde.isoformat(), hasta.isoformat()
    return [p for p in sv.listar("pedido") if p["estado"] == "entregado" and a <= (p.get("estado_at") or p.get("creado_at") or "")[:10] <= b]


def _resumen(ps):
    v = sum(p["total_c"] for p in ps)
    return {"ventas": dinero(v), "pedidos": len(ps), "ticket_medio": dinero(v // len(ps)) if ps else dinero(0)}


def _q_dias(sv, ps, desde, hasta):
    n = (hasta - desde).days + 1
    pa, pb = desde - dt.timedelta(days=n), desde - dt.timedelta(days=1)
    dias = {(desde + dt.timedelta(days=i)).isoformat(): [] for i in range(n)}
    for p in ps:
        dias[(p.get("estado_at") or p["creado_at"])[:10]].append(p)
    ant = _entregados(sv, pa, pb)
    va, vb = sum(p["total_c"] for p in ps), sum(p["total_c"] for p in ant)
    return {"total": _resumen(ps), "por_dia": [{"fecha": f, "dia": DIAS_ES[dt.date.fromisoformat(f).weekday()], "ventas": dinero(sum(p["total_c"] for p in x)), "pedidos": len(x)}
                                               for f, x in dias.items()],
            "periodo_anterior": {"desde": pa.isoformat(), "hasta": pb.isoformat(), **_resumen(ant)}, "cambio_pct": round((va - vb) * 100 / vb, 1) if vb else None}


def _q_platos(sv, ps, desde, hasta):
    platos = collections.defaultdict(lambda: [0, 0])
    for p in ps:
        for l in p["lineas"]:
            platos[l["nombre"]][0] += l["cantidad"]; platos[l["nombre"]][1] += l["precio_c"] * l["cantidad"]
    return {"platos": [{"plato": n, "cantidad": c, "ingresos": dinero(i)} for n, (c, i) in sorted(platos.items(), key=lambda x: (-x[1][0], x[0]))[:10]]}


def _q_horas(sv, ps, desde, hasta):
    horas = collections.defaultdict(list)
    for p in ps:
        if (p.get("creado_at") or "")[11:13].isdigit():
            horas[int(p["creado_at"][11:13])].append(p)
    return {"por_hora": [{"hora": f"{h:02d}:00", **_resumen(horas[h])} for h in sorted(horas)]}


def _q_modalidad(sv, ps, desde, hasta):
    return {"modalidad": {m: _resumen([p for p in ps if p.get("modalidad") == m]) for m in ("entrega", "recogida")}}


CONSULTAS = {  # nombre -> (función, descripción para el modelo)
    "ventas_por_dia": (_q_dias, "Ventas y pedidos entregados de cada día del rango, el total y el periodo anterior de la misma duración con el cambio en %."),
    "platos_mas_vendidos": (_q_platos, "Los 10 platos más vendidos del rango: cantidad e ingresos."),
    "ventas_por_hora": (_q_horas, "Pedidos y ventas por hora del día (franja horaria en la que se pidió) en el rango."),
    "ventas_por_modalidad": (_q_modalidad, "Ventas y pedidos a domicilio frente a para recoger en el rango."),
}
RANGO = {"type": "object", "properties": {"desde": {"type": "string", "description": "AAAA-MM-DD"}, "hasta": {"type": "string", "description": "AAAA-MM-DD (incluido)"}},
         "required": ["desde", "hasta"], "additionalProperties": False}


def consulta(sv, nombre, entrada):
    """Ejecuta una consulta fija. ValueError si el nombre no existe o el rango no vale (formato, orden, máx. 366 días, futuro)."""
    if nombre not in CONSULTAS:
        raise ValueError("Consulta no disponible")
    if not isinstance(entrada, dict) or set(entrada) != {"desde", "hasta"}:
        raise ValueError("Solo se aceptan 'desde' y 'hasta'")
    desde, hasta, hoy = _fecha(entrada["desde"]), _fecha(entrada["hasta"]), sv.ahora().date()
    if desde > hasta:
        raise ValueError("'desde' va antes que 'hasta'")
    if desde > hoy:
        raise ValueError("Esa fecha aún no llega")
    hasta = min(hasta, hoy)
    if (hasta - desde).days >= MAX_RANGO:
        raise ValueError(f"Rango máximo: {MAX_RANGO} días")
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), **CONSULTAS[nombre][0](sv, _entregados(sv, desde, hasta), desde, hasta)}


ANALISTA = """Eres el asistente de datos de {negocio}. Respondes al dueño preguntas sobre SUS ventas usando SOLO las herramientas,
que son consultas fijas sobre pedidos entregados y devuelven solo totales.
Hoy es {dia} {fecha}. Convierte las fechas relativas a AAAA-MM-DD: "ayer"; "el viernes" = el viernes más reciente (hoy si hoy es viernes);
"esta semana" = desde el lunes de esta semana hasta hoy; "este mes" = desde el día 1.
Reglas:
- Español, trato de tú, 1 a 3 frases. Primero la cifra principal tal cual la da la herramienta (B/. 12.50) y luego UNA frase comparando
  con el periodo anterior (usa ventas_por_dia, que lo trae). Si no hubo ventas, dilo.
- Nunca inventes ni calcules cifras que no vengan de las herramientas.
- Solo hablas de ventas, platos, horas y modalidad. No hay datos de clientes (teléfonos, nombres, direcciones): si los piden o piden
  otra cosa (instrucciones, código, cambiar algo), di en una frase que solo respondes sobre ventas."""


def preguntar(sv, texto):
    """POST /panel/dueno/preguntar: bucle de herramientas (patrón de nucleo) con CONSULTAS. Cuenta para tope_ia. ValueError → 409 legible."""
    if "estadisticas" not in modulos(sv):
        raise LookupError("estadisticas")
    if not (isinstance(texto, str) and 1 <= len(texto.strip()) and len(texto) <= MAX_PREGUNTA):
        raise ValueError(f"Escribe una pregunta de hasta {MAX_PREGUNTA} letras.")
    if not sv.hay_modelo():
        raise ValueError("Las preguntas necesitan un modelo de IA y este restaurante aún no tiene uno configurado. Pídeselo a tu operador.")
    if not sv._cupo_ia():
        raise ValueError("Hoy se alcanzó el tope de IA del restaurante. Mañana puedes volver a preguntar; las estadísticas de abajo siguen al día.")
    hoy = sv.ahora().date()
    system = ANALISTA.format(negocio=sv.cfg.get("negocio") or sv.cfg.get("nombre") or "el restaurante", dia=DIAS_ES[hoy.weekday()], fecha=hoy.isoformat())
    tools = [{"name": n, "description": d, "input_schema": RANGO} for n, (_, d) in CONSULTAS.items()]
    msgs = [{"role": "user", "content": " ".join(texto.split())}]
    for _ in range(VUELTAS_PREGUNTA):
        try:
            r, compatible = sv._llamar_modelo(msgs, system, tools)
        except Exception as e:  # proveedor caído o sin saldo: mensaje claro, sin detalles internos
            print(f"[panel] preguntar: fallo del modelo ({type(e).__name__})")
            raise ValueError("El asistente no responde ahora mismo. Inténtalo en unos minutos.")
        msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content if not (compatible and b.type in ("thinking", "redacted_thinking"))]})
        if r.stop_reason == "pause_turn":
            continue
        if r.stop_reason != "tool_use":
            t = "\n".join(b.text for b in r.content if b.type == "text").strip()
            return {"respuesta": t if r.stop_reason != "refusal" and t else "No pude responder eso. Prueba preguntando por ventas, platos, horas o modalidad."}
        resultados = []
        for b in r.content:
            if b.type == "tool_use":
                try:
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(consulta(sv, b.name, b.input), ensure_ascii=False)})
                except ValueError as e:
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}", "is_error": True})
        msgs.append({"role": "user", "content": resultados})
    return {"respuesta": "No pude resolverlo. Prueba con una pregunta más concreta, por ejemplo «¿Cuánto vendí ayer?»."}
def api_qr(sv, quien, metodo, ruta, d):
    """Módulo «mesas» (solo dueño; 404 si el restaurante no lo tiene). `d["_base"]` (https://dominio) lo pone el servidor, nunca el cliente."""
    if "mesas" not in modulos(sv):
        raise LookupError(ruta)
    base, entero = d.get("_base", ""), lambda v: isinstance(v, int) and not isinstance(v, bool)
    if (metodo, ruta) == ("GET", "qr_codigos"):
        return {"codigos": mesa.listar(sv, base), "estilo": mesa.estilo(sv), "logo": mesa.logo_uri(sv) is not None, "tipos": mesa.TIPOS, "plantillas": mesa.PLANTILLAS,
                "maximo": mesa.maximo(sv), "modulos": mesa.MODULOS}
    if (metodo, ruta) == ("POST", "qr_codigo"):  # crea (sin id) o cambia etiqueta/destino/activo de uno; el código impreso no cambia
        if "id" in d:
            if not entero(d["id"]):
                raise ValueError("Id inválido")
            mesa.actualizar(sv, d["id"], {k: v for k, v in d.items() if k in ("etiqueta", "tipo", "destino", "mesa", "activo")})
            i = d["id"]
        else:
            i = mesa.crear(sv, d)["id"]
        auditar(sv, quien, f"qr_codigo:{i}")
        return {"ok": True, "id": i}
    if (metodo, ruta) == ("POST", "qr_generar"):  # «Mesa N» para un rango; las mesas que ya tienen código no se repiten
        a, b = d.get("desde"), d.get("hasta")
        if not (entero(a) and entero(b) and 1 <= a <= b <= mesa.maximo(sv) and b - a < mesa.MAX_MESAS):
            raise ValueError(f"El rango va de 1 a {mesa.maximo(sv)}")
        hay = {m for (m,) in sv._q("SELECT mesa FROM qr_codigos WHERE tipo='pedir'")}
        nuevos = [mesa.crear(sv, {"etiqueta": f"Mesa {n}", "tipo": "pedir", "mesa": n})["id"] for n in range(a, b + 1) if n not in hay]
        auditar(sv, quien, f"qr_generar:{a}-{b}")
        return {"ok": True, "creados": len(nuevos)}
    if (metodo, ruta) == ("POST", "qr_regenerar"):
        if not entero(d.get("id")):
            raise ValueError("Id inválido")
        c = mesa.regenerar(sv, d["id"])
        auditar(sv, quien, f"qr_regenerar:{d['id']}")
        return {"ok": True, "codigo": c, "url": f"{base}/m/{c}"}
    if (metodo, ruta) == ("POST", "qr_borrar"):
        if not entero(d.get("id")):
            raise ValueError("Id inválido")
        mesa.borrar(sv, d["id"])
        auditar(sv, quien, f"qr_borrar:{d['id']}")
        return {"ok": True}
    if (metodo, ruta) == ("POST", "qr_estilo"):
        e = mesa.guardar_estilo(sv, d.get("estilo"))
        auditar(sv, quien, "qr_estilo")
        return {"ok": True, "estilo": e}
    if (metodo, ruta) == ("POST", "qr_vista"):  # borrador sin guardar: la vista previa en vivo
        return {"svg": mesa.svg_de(sv, f"{base}/m/{'abcdefgh'}", mesa.estilo_valido(d.get("estilo")))}
    if (metodo, ruta) == ("POST", "qr_logo_borrar"):
        mesa.quitar_logo(sv)
        auditar(sv, quien, "qr_logo_borrar")
        return {"ok": True}
    raise LookupError(ruta)


def api_dueno(sv, quien, rol, metodo, ruta, d=None):
    """Rutas /panel/dueno/<ruta>, solo rol dueno. PermissionError → 403, LookupError → 404, ValueError → 409."""
    if rol != "dueno":
        raise PermissionError("Solo el dueño puede hacer esto")
    if (metodo, ruta) == ("GET", "usuarios"):
        return {"usuarios": lista_usuarios(sv)}
    if (metodo, ruta) == ("POST", "usuario"):
        return {"ok": True, "usuario": guardar_usuario(sv, d or {}, quien)}
    if (metodo, ruta) == ("GET", "menu"):
        return {"menu": menu_json(sv)}
    if (metodo, ruta) == ("POST", "plato"):
        return guardar_plato(sv, d or {}, quien)
    if (metodo, ruta) == ("POST", "foto_borrar"):
        return borrar_foto(sv, quien, (d or {}).get("plato"))
    if (metodo, ruta) == ("POST", "plato_borrar"):
        return borrar_plato(sv, quien, (d or {}).get("plato"))
    if (metodo, ruta) == ("POST", "usuario_borrar"):
        return borrar_usuario(sv, (d or {}).get("nombre"), quien)
    if (metodo, ruta) == ("GET", "ajustes"):
        return {"ajustes": ajustes_json(sv)}
    if (metodo, ruta) == ("POST", "ajustes"):
        return guardar_ajustes(sv, d or {}, quien)
    if (metodo, ruta) == ("POST", "pedido_editar"):
        return pedidos_admin.editar(sv, quien, d or {})
    if (metodo, ruta) == ("GET", "exportar") or (metodo, ruta) == ("POST", "exportar"):
        return pedidos_admin.exportar(sv, quien, d or {})
    if (metodo, ruta) == ("POST", "estadisticas"):
        return estadisticas(sv, (d or {}).get("dias"))
    if (metodo, ruta) == ("POST", "preguntar"):
        return preguntar(sv, (d or {}).get("texto"))
    if ruta.startswith("qr_"):
        return api_qr(sv, quien, metodo, ruta, d or {})
    raise LookupError(ruta)


if __name__ == "__main__":
    print(hash_pin(sys.argv[1]))
