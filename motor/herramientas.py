"""Catálogo de herramientas genéricas. Un servicio elige cuáles usa en servicio.yaml.

Todo se guarda en una sola tabla `registros` (tipo + datos JSON): citas, leads, cobros,
mensajes programados, escalados... Así un servicio nuevo casi nunca necesita código nuevo.
"""
import datetime as dt
import json
import re
import secrets
import struct
import time
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


def _pedido_para_modelo(p):
    """El modelo no ve centavos (los repite al cliente): importes como texto 'B/. 9.36', sin campos *_c."""
    out = {k: v for k, v in p.items() if not k.endswith("_c") and k not in ("lineas", "comprobante")}
    out.update(total=dinero(p["total_c"]), subtotal=dinero(p["subtotal_c"]), impuesto=dinero(p["itbms_c"]), envio=dinero(p["envio_c"]),
               lineas=[{**{k: v for k, v in l.items() if not k.endswith("_c")}, "precio": dinero(l["precio_c"]), "importe": dinero(l["importe_c"])} for l in p["lineas"]])
    return out


def buscar(sv, tipo, texto="", telefono=None):
    t = texto.lower()
    r = [r for r in sv.listar(tipo) if r.get("telefono") == telefono and t in json.dumps(r, ensure_ascii=False).lower()][-20:]
    return [_pedido_para_modelo(p) for p in r] if tipo == "pedido" else r


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
    if not items:
        raise ValueError(f"{archivo} está vacío")
    menu = {}
    for it in items:
        if not isinstance(it, dict) or not all(it.get(k) not in (None, "") for k in ("id", "nombre", "precio")):
            raise ValueError(f"Ítem de menú sin id, nombre o precio: {it}")
        iid = str(it["id"])  # el modelo manda texto; un id numérico del yaml (id: 1) debe coincidir
        if iid in menu:
            raise ValueError(f"Id de menú repetido: {iid}")
        pc = _c(it["precio"])
        if pc <= 0:
            raise ValueError(f"El precio de '{iid}' debe ser mayor que 0")
        menu[iid] = {**it, "id": iid, "precio_c": pc}
    return menu


def texto_menu(menu):
    return "\n".join(f"- {i['id']} | {i['nombre']} | {dinero(i['precio_c'])} | {i.get('categoria', '')}" + ("" if i.get("disponible", True) else " | AGOTADO") for i in menu.values())


def _transicion(sv, pedido_id, desde, hacia, extra=None):
    """Cambio de estado atómico (también entre procesos): ver Servicio.actualizar."""
    return sv.actualizar(pedido_id, {"estado": hacia, **(extra or {})}, tipo="pedido", desde=desde)


def _hora_pedido(sv, hora_deseada):
    """(hora, texto a guardar): la deseada en formato canónico si viene como AAAA-MM-DD HH:MM; texto libre ('lo antes posible') se guarda tal cual y se usa la hora actual."""
    ahora = sv.ahora()
    try:
        t = _hora(hora_deseada, sv.tz)
    except ValueError:
        return ahora, hora_deseada
    if t < ahora:
        raise ValueError("La hora deseada ya pasó.")
    return t, f"{t:%Y-%m-%d %H:%M}"


def _turnos(sv, dia, cierre_min=0):
    """Turnos [(abre, último minuto para pedir)] del día: `turnos: [[desde, hasta], ...]` o el desde/hasta simple; [] si no es día de atención."""
    h = sv.cfg.get("horario") or {}
    if DIAS[dia.weekday()] not in h.get("dias", DIAS[:5]):
        return []
    pares = h.get("turnos") or [[h.get("desde", "08:00"), h.get("hasta", "17:00")]]
    return [(_hora(f"{dia:%Y-%m-%d} {a}", sv.tz), _hora(f"{dia:%Y-%m-%d} {b}", sv.tz) - dt.timedelta(minutes=cierre_min)) for a, b in pares]


MAX_TEXTO, MAX_CANTIDAD, MAX_LINEAS = 200, 50, 30  # topes anti-abuso


