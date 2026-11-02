"""Catálogo de herramientas genéricas. Un servicio elige cuáles usa en servicio.yaml.

Todo se guarda en una sola tabla `registros` (tipo + datos JSON): citas, leads, cobros,
mensajes programados, escalados... Así un servicio nuevo casi nunca necesita código nuevo.
"""
import datetime as dt
import json
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import yaml

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
FMT = "%Y-%m-%d %H:%M"


def _hora(s, tz):
    try:
        return dt.datetime.strptime(s, FMT).replace(tzinfo=tz)
    except (TypeError, ValueError):
        raise ValueError(f"Fecha/hora inválida '{s}'. Usa el formato AAAA-MM-DD HH:MM.")


def _huecos(sv, dia):
    """Inicios de cita posibles en un día según el horario del servicio."""
    h = sv.cfg.get("horario") or {}
    if h.get("duracion_cita_min", 30) <= 0:
        raise ValueError("duracion_cita_min debe ser mayor que 0 en servicio.yaml")
    if DIAS[dia.weekday()] not in h.get("dias", DIAS[:5]):
        return []
    paso = dt.timedelta(minutes=h.get("duracion_cita_min", 30))
    t = _hora(f"{dia:%Y-%m-%d} {h.get('desde', '08:00')}", sv.tz)
    fin = _hora(f"{dia:%Y-%m-%d} {h.get('hasta', '17:00')}", sv.tz)
    out = []
    while t + paso <= fin:
        out.append(t)
        t += paso
    return out


def _citas_activas(sv, dia):
    return [c for c in sv.listar("cita") if c["estado"] != "cancelada" and c["fecha_hora"].startswith(f"{dia:%Y-%m-%d}")]


def consultar_disponibilidad(sv, fecha):
    dia = _hora(f"{fecha} 00:00", sv.tz).date()
    ocupadas = {c["fecha_hora"] for c in _citas_activas(sv, dia)}
    ahora = sv.ahora()
    libres = [f"{t:%H:%M}" for t in _huecos(sv, dia) if t > ahora and f"{t:%Y-%m-%d %H:%M}" not in ocupadas]
    return {"fecha": fecha, "horas_libres": libres} if libres else {"fecha": fecha, "horas_libres": [], "nota": "Sin horas libres ese día (cerrado, lleno o ya pasó)."}


def agendar_cita(sv, nombre, telefono, fecha_hora, motivo=""):
    t = _hora(fecha_hora, sv.tz)
    fecha_hora = f"{t:%Y-%m-%d %H:%M}"  # strptime acepta "2026-11-3 9:00"; guardar siempre el formato canónico
    if t <= sv.ahora():
        raise ValueError("Esa hora ya pasó.")
    if t not in _huecos(sv, t.date()):
        raise ValueError("Fuera del horario de atención o no coincide con el inicio de un turno. Consulta la disponibilidad.")
    with sv.citas_lock:  # comprobar + insertar atómico: dos clientes a la vez no pueden tomar el mismo turno
        if any(c["fecha_hora"] == fecha_hora for c in _citas_activas(sv, t.date())):
            raise ValueError("Esa hora ya está ocupada. Consulta la disponibilidad.")
        cid = sv.insertar("cita", {"nombre": nombre, "telefono": telefono, "fecha_hora": fecha_hora, "motivo": motivo, "estado": "agendada"})
    horas = sv.cfg.get("recordatorio_horas_antes")
    if horas:
        cuando = t - dt.timedelta(hours=horas)
        if cuando > sv.ahora():
            texto = f"Hola {nombre}, le recordamos su cita en {sv.cfg.get('negocio', 'nuestro negocio')} el {t:%d/%m a las %H:%M}. ¿Nos confirma su asistencia? Responda SÍ o indíquenos si desea cambiarla."
            programar_mensaje(sv, telefono, f"{cuando:%Y-%m-%d %H:%M}", texto, cita_id=cid)
    return {"ok": True, "cita_id": cid, "fecha_hora": fecha_hora}


