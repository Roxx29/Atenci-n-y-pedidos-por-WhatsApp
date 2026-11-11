"""Panel web del restaurante (rutas /panel* del servidor `motor web`). Solo stdlib.

Acceso del personal (.env): PANEL_PIN (6 dígitos, rol dueno) o PANEL_PIN_HASH ("pbkdf2$iter$sal$hash"),
y opcional PANEL_PIN_PERSONAL / PANEL_PIN_PERSONAL_HASH (rol personal). Sin PIN válido, /panel está deshabilitado (404).
Las reglas de estado son las de herramientas.marcar_pedido / cancelar_pedido.

  python -m motor.panel_web 123456      # imprime un PANEL_PIN_HASH
"""
import hashlib
import hmac
import io
import ipaddress
import os
import re
import secrets
import sys
import time
import unicodedata
from collections.abc import Mapping

from . import herramientas
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


def env_panel():
    roles = {}
    for rol, p, h in (("dueno", "PANEL_PIN", "PANEL_PIN_HASH"), ("personal", "PANEL_PIN_PERSONAL", "PANEL_PIN_PERSONAL_HASH")):
        texto, hash_ = os.environ.get(p) or None, os.environ.get(h) or None
        if pin_valido(texto, hash_, p):
            roles[rol] = (texto, hash_)
    return roles


def env_repartidores():
    """{nombre: (pin, hash)} de REPARTIDORES="Juan:482913,Pedro:pbkdf2$..." (y REPARTIDOR_PIN = un repartidor llamado Repartidor). Nombres A-Za-z0-9_-; PIN únicos."""
    crudo = [x.strip().partition(":")[::2] for x in os.environ.get("REPARTIDORES", "").split(",") if ":" in x]
    if os.environ.get("REPARTIDOR_PIN"):
        crudo.append(("Repartidor", os.environ["REPARTIDOR_PIN"]))
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

    def __init__(self, sv, roles):
        self.sv, self.de = sv, set(roles)
        sembrar_usuarios(sv)

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


def sembrar_usuarios(sv):
    """Importa PANEL_PIN* (usuarios Dueno / Personal) y REPARTIDORES del .env si ese nombre aún no está en la BD; nunca pisa la BD. Solo guarda el hash."""
    semillas = [(n, r, *env_panel().get(r, (None, None))) for n, r in (("Dueno", "dueno"), ("Personal", "personal"))]
    semillas += [(n, "repartidor", t, h) for n, (t, h) in env_repartidores().items()]
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
        _poner(sv, nuevo)
    cambios = ["usuario_crear"] if not u else [a for a, si in (("usuario_pin", pin), ("usuario_rol", rol and rol != u["rol"]),
                                                               ("usuario_activo" if activo else "usuario_desactivar", activo is not None and activo != u["activo"])) if si]
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
    iid, nombre, precio, cat, disp = d.get("id"), d.get("nombre"), d.get("precio"), d.get("categoria"), d.get("disponible")
    if iid is not None and not (isinstance(iid, str) and ID_PLATO.fullmatch(iid)):
        raise ValueError("Id inválido: 1 a 40 minúsculas, números o -")
    if nombre is not None and not (isinstance(nombre, str) and 1 <= len(nombre.strip()) <= 80):
        raise ValueError("El nombre no puede estar vacío (máx. 80)")
    if cat is not None and not (isinstance(cat, str) and len(cat) <= 30):
        raise ValueError("Categoría demasiado larga (máx. 30)")
    if disp is not None and not isinstance(disp, bool):
        raise ValueError("Disponible debe ser sí o no")
    cambios = {k: v for k, v in (("nombre", nombre and nombre.strip()), ("categoria", cat), ("disponible", disp)) if v is not None}
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
    return [{"nombre": u["nombre"], "rol": u["rol"], "activo": u["activo"]} for u in _todos(sv)]


class Bloqueo:
    """5 fallos de PIN desde una IP → bloqueo de 5 min."""

    def __init__(self):
        self.fallos, self.hasta = {}, {}

    def bloqueada(self, ip):
        return self.hasta.get(ip, 0) > time.time()

    def fallo(self, ip):
        self.fallos[ip] = self.fallos.get(ip, 0) + 1
        if self.fallos[ip] >= 5:
            self.fallos[ip], self.hasta[ip] = 0, time.time() + 300

    def acierto(self, ip):
        self.fallos.pop(ip, None)


