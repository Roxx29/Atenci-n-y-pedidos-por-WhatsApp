"""Catálogo de herramientas genéricas. Un servicio elige cuáles usa en servicio.yaml.

Todo se guarda en una sola tabla `registros` (tipo + datos JSON): citas, leads, cobros,
mensajes programados, escalados... Así un servicio nuevo casi nunca necesita código nuevo.
"""
import datetime as dt
import json

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
    if tipo in ("cita", "mensaje_programado"):
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
}


def esquemas(nombres):
    faltan = [n for n in nombres if n not in CATALOGO]
    if faltan:
        raise ValueError(f"Herramientas desconocidas en servicio.yaml: {faltan}. Disponibles: {list(CATALOGO)}")
    return [{"name": n, "description": CATALOGO[n][1], "input_schema": CATALOGO[n][2]} for n in nombres]


CON_TELEFONO = {"agendar_cita", "programar_mensaje", "escalar_a_humano", "registrar", "buscar"}
INMUTABLES = {"telefono", "fecha_hora", "cita_id"}


def ejecutar(sv, nombre, entrada, cliente):
    """Ejecuta una herramienta para el cliente que escribe: nunca lee ni cambia datos de otro cliente."""
    if nombre not in sv.cfg.get("herramientas", []):
        raise ValueError(f"Herramienta no permitida: {nombre}")
    entrada = dict(entrada)
    if nombre in CON_TELEFONO:
        entrada["telefono"] = cliente
    if nombre in ("cancelar_cita", "actualizar_registro"):
        fila = sv._q("SELECT datos FROM registros WHERE id=?", (entrada.get("cita_id", entrada.get("id")),))
        if not fila or json.loads(fila[0][0]).get("telefono") != cliente:
            raise ValueError("No existe ese registro para este cliente.")
        if INMUTABLES & set(entrada.get("cambios", {})):
            raise ValueError("No se puede cambiar teléfono, fecha_hora ni cita_id; para mover una cita, cancélala y agenda otra.")
    return CATALOGO[nombre][0](sv, **entrada)