def cancelar_cita(sv, cita_id):
    sv.actualizar(cita_id, {"estado": "cancelada"}, tipo="cita")
    for m in sv.listar("mensaje_programado"):
        if m.get("cita_id") == cita_id and m["estado"] == "pendiente":
            sv.actualizar(m["id"], {"estado": "cancelado"})
    return {"ok": True}


def programar_mensaje(sv, telefono, enviar_en, texto, cita_id=None):
    enviar_en = f"{_hora(enviar_en, sv.tz):%Y-%m-%d %H:%M}"  # se compara como texto en pendientes()
    mid = sv.insertar("mensaje_programado", {"telefono": telefono, "enviar_en": enviar_en, "texto": texto, "estado": "pendiente", "cita_id": cita_id})
    return {"ok": True, "mensaje_id": mid}


def registrar(sv, tipo, datos, telefono=None):
    if tipo in ("cita", "mensaje_programado", "pedido", "aviso"):
        raise ValueError(f"Usa la herramienta específica para '{tipo}'.")
    return {"ok": True, "id": sv.insertar(tipo, {**datos, "telefono": telefono})}


def actualizar_registro(sv, id, cambios):
    sv.actualizar(id, cambios)
    return {"ok": True}


def buscar(sv, tipo, texto="", telefono=None):
    t = texto.lower()
    return [r for r in sv.listar(tipo) if r.get("telefono") == telefono and t in json.dumps(r, ensure_ascii=False).lower()][-20:]


def escalar_a_humano(sv, telefono, motivo):
    sv.insertar("escalado", {"telefono": telefono, "motivo": motivo, "estado": "abierto"})
    return {"ok": True, "nota": "Avisado el equipo humano. Dile al cliente que una persona le contactará pronto."}


# --- pedidos: el dinero va en centavos enteros y lo calcula el código, nunca el modelo ---
ESTADOS_PEDIDO = ["por_confirmar", "confirmado", "pago_reportado", "pagado", "en_preparacion", "listo", "entregado"]


def _c(x):
    """Importe del yaml (B/.) a centavos enteros; rechaza fracciones de centavo."""
    try:
        d = Decimal(str(x)) * 100
    except InvalidOperation:
        raise ValueError(f"Importe inválido: {x!r}")
    if d != d.to_integral_value():
        raise ValueError(f"Importe con fracción de centavo: {x!r}")
    return int(d)


def dinero(c):
    return f"B/. {c // 100}.{c % 100:02d}"


def cargar_menu(carpeta, archivo):
    """Lee y valida el menú: {id: item} con precio_c. ValueError si está mal."""
    items = yaml.safe_load((carpeta / archivo).read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"{archivo} debe ser una lista de ítems")
    menu = {}
    for it in items:
        if not isinstance(it, dict) or not all(it.get(k) not in (None, "") for k in ("id", "nombre", "precio")):
            raise ValueError(f"Ítem de menú sin id, nombre o precio: {it}")
        if it["id"] in menu:
            raise ValueError(f"Id de menú repetido: {it['id']}")
        pc = _c(it["precio"])
        if pc <= 0:
            raise ValueError(f"El precio de '{it['id']}' debe ser mayor que 0")
        menu[it["id"]] = {**it, "precio_c": pc}
    return menu


def texto_menu(menu):
    return "\n".join(f"- {i['id']} | {i['nombre']} | {dinero(i['precio_c'])} | {i.get('categoria', '')}" + ("" if i.get("disponible", True) else " | AGOTADO") for i in menu.values())


def _transicion(sv, pedido_id, desde, hacia, extra=None):
    """Cambio de estado atómico (también entre procesos): ver Servicio.actualizar."""
    return sv.actualizar(pedido_id, {"estado": hacia, **(extra or {})}, tipo="pedido", desde=desde)