def acciones_de(p):
    """Acciones válidas ahora (mismas reglas que marcar_pedido / cancelar_pedido)."""
    a = [e for e, previos in herramientas.PREVIOS.items() if p["estado"] in previos]
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
    d = {"id": p["id"], "estado": p["estado"], "nombre": p.get("nombre"), "telefono": p.get("telefono") if detalle and not cerrado else _mask(p.get("telefono")),
         "modalidad": p.get("modalidad"), "zona": p.get("zona"), "hora_deseada": p.get("hora_deseada"), "notas": p.get("notas"),
         "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], "precio": dinero(l["precio_c"]), **({"nota": l["nota"]} if l.get("nota") else {})} for l in p["lineas"]],
         "subtotal": dinero(p["subtotal_c"]), "impuesto": dinero(p["itbms_c"]), "envio": dinero(p["envio_c"]), "total": dinero(p["total_c"]),
         "metodo": p.get("metodo"), "referencia": p.get("referencia"), "comprobante": bool(p.get("comprobante")),
         "creado_at": p.get("creado_at"), "estado_at": p.get("estado_at") or p.get("creado_at"), "acciones": acciones_de(p),
         "repartidor": p.get("repartidor"), "tiene_ubicacion": bool(p.get("ubicacion")),
         "fuera_de_zona": bool((p.get("ubicacion") or {}).get("fuera_de_zona"))}
    if detalle and not cerrado:
        d["direccion"] = p.get("direccion")
        d["mapa"] = ubicacion.url_mapa(p)
    return d


def estado_panel(sv, desde=0, repartidores=()):
    """Lo que el panel sondea cada 3 s: pedidos (activos + cerrados de hoy), avisos nuevos (id > desde) o de pedidos pendientes, escalados abiertos."""
    herramientas.caducar_pedidos(sv)
    hoy = sv.ahora().date().isoformat()
    peds = [p for p in sv.listar("pedido") if p["estado"] not in TERMINADOS or (p.get("estado_at") or "").startswith(hoy)]
    espera = {p["id"] for p in peds if p["estado"] in PAGO_PENDIENTE}
    todos = sv.listar("aviso")
    avisos = [{"id": a["id"], "tipo": "pago" if a.get("pedido_id") in espera else "pedido", "texto": a["texto"], "pedido_id": a.get("pedido_id")}
              for a in reversed(todos) if a["id"] > desde or a.get("pedido_id") in espera]
    esc = [{"id": e["id"], "nombre": None, "telefono": _mask(e.get("telefono")), "motivo": e.get("motivo"), "pedido_id": None}
           for e in sv.listar("escalado") if e.get("estado") == "abierto"]
    return {"pedidos": [pedido_json(p) for p in sorted(peds, key=lambda p: p["id"])], "avisos": avisos,
            "ultimo": max([a["id"] for a in todos] + [0]), "escalados": esc, "repartidores": sorted(repartidores)}


def marcar(sv, pedido_id, estado, rol):
    """pagado | pago_rechazado | en_preparacion | listo | entregado | cancelado. ValueError legible si no procede. Anota quién y cuándo."""
    if estado in ("cancelado", "cancelar"):
        herramientas.cancelar_pedido(sv, pedido_id)
        estado = "cancelado"
    else:
        herramientas.marcar_pedido(sv, pedido_id, estado)
    ahora = sv.ahora().isoformat()
    return sv.actualizar(pedido_id, {f"{estado}_por": rol, f"{estado}_at": ahora, "estado_at": ahora}, tipo="pedido")


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


def pedido_repartidor(p):
    return {"id": p["id"], "estado": p["estado"], "nombre": p.get("nombre"), "telefono": p.get("telefono"), "direccion": p.get("direccion"), "zona": p.get("zona"),
            "notas": p.get("notas"), "hora_deseada": p.get("hora_deseada"),
            "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], **({"nota": l["nota"]} if l.get("nota") else {})} for l in p["lineas"]],
            "total": dinero(p["total_c"]), "pagado": True,  # el pago va antes de preparar: el repartidor no cobra
            "mapa": ubicacion.url_mapa(p), "tiene_ubicacion": bool(p.get("ubicacion")), "fuera_de_zona": bool((p.get("ubicacion") or {}).get("fuera_de_zona")),
            "acciones": [SIGUIENTE[p["estado"]]] if p["estado"] in SIGUIENTE else []}


def pedidos_repartidor(sv, nombre):
    herramientas.caducar_pedidos(sv)
    return [pedido_repartidor(p) for p in sv.listar("pedido") if p.get("repartidor") == nombre and p["estado"] in EN_RUTA]


def marcar_repartidor(sv, nombre, pedido_id, estado):
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if not p or p.get("repartidor") != nombre or p["estado"] not in EN_RUTA:
        raise ValueError("No tiene ese pedido asignado")
    if SIGUIENTE.get(p["estado"]) != estado:
        raise ValueError("Ese cambio no corresponde al estado actual del pedido")
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
    raise LookupError(ruta)


if __name__ == "__main__":
    print(hash_pin(sys.argv[1]))