def _txt(v, campo):
    """Texto opcional del modelo: None es vacío; solo str y con tope de largo."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"'{campo}' debe ser texto.")
    if len(v) > MAX_TEXTO:
        raise ValueError(f"'{campo}' es demasiado largo (máximo {MAX_TEXTO} caracteres).")
    return v


def _una_linea(s):
    return s.replace("\r", " ").replace("\n", " ")


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).casefold()) if not unicodedata.combining(c)).strip()


def _nombre_real(nombre, telefono):
    """El modelo a veces copia el teléfono del prefijo como nombre: exige un nombre de persona."""
    n, dig = nombre.strip(), "".join(c for c in str(telefono) if c.isdigit())
    if sum(c.isalpha() for c in n) < 2 or n.casefold().startswith("cliente") or (len(dig) >= 4 and dig in "".join(c for c in n if c.isdigit())):
        raise ValueError("Falta el nombre real del cliente: pregúntaselo (no uses su teléfono ni 'cliente'); no inventes datos.")


def caducar_pedidos(sv):
    """Pasa a 'caducado' los por_confirmar con más de pedidos.caduca_min (30 por defecto) minutos; devuelve sus ids."""
    limite = sv.ahora() - dt.timedelta(minutes=(sv.cfg.get("pedidos") or {}).get("caduca_min", 30))
    ids = []
    for p in sv.listar("pedido"):
        if p["estado"] == "por_confirmar" and p.get("creado_at") and dt.datetime.fromisoformat(p["creado_at"]) <= limite:
            try:
                _transicion(sv, p["id"], {"por_confirmar"}, "caducado")
                ids.append(p["id"])
            except ValueError:  # otro proceso lo confirmó o canceló justo ahora
                pass
    return ids


def _envio(cfg, zona):
    """Costo de envío en centavos: fijo, o por zona (lista = zonas permitidas a costo fijo, dict = zona -> costo)."""
    e = cfg.get("envio") or {}
    zonas = e.get("zonas")
    if not zonas:
        return _c(e.get("costo", 0))
    por_zona = {_sin_tildes(z): e.get("costo", 0) for z in zonas} if isinstance(zonas, list) else {_sin_tildes(z): c for z, c in zonas.items()}
    if not (zona or "").strip():
        raise ValueError("Falta la zona de entrega: pregúntasela al cliente (" + ", ".join(por_zona) + "); no inventes datos.")
    if _sin_tildes(zona) not in por_zona:
        raise ValueError("Zona sin entrega; ofrece recogida o escalar_a_humano.")
    return _c(por_zona[_sin_tildes(zona)])


def crear_pedido(sv, items, modalidad, nombre, telefono, direccion="", hora_deseada="", zona=""):
    if not isinstance(items, list) or not items:
        raise ValueError("El pedido no tiene ítems (debe ser una lista de ítems).")
    if len(items) > MAX_LINEAS:
        raise ValueError(f"Máximo {MAX_LINEAS} líneas por pedido.")
    nombre, direccion, hora_deseada, zona = _txt(nombre, "nombre"), _txt(direccion, "direccion"), _txt(hora_deseada, "hora_deseada"), _txt(zona, "zona")
    _nombre_real(nombre, telefono)
    if modalidad not in ("entrega", "recogida"):
        raise ValueError("Modalidad inválida: debe ser 'entrega' o 'recogida'; pregúntasela al cliente, no inventes datos.")
    if modalidad == "entrega" and not direccion.strip():
        raise ValueError("Falta la dirección de entrega: pregúntasela al cliente; no inventes datos.")
    lineas, subtotal = [], 0
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("Cada ítem debe ser un objeto con id y cantidad.")
        cant = it.get("cantidad")
        if not isinstance(cant, int) or isinstance(cant, bool) or not 1 <= cant <= MAX_CANTIDAD:
            raise ValueError(f"Cantidad inválida para '{it.get('id')}': debe ser un entero de 1 a {MAX_CANTIDAD}.")
        m = sv.menu.get(it.get("id")) if isinstance(it.get("id"), str) else None
        if not m:
            raise ValueError(f"No existe '{it.get('id')}' en el menú.")
        if not m.get("disponible", True):
            raise ValueError(f"'{m['nombre']}' no está disponible hoy.")
        imp = m["precio_c"] * cant
        subtotal += imp
        lineas.append({"id": m["id"], "nombre": m["nombre"], "cantidad": cant, "nota": _txt(it.get("nota"), "nota"), "precio_c": m["precio_c"], "importe_c": imp})
    cfg = sv.cfg.get("pedidos") or {}
    t, hora_deseada = _hora_pedido(sv, hora_deseada)
    turnos = _turnos(sv, t.date(), cfg.get("cierre_cocina_min", 0))
    if not any(abre <= t <= cierra for abre, cierra in turnos):
        rango = " y ".join(f"{a:%H:%M} a {c:%H:%M}" for a, c in turnos) or "ninguna"
        raise ValueError(f"Local cerrado a esa hora (la cocina recibe pedidos de {rango} en días de atención).")
    minimo = _c(cfg.get("pedido_minimo") or 0)
    if subtotal < minimo:
        raise ValueError(f"El pedido mínimo es {dinero(minimo)} y el subtotal es {dinero(subtotal)}.")
    itbms = int((Decimal(subtotal) * Decimal(str(cfg.get("impuesto_pct") or 0)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    envio = _envio(cfg, zona) if modalidad == "entrega" else 0
    total = subtotal + itbms + envio
    caducar_pedidos(sv)
    previo = next((p for p in sv.listar("pedido") if p["telefono"] == telefono and p["estado"] == "por_confirmar"), None)  # un solo pedido en construcción por cliente
    datos = {"nombre": nombre, "telefono": telefono, "modalidad": modalidad, "direccion": direccion, "hora_deseada": hora_deseada, "zona": zona,
                                 "lineas": lineas, "subtotal_c": subtotal, "itbms_c": itbms, "envio_c": envio, "total_c": total, "estado": "por_confirmar", "creado_at": sv.ahora().isoformat()}
    if previo:
        pid = previo["id"]
        sv.actualizar(pid, datos, tipo="pedido", desde={"por_confirmar"})
    else:
        pid = sv.insertar("pedido", datos)
    resumen = "\n".join([f"{l['cantidad']} x {l['nombre']}: {dinero(l['importe_c'])}" + (f" ({l['nota']})" if l["nota"] else "") for l in lineas]
                        + [f"Subtotal: {dinero(subtotal)}"] + ([f"ITBMS: {dinero(itbms)}"] if itbms else []) + ([f"Envío: {dinero(envio)}"] if modalidad == "entrega" else []) + [f"Total: {dinero(total)}"])
    return {"ok": True, "pedido_id": pid, "resumen": resumen, "total": dinero(total), "actualizado": bool(previo)}


def _aviso(sv, p, pedido_id, texto):
    sv.insertar("aviso", {"pedido_id": pedido_id, "texto": texto, "telefono": p["telefono"]})
    print(f"[aviso restaurante] {texto}")


def confirmar_pedido(sv, pedido_id):
    caducar_pedidos(sv)
    try:
        p = _transicion(sv, pedido_id, {"por_confirmar"}, "confirmado")
    except ValueError:
        if any(x["id"] == pedido_id and x["estado"] == "caducado" for x in sv.listar("pedido")):
            raise ValueError("Su pedido caducó; cree uno nuevo.")
        raise
    _aviso(sv, p, pedido_id, f"Pedido #{pedido_id} confirmado de {_una_linea(p['nombre'])} ({p['modalidad']}), total {dinero(p['total_c'])}.")
    return {"ok": True, "estado": "confirmado", "total": dinero(p["total_c"]), "pago": {k: v for k, v in (sv.cfg.get("pago") or {}).items() if v is not None}}


def reportar_pago(sv, pedido_id, metodo, referencia):
    metodo, referencia = _txt(metodo, "metodo"), _txt(referencia, "referencia")
    p = _transicion(sv, pedido_id, {"confirmado"}, "pago_reportado", {"metodo": metodo, "referencia": referencia})
    _aviso(sv, p, pedido_id, f"Verificar Yappy/transferencia ref {_una_linea(referencia)} ({_una_linea(metodo)}) y marcar pagado: pedido #{pedido_id}, total {dinero(p['total_c'])}.")
    return {"ok": True, "estado": "pago_reportado", "nota": "Pago pendiente de verificación por el restaurante. No lo des por pagado ni prometas tiempo de preparación o entrega hasta que el equipo lo verifique."}


# --- comprobante de Yappy (foto): solo para verificar el pago de ese pedido; el modelo nunca la ve ---
NOMBRE_COMPROBANTE = re.compile(r"[0-9a-f]{32}\.(jpg|png)")
PNG_SIG, PNG_QUITAR = b"\x89PNG\r\n\x1a\n", {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def tipo_imagen(datos):
    """'jpg' | 'png' según la cabecera mágica, o None."""
    return "jpg" if datos[:3] == b"\xff\xd8\xff" else "png" if datos[:8] == PNG_SIG else None


def limpiar_imagen(datos, ext):
    """Quita metadatos (EXIF/texto/fecha) sin recortar ni recomprimir. ValueError('Imagen no válida') si la estructura está corrupta."""
    malo = ValueError("Imagen no válida")
    out, i, n = bytearray(), 8 if ext == "png" else 2, len(datos)
    if ext == "png":
        out += PNG_SIG
        while i + 12 <= n:
            largo, tipo = struct.unpack(">I4s", datos[i:i + 8])
            fin = i + 12 + largo
            if fin > n or (len(out) == 8 and tipo != b"IHDR"):
                raise malo
            if tipo not in PNG_QUITAR:
                out += datos[i:fin]
            i = fin
            if tipo == b"IEND":
                return bytes(out)  # lo que haya después de IEND se descarta
        raise malo
    out += datos[:2]
    while i + 4 <= n:
        if datos[i] != 0xFF:
            raise malo
        m = datos[i + 1]
        if m == 0xFF:  # relleno
            i += 1
            continue
        largo = struct.unpack(">H", datos[i + 2:i + 4])[0]
        if largo < 2 or i + 2 + largo > n:
            raise malo
        if m == 0xDA:  # SOS: el resto (datos comprimidos + EOI) se copia tal cual
            return bytes(out + datos[i:])
        if not (0xE1 <= m <= 0xEF or m == 0xFE):  # fuera APP1..APP15 y COM
            out += datos[i:i + 2 + largo]
        i += 2 + largo
    raise malo


def _borrar_archivo(sv, nombre):
    if isinstance(nombre, str) and NOMBRE_COMPROBANTE.fullmatch(nombre):
        try:
            (sv.dir / "comprobantes" / nombre).unlink()
        except FileNotFoundError:
            pass


def borrar_comprobante(sv, p):
    """Borra el archivo del pedido `p` y deja solo la fecha de borrado."""
    if p.get("comprobante"):
        _borrar_archivo(sv, p["comprobante"])
        sv.actualizar(p["id"], {"comprobante": "", "comprobante_borrado_at": sv.ahora().isoformat()}, tipo="pedido")


def purgar_comprobantes(sv):
    """Borra comprobantes con más de comprobantes.max_dias (7) y archivos huérfanos (sin pedido, con más de 10 min por si se está subiendo uno)."""
    dias = (sv.cfg.get("comprobantes") or {}).get("max_dias", 7)
    limite = sv.ahora() - dt.timedelta(days=dias)
    for p in sv.listar("pedido"):
        if p.get("comprobante") and dt.datetime.fromisoformat(p["comprobante_at"]) <= limite:
            borrar_comprobante(sv, p)
    carpeta = sv.dir / "comprobantes"
    en_uso = {p.get("comprobante") for p in sv.listar("pedido")}
    for f in carpeta.iterdir() if carpeta.is_dir() else ():
        if f.name not in en_uso and time.time() - f.stat().st_mtime > 600:
            f.unlink(missing_ok=True)


def subir_comprobante(sv, p, ext, datos, referencia=""):
    """Guarda la imagen ya limpia con nombre aleatorio y pasa el pedido `p` (confirmado o pago_reportado) a pago_reportado. ValueError si cambió de estado."""
    carpeta = sv.dir / "comprobantes"
    carpeta.mkdir(exist_ok=True)
    nombre = f"{secrets.token_hex(16)}.{ext}"
    (carpeta / nombre).write_bytes(datos)
    try:
        nuevo = _transicion(sv, p["id"], {"confirmado", "pago_reportado"}, "pago_reportado",
                            {"metodo": "yappy", "referencia": referencia or "comprobante", "comprobante": nombre, "comprobante_at": sv.ahora().isoformat()})
    except ValueError:
        _borrar_archivo(sv, nombre)
        raise
    if p.get("comprobante"):
        _borrar_archivo(sv, p["comprobante"])
    _aviso(sv, nuevo, p["id"], f"Comprobante de Yappy recibido: verificar y marcar pagado: pedido #{p['id']}, total {dinero(nuevo['total_c'])}.")
    return nuevo


def cancelar_pedido(sv, pedido_id):
    caducar_pedidos(sv)
    borrar_comprobante(sv, _transicion(sv, pedido_id, {"por_confirmar", "confirmado"}, "cancelado"))
    return {"ok": True}


PREVIOS = {"pagado": {"pago_reportado", "confirmado"}, "en_preparacion": {"pagado"}, "listo": {"en_preparacion"}, "entregado": {"listo"}}


def marcar_pedido(sv, pedido_id, estado):
    """Uso del restaurante (comando `pedidos`), no del modelo."""
    if estado not in PREVIOS:
        raise ValueError("Estado inválido: pagado, en_preparacion, listo o entregado.")
    p = _transicion(sv, pedido_id, PREVIOS[estado], estado)  # el pago va antes de preparar; un cancelado no está en ningún conjunto
    if (sv.cfg.get("comprobantes") or {}).get("borrar_al_marcar", True):
        borrar_comprobante(sv, p)
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
    "crear_pedido": (crear_pedido, "Crea el pedido con los ids del menú (el código calcula el total) y devuelve el resumen para leerlo al cliente tal cual. Llámala solo con TODOS los datos (nombre real, modalidad, dirección y zona si es entrega). Solo hay un pedido por confirmar por cliente: para añadir, quitar o cambiar algo antes de confirmar, vuelve a llamarla con la lista COMPLETA de ítems y datos; eso modifica el mismo pedido (actualizado: true).", _s({"items": {"type": "array", "items": _s({"id": S, "cantidad": {"type": "integer"}, "nota": S}, ["id", "cantidad"])}, "modalidad": {"type": "string", "enum": ["entrega", "recogida"]}, "nombre": S, "direccion": S, "hora_deseada": S, "zona": S}, ["items", "modalidad", "nombre"])),
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
CON_ID = {"cancelar_cita": ("cita_id", "cita"), "actualizar_registro": ("id", None), "confirmar_pedido": ("pedido_id", "pedido"),
          "reportar_pago": ("pedido_id", "pedido"), "cancelar_pedido": ("pedido_id", "pedido")}  # herramienta -> (clave del id, tipo esperado)
INMUTABLES = {"telefono", "fecha_hora", "cita_id"}


def ejecutar(sv, nombre, entrada, cliente, turno=None):
    """Ejecuta una herramienta para el cliente que escribe: nunca lee ni cambia datos de otro cliente.
    `turno`: {"texto": mensaje del cliente, "creados": set de ids} del bucle; None = llamada directa, sin salvaguardas de turno."""
    if nombre not in sv.cfg.get("herramientas", []):
        raise ValueError(f"Herramienta no permitida: {nombre}")
    entrada = dict(entrada)
    if nombre in CON_TELEFONO:
        entrada["telefono"] = cliente
    if nombre in CON_ID:
        clave, tipo = CON_ID[nombre]
        fila = sv._q("SELECT tipo, datos FROM registros WHERE id=?", (entrada.get(clave),))
        if not fila or json.loads(fila[0][1]).get("telefono") != cliente or tipo and fila[0][0] != tipo:
            raise ValueError("No existe ese registro para este cliente.")
        if nombre == "actualizar_registro" and fila[0][0] in ("pedido", "aviso"):
            raise ValueError("Los pedidos no se cambian con esta herramienta; usa las herramientas de pedido.")
        if INMUTABLES & set(entrada.get("cambios", {})):
            raise ValueError("No se puede cambiar teléfono, fecha_hora ni cita_id; para mover una cita, cancélala y agenda otra.")
    if turno is not None:
        if nombre == "confirmar_pedido" and entrada.get("pedido_id") in turno["creados"]:
            raise ValueError("Primero lee el resumen al cliente y espera su 'sí' en un mensaje nuevo.")
        if nombre == "reportar_pago" and "".join(str(entrada.get("referencia", "")).split()).lower() not in "".join(turno["texto"].split()).lower():
            raise ValueError("Pide al cliente el número de referencia del pago; no lo inventes.")
    res = CATALOGO[nombre][0](sv, **entrada)
    if turno is not None and nombre == "crear_pedido":
        turno["creados"].add(res["pedido_id"])
    return res