def _hora_pedido(sv, hora_deseada):
    """Hora del pedido: la deseada si viene como AAAA-MM-DD HH:MM; texto libre ('lo antes posible') se ignora."""
    ahora = sv.ahora()
    try:
        t = _hora(hora_deseada, sv.tz)
    except ValueError:
        return ahora
    if t < ahora:
        raise ValueError("La hora deseada ya pasó.")
    return t


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).casefold()) if not unicodedata.combining(c)).strip()


def _envio(cfg, zona):
    """Costo de envío en centavos: fijo, o por zona (lista = zonas permitidas a costo fijo, dict = zona -> costo)."""
    e = cfg.get("envio") or {}
    zonas = e.get("zonas")
    if not zonas:
        return _c(e.get("costo", 0))
    por_zona = {_sin_tildes(z): e.get("costo", 0) for z in zonas} if isinstance(zonas, list) else {_sin_tildes(z): c for z, c in zonas.items()}
    if _sin_tildes(zona or "") not in por_zona:
        raise ValueError("Zona sin entrega; ofrece recogida o escalar_a_humano.")
    return _c(por_zona[_sin_tildes(zona)])


def crear_pedido(sv, items, modalidad, nombre, telefono, direccion="", hora_deseada="", zona=""):
    if not items:
        raise ValueError("El pedido no tiene ítems.")
    if modalidad not in ("entrega", "recogida"):
        raise ValueError("Modalidad inválida: debe ser 'entrega' o 'recogida'.")
    if modalidad == "entrega" and not direccion.strip():
        raise ValueError("Para entrega se necesita la dirección.")
    lineas, subtotal = [], 0
    for it in items:
        cant = it.get("cantidad")
        if not isinstance(cant, int) or isinstance(cant, bool) or cant < 1:
            raise ValueError(f"Cantidad inválida para '{it.get('id')}': debe ser un entero de 1 o más.")
        m = sv.menu.get(it.get("id"))
        if not m:
            raise ValueError(f"No existe '{it.get('id')}' en el menú.")
        if not m.get("disponible", True):
            raise ValueError(f"'{m['nombre']}' no está disponible hoy.")
        imp = m["precio_c"] * cant
        subtotal += imp
        lineas.append({"id": m["id"], "nombre": m["nombre"], "cantidad": cant, "nota": it.get("nota", ""), "precio_c": m["precio_c"], "importe_c": imp})
    cfg = sv.cfg.get("pedidos") or {}
    t = _hora_pedido(sv, hora_deseada)
    h = sv.cfg.get("horario") or {}
    abre = _hora(f"{t:%Y-%m-%d} {h.get('desde', '08:00')}", sv.tz)
    cierra = _hora(f"{t:%Y-%m-%d} {h.get('hasta', '17:00')}", sv.tz) - dt.timedelta(minutes=cfg.get("cierre_cocina_min", 0))
    if DIAS[t.weekday()] not in h.get("dias", DIAS[:5]) or not abre <= t <= cierra:
        raise ValueError(f"Local cerrado a esa hora (la cocina recibe pedidos de {abre:%H:%M} a {cierra:%H:%M} en días de atención).")
    minimo = _c(cfg.get("pedido_minimo") or 0)
    if subtotal < minimo:
        raise ValueError(f"El pedido mínimo es {dinero(minimo)} y el subtotal es {dinero(subtotal)}.")
    itbms = int((Decimal(subtotal) * Decimal(str(cfg.get("impuesto_pct") or 0)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    envio = _envio(cfg, zona) if modalidad == "entrega" else 0
    total = subtotal + itbms + envio
    pid = sv.insertar("pedido", {"nombre": nombre, "telefono": telefono, "modalidad": modalidad, "direccion": direccion, "hora_deseada": hora_deseada, "zona": zona,
                                 "lineas": lineas, "subtotal_c": subtotal, "itbms_c": itbms, "envio_c": envio, "total_c": total, "estado": "por_confirmar"})
    resumen = "\n".join([f"{l['cantidad']} x {l['nombre']}: {dinero(l['importe_c'])}" + (f" ({l['nota']})" if l["nota"] else "") for l in lineas]
                        + [f"Subtotal: {dinero(subtotal)}"] + ([f"ITBMS: {dinero(itbms)}"] if itbms else []) + ([f"Envío: {dinero(envio)}"] if modalidad == "entrega" else []) + [f"Total: {dinero(total)}"])
    return {"ok": True, "pedido_id": pid, "resumen": resumen, "total": dinero(total), "total_c": total}


def _aviso(sv, p, pedido_id, texto):
    sv.insertar("aviso", {"pedido_id": pedido_id, "texto": texto, "telefono": p["telefono"]})
    print(f"[aviso restaurante] {texto}")


def confirmar_pedido(sv, pedido_id):
    p = _transicion(sv, pedido_id, {"por_confirmar"}, "confirmado")
    _aviso(sv, p, pedido_id, f"Pedido #{pedido_id} confirmado de {p['nombre']} ({p['modalidad']}), total {dinero(p['total_c'])}.")
    return {"ok": True, "estado": "confirmado", "total": dinero(p["total_c"]), "pago": {k: v for k, v in (sv.cfg.get("pago") or {}).items() if v is not None}}


def reportar_pago(sv, pedido_id, metodo, referencia):
    p = _transicion(sv, pedido_id, {"confirmado"}, "pago_reportado", {"metodo": metodo, "referencia": referencia})
    _aviso(sv, p, pedido_id, f"Verificar Yappy/transferencia ref {referencia} ({metodo}) y marcar pagado: pedido #{pedido_id}, total {dinero(p['total_c'])}.")
    return {"ok": True, "estado": "pago_reportado", "nota": "El equipo verificará el pago; aún no está verificado."}


def cancelar_pedido(sv, pedido_id):
    _transicion(sv, pedido_id, {"por_confirmar", "confirmado"}, "cancelado")
    return {"ok": True}


PREVIOS = {"pagado": {"pago_reportado", "confirmado"}, "en_preparacion": {"pagado"}, "listo": {"en_preparacion"}, "entregado": {"listo"}}


def marcar_pedido(sv, pedido_id, estado):
    """Uso del restaurante (comando `pedidos`), no del modelo."""
    if estado not in PREVIOS:
        raise ValueError("Estado inválido: pagado, en_preparacion, listo o entregado.")
    p = _transicion(sv, pedido_id, PREVIOS[estado], estado)  # el pago va antes de preparar; un cancelado no está en ningún conjunto
    if estado == "listo":
        programar_mensaje(sv, p["telefono"], f"{sv.ahora():%Y-%m-%d %H:%M}", f"Hola {p['nombre']}, su pedido #{pedido_id} está listo.")
    return {"ok": True, "estado": estado}


def _s(props, req):
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


S, O = {"type": "string"}, {"type": "object", "description": "Campos libres clave: valor"}
FH = {"type": "string", "description": "AAAA-MM-DD HH:MM, hora local"}

CATALOGO = {
    "consultar_disponibilidad": (consultar_disponibilidad, "Horas libres para citas en una fecha.", _s({"fecha": {"type": "string", "description": "AAAA-MM-DD"}}, ["fecha"])),
    "agendar_cita": (agendar_cita, "Agenda una cita (valida horario y choques; programa recordatorio). Confirma los datos con el cliente antes.", _s({"nombre": S, "fecha_hora": FH, "motivo": S}, ["nombre", "fecha_hora"])),
    "cancelar_cita": (cancelar_cita, "Cancela una cita por su id (y su recordatorio).", _s({"cita_id": {"type": "integer"}}, ["cita_id"])),
    "programar_mensaje": (programar_mensaje, "Programa un mensaje de WhatsApp al cliente para más tarde (seguimiento, recordatorio, cobro).", _s({"enviar_en": FH, "texto": S}, ["enviar_en", "texto"])),
    "registrar": (registrar, "Guarda un registro de cualquier tipo (lead, cobro, pedido, siniestro, encuesta...).", _s({"tipo": S, "datos": O}, ["tipo", "datos"])),
    "actualizar_registro": (actualizar_registro, "Cambia campos de un registro existente (p. ej. estado de cita a 'confirmada', cobro a 'pagado').", _s({"id": {"type": "integer"}, "cambios": O}, ["id", "cambios"])),
    "buscar": (buscar, "Busca registros del cliente actual de un tipo (cita, lead, cobro...) que contengan un texto. Devuelve los 20 más recientes.", _s({"tipo": S, "texto": S}, ["tipo"])),
    "escalar_a_humano": (escalar_a_humano, "Pasa la conversación a una persona: el cliente lo pide, está molesto, es urgente o no sabes la respuesta.", _s({"motivo": S}, ["motivo"])),
    "crear_pedido": (crear_pedido, "Crea un pedido con los ids del menú; el código calcula el total. Devuelve el resumen para leerlo al cliente tal cual.", _s({"items": {"type": "array", "items": _s({"id": S, "cantidad": {"type": "integer"}, "nota": S}, ["id", "cantidad"])}, "modalidad": {"type": "string", "enum": ["entrega", "recogida"]}, "nombre": S, "direccion": S, "hora_deseada": S, "zona": S}, ["items", "modalidad", "nombre"])),
    "confirmar_pedido": (confirmar_pedido, "Confirma un pedido por_confirmar tras un 'sí' explícito del cliente; devuelve los datos de pago.", _s({"pedido_id": {"type": "integer"}}, ["pedido_id"])),
    "reportar_pago": (reportar_pago, "Registra que el cliente dice haber pagado (método y referencia). No lo da por verificado.", _s({"pedido_id": {"type": "integer"}, "metodo": S, "referencia": S}, ["pedido_id", "metodo", "referencia"])),
    "cancelar_pedido": (cancelar_pedido, "Cancela un pedido por_confirmar o confirmado.", _s({"pedido_id": {"type": "integer"}}, ["pedido_id"])),
}


def esquemas(nombres):
    faltan = [n for n in nombres if n not in CATALOGO]
    if faltan:
        raise ValueError(f"Herramientas desconocidas en servicio.yaml: {faltan}. Disponibles: {list(CATALOGO)}")
    return [{"name": n, "description": CATALOGO[n][1], "input_schema": CATALOGO[n][2]} for n in nombres]


CON_TELEFONO = {"agendar_cita", "programar_mensaje", "escalar_a_humano", "registrar", "buscar", "crear_pedido"}
INMUTABLES = {"telefono", "fecha_hora", "cita_id"}


def ejecutar(sv, nombre, entrada, cliente):
    """Ejecuta una herramienta para el cliente que escribe: nunca lee ni cambia datos de otro cliente."""
    if nombre not in sv.cfg.get("herramientas", []):
        raise ValueError(f"Herramienta no permitida: {nombre}")
    entrada = dict(entrada)
    if nombre in CON_TELEFONO:
        entrada["telefono"] = cliente
    if nombre in ("cancelar_cita", "actualizar_registro", "confirmar_pedido", "reportar_pago", "cancelar_pedido"):
        fila = sv._q("SELECT tipo, datos FROM registros WHERE id=?", (entrada.get("cita_id", entrada.get("pedido_id", entrada.get("id"))),))
        if not fila or json.loads(fila[0][1]).get("telefono") != cliente or (nombre.endswith("_pedido") or nombre == "reportar_pago") and fila[0][0] != "pedido":
            raise ValueError("No existe ese registro para este cliente.")
        if nombre == "actualizar_registro" and fila[0][0] in ("pedido", "aviso"):
            raise ValueError("Los pedidos no se cambian con esta herramienta; usa las herramientas de pedido.")
        if INMUTABLES & set(entrada.get("cambios", {})):
            raise ValueError("No se puede cambiar teléfono, fecha_hora ni cita_id; para mover una cita, cancélala y agenda otra.")
    return CATALOGO[nombre][0](sv, **entrada)
