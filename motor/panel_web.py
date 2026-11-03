"""Panel web del restaurante (rutas /panel* del servidor `motor web`). Solo stdlib.

Acceso del personal (.env): PANEL_PIN (6 dígitos, rol dueno) o PANEL_PIN_HASH ("pbkdf2$iter$sal$hash"),
y opcional PANEL_PIN_PERSONAL / PANEL_PIN_PERSONAL_HASH (rol personal). Sin PIN válido, /panel está deshabilitado (404).
Las reglas de estado son las de herramientas.marcar_pedido / cancelar_pedido.

  python -m motor.panel_web 123456      # imprime un PANEL_PIN_HASH
"""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import sys
import time

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
        return hmac.new(self.clave, f"{rol}.{exp}.{lim}".encode(), hashlib.sha256).hexdigest()

    def cookie(self, rol, lim=None):
        """Cookie con inactividad de 30 min (exp) y tope absoluto de 12 h (lim)."""
        lim = lim or int(time.time()) + VIDA_S
        exp = min(int(time.time()) + self.inactivo, lim)
        return f"{self.nombre}={rol}.{exp}.{lim}.{self._firma(rol, exp, lim)}; HttpOnly; SameSite=Strict; Path={self.ruta}; Max-Age={exp - int(time.time())}"

    def borrar_cookie(self):
        return f"{self.nombre}=; HttpOnly; SameSite=Strict; Path={self.ruta}; Max-Age=0"

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


def _mask(tel):
    t = str(tel or "")
    return t[:1] + "***-**" + t[-2:] if len(t) > 4 else "***"


def pedido_json(p, detalle=False):
    """Lista: teléfono enmascarado, solo zona y sin imagen. Detalle: teléfono y dirección completos solo en pedidos abiertos."""
    cerrado = p["estado"] in TERMINADOS
    d = {"id": p["id"], "estado": p["estado"], "nombre": p.get("nombre"), "telefono": p.get("telefono") if detalle and not cerrado else _mask(p.get("telefono")),
         "modalidad": p.get("modalidad"), "zona": p.get("zona"), "hora_deseada": p.get("hora_deseada"), "notas": p.get("notas"),
         "items": [{"nombre": l["nombre"], "cantidad": l["cantidad"], "precio": dinero(l["precio_c"]), **({"nota": l["nota"]} if l.get("nota") else {})} for l in p["lineas"]],
         "subtotal": dinero(p["subtotal_c"]), "impuesto": dinero(p["itbms_c"]), "envio": dinero(p["envio_c"]), "total": dinero(p["total_c"]),
         "metodo": p.get("metodo"), "referencia": p.get("referencia"), "comprobante": bool(p.get("comprobante")),
         "creado_at": p.get("creado_at"), "estado_at": p.get("estado_at") or p.get("creado_at"), "acciones": acciones_de(p),
         "repartidor": p.get("repartidor"), "tiene_ubicacion": bool(p.get("ubicacion"))}
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
            "mapa": ubicacion.url_mapa(p), "tiene_ubicacion": bool(p.get("ubicacion")),
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
    from .panel import ruta_comprobante
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    ruta = p and p["estado"] in PAGO_PENDIENTE and ruta_comprobante(sv, p)
    return (ruta.read_bytes(), CT_IMAGEN[ruta.suffix[1:]]) if ruta else None


def auditar(sv, usuario, accion, pedido_id=None):
    """Tabla auditoria (usuario=rol, accion, pedido_id, at): sin datos personales ni IP."""
    sv.insertar("auditoria", {"usuario": usuario, "accion": accion, "pedido_id": pedido_id, "at": sv.ahora().isoformat()})


if __name__ == "__main__":
    print(hash_pin(sys.argv[1]))
