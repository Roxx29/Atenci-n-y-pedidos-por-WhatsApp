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

from . import ubicacion

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
    out = {k: v for k, v in p.items() if not k.endswith("_c") and k not in ("lineas", "comprobante", "comprobante_at", "comprobante_borrado_at", "pago_rechazado_at", "ubicacion")}
    out.update(ubicacion_recibida=bool(p.get("ubicacion")), total=dinero(p["total_c"]), subtotal=dinero(p["subtotal_c"]), impuesto=dinero(p["itbms_c"]), envio=dinero(p["envio_c"]),
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
ESTADOS_PEDIDO = ["por_confirmar", "confirmado", "pago_reportado", "pagado", "en_preparacion", "listo", "en_camino", "entregado"]


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


def _transicion(sv, pedido_id, desde, hacia, extra=None, antes=None):
    """Cambio de estado atómico (también entre procesos): ver Servicio.actualizar."""
    return sv.actualizar(pedido_id, {"estado": hacia, **(extra or {})}, tipo="pedido", desde=desde, antes=antes)


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
                _transicion(sv, p["id"], {"por_confirmar"}, "caducado", {"ubicacion": None})
                ids.append(p["id"])
            except ValueError:  # otro proceso lo confirmó o canceló justo ahora
                pass
    return ids


def _envio(cfg, zona):
    """Costo de envío en centavos: fijo, o por zona (lista = zonas permitidas a costo fijo, dict = zona -> costo).
    `alias: {zona: [corregimientos o barrios]}` hace que esos nombres cuenten como esa zona."""
    e = cfg.get("envio") or {}
    zonas = e.get("zonas")
    if not zonas:
        return _c(e.get("costo", 0))
    por_zona = {_sin_tildes(z): e.get("costo", 0) for z in zonas} if isinstance(zonas, list) else {_sin_tildes(z): c for z, c in zonas.items()}
    for canon, nombres in (e.get("alias") or {}).items():
        if _sin_tildes(canon) in por_zona:
            for n in nombres:
                por_zona.setdefault(_sin_tildes(n), por_zona[_sin_tildes(canon)])
    if not (zona or "").strip():
        raise ValueError("Falta la zona de entrega: pregúntasela al cliente (" + ", ".join(por_zona) + "); no inventes datos.")
    if _sin_tildes(zona) not in por_zona:
        raise ValueError("No reconozco esa zona: pregunta al cliente a qué zona o corregimiento pertenece; solo si claramente no es de las zonas de entrega, ofrece recogida.")
    return _c(por_zona[_sin_tildes(zona)])


ACTIVOS = {"por_confirmar", "confirmado", "pago_reportado", "pagado", "en_preparacion"}
MAX_ACTIVOS = 3
YA_PAGADO = {"pago_reportado", "pagado", "en_preparacion"}


def _lineas(sv, items):
    """Valida los ítems contra el menú: (lineas, subtotal_c)."""
    if not isinstance(items, list) or not items:
        raise ValueError("El pedido no tiene ítems (debe ser una lista de ítems).")
    if len(items) > MAX_LINEAS:
        raise ValueError(f"Máximo {MAX_LINEAS} líneas por pedido.")
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
    return lineas, subtotal


def _armar(sv, lineas, subtotal, modalidad, nombre, telefono, direccion, hora_deseada, zona, notas, validar_hora=True):
    """Valida y calcula TODO el pedido (compartido por crear y modificar): dict de campos listo para guardar."""
    nombre, direccion, hora_deseada, zona, notas = _txt(nombre, "nombre"), _txt(direccion, "direccion"), _txt(hora_deseada, "hora_deseada"), _txt(zona, "zona"), _txt(notas, "notas")
    _nombre_real(nombre, telefono)
    if modalidad not in ("entrega", "recogida"):
        raise ValueError("Modalidad inválida: debe ser 'entrega' o 'recogida'; pregúntasela al cliente, no inventes datos.")
    if modalidad == "entrega" and not direccion.strip():
        raise ValueError("Falta la dirección de entrega: pregúntasela al cliente; no inventes datos.")
    cfg = sv.cfg.get("pedidos") or {}
    if validar_hora:  # ponytail: modificar solo revalida la hora si el cliente da una nueva; la guardada no se vuelve a juzgar
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
    return {"nombre": nombre, "telefono": telefono, "modalidad": modalidad, "direccion": direccion, "hora_deseada": hora_deseada, "zona": zona, "notas": notas,
            "lineas": lineas, "subtotal_c": subtotal, "itbms_c": itbms, "envio_c": envio, "total_c": subtotal + itbms + envio}


def _resumen(d):
    return "\n".join([f"{l['cantidad']} x {l['nombre']}: {dinero(l['importe_c'])}" + (f" ({l['nota']})" if l["nota"] else "") for l in d["lineas"]]
                     + [f"Subtotal: {dinero(d['subtotal_c'])}"] + ([f"ITBMS: {dinero(d['itbms_c'])}"] if d["itbms_c"] else [])
                     + ([f"Envío: {dinero(d['envio_c'])}"] if d["modalidad"] == "entrega" else []) + [f"Total: {dinero(d['total_c'])}"]
                     + ([f"Notas: {d['notas']}"] if d.get("notas") else []))


def crear_pedido(sv, items, modalidad, nombre, telefono, direccion="", hora_deseada="", zona="", notas="", aparte=False):
    lineas, subtotal = _lineas(sv, items)
    datos = _armar(sv, lineas, subtotal, modalidad, nombre, telefono, direccion, hora_deseada, zona, notas)
    datos.update(estado="por_confirmar", creado_at=sv.ahora().isoformat())
    with sv.pedidos_lock:  # ponytail: lock del proceso; entre procesos basta porque solo el bot crea pedidos
        caducar_pedidos(sv)
        mios = [p for p in sv.listar("pedido") if p["telefono"] == telefono and p["estado"] in ACTIVOS]
        previo = next((p for p in mios if p["estado"] == "por_confirmar"), None)  # un solo pedido en construcción por cliente
        if aparte and previo:
            raise ValueError(f"Confirme o cancele primero el pedido #{previo['id']}.")
        if aparte and len(mios) >= MAX_ACTIVOS:
            raise ValueError(f"Ya tiene {MAX_ACTIVOS} pedidos en curso; no se pueden crear más hasta que termine alguno.")
        if not aparte and not previo and mios:
            o = mios[-1]
            raise ValueError(f"Ya tiene el pedido #{o['id']} en curso (estado {o['estado']}). Para cambiarlo use modificar_pedido; solo si el cliente pide explícitamente OTRO pedido aparte vuelva a llamar con aparte=true.")
        if previo:
            pid = previo["id"]
            sv.actualizar(pid, datos, tipo="pedido", desde={"por_confirmar"})
        else:
            pid = sv.insertar("pedido", datos)
    return {"ok": True, "pedido_id": pid, "resumen": _resumen(datos), "total": dinero(datos["total_c"]), "actualizado": bool(previo)}


def _norm_items(items):
    return sorted((str(i.get("id")), i.get("cantidad"), i.get("nota") or "") for i in items)


def modificar_pedido(sv, pedido_id, items=None, modalidad=None, direccion=None, zona=None, hora_deseada=None, notas=None):
    with sv.pedidos_lock:
        caducar_pedidos(sv)
        p = next((x for x in sv.listar("pedido") if x["id"] == pedido_id), None)
        if not p:
            raise ValueError(f"No existe el pedido {pedido_id}.")
        est = p["estado"]
        if est not in ACTIVOS:
            raise ValueError("Este pedido ya no se puede modificar.")
        previas = [{"id": l["id"], "cantidad": l["cantidad"], "nota": l["nota"]} for l in p["lineas"]]
        c_items = items is not None and not (isinstance(items, list) and all(isinstance(i, dict) for i in items) and _norm_items(items) == _norm_items(previas))
        mod = p["modalidad"] if modalidad is None else modalidad
        c_mod = mod != p["modalidad"]
        c_zona = mod == "entrega" and zona is not None and _sin_tildes(zona) != _sin_tildes(p["zona"])
        if mod == "recogida" and direccion not in (None, ""):
            raise ValueError("Es un pedido de recogida: no lleva dirección. Si el cliente quiere entrega, cambie la modalidad.")
        if est in YA_PAGADO and (c_items or c_mod or c_zona):
            return _escalar_cambio(sv, p, pedido_id)
        lineas, subtotal = _lineas(sv, items) if c_items else (p["lineas"], p["subtotal_c"])
        pick = lambda nuevo, viejo: viejo if nuevo is None else nuevo
        datos = _armar(sv, lineas, subtotal, mod, p["nombre"], p["telefono"], pick(direccion, p["direccion"]), pick(hora_deseada, p["hora_deseada"]), pick(zona, p["zona"]),
                       pick(notas, p.get("notas", "")), validar_hora=hora_deseada is not None)
        cambio_total = datos["total_c"] != p["total_c"]
        if est in YA_PAGADO and cambio_total:
            return _escalar_cambio(sv, p, pedido_id)
        reconfirmar = est == "confirmado" and cambio_total
        if reconfirmar or est == "por_confirmar":
            datos.update(estado="por_confirmar", creado_at=sv.ahora().isoformat())
        sv.actualizar(pedido_id, datos, tipo="pedido", desde={est})
        que = [t for ok, t in ((c_items, "los ítems"), (c_mod, f"la modalidad a {mod}"), (c_zona, f"la zona a {datos['zona']}"),
                               (datos["direccion"] != p["direccion"], f"la dirección a {datos['direccion']}"),
                               (datos["hora_deseada"] != p["hora_deseada"], f"la hora a {datos['hora_deseada']}"),
                               (datos["notas"] != p.get("notas", ""), f"las notas a {datos['notas']}")) if ok]
        if que and not reconfirmar and est != "por_confirmar":
            _aviso(sv, p, pedido_id, _una_linea(f"Pedido #{pedido_id}: el cliente cambió " + " y ".join(que))[:MAX_TEXTO])
    return {"ok": True, "pedido_id": pedido_id, "estado": datos.get("estado", est), "resumen": _resumen(datos), "total": dinero(datos["total_c"]), "reconfirmar": reconfirmar}


def _escalar_cambio(sv, p, pedido_id):
    sv.insertar("escalado", {"telefono": p["telefono"], "motivo": f"Cambio de ítems/zona/modalidad en pedido #{pedido_id} ya pagado o en curso", "estado": "abierto"})
    _aviso(sv, p, pedido_id, f"Pedido #{pedido_id} ya pagado o en curso: el cliente pide cambiar ítems, zona o modalidad; contactarlo.")
    return {"ok": False, "escalado": True, "nota": "No puedo cambiar eso en un pedido ya pagado; una persona del equipo lo contactará."}


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
    """Quita metadatos (EXIF/XMP/IPTC/texto/fecha) sin recortar ni recomprimir; en JPEG limpia la cabecera, no los segmentos entre escaneos. ValueError('Imagen no válida') si la estructura está corrupta."""
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
    while i + 2 <= n:
        if datos[i] != 0xFF:
            raise malo
        m = datos[i + 1]
        if m == 0xFF:  # relleno
            i += 1
            continue
        if m == 0x01 or 0xD0 <= m <= 0xD8:  # sin longitud
            out += datos[i:i + 2]
            i += 2
            continue
        if i + 4 > n:
            raise malo
        largo = struct.unpack(">H", datos[i + 2:i + 4])[0]
        if largo < 2 or i + 2 + largo > n:
            raise malo
        if m == 0xDA:  # SOS: datos comprimidos tal cual, hasta el último EOI (lo posterior se descarta)
            fin = datos.rfind(b"\xff\xd9")
            if fin < i:
                raise malo
            return bytes(out + datos[i:fin + 2])
        if not (0xE1 <= m <= 0xEF and m not in (0xE2, 0xEE) or m == 0xFE):  # se conservan APP0, APP2 (ICC) y APP14 (Adobe)
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
    tope = sv.ahora() - dt.timedelta(hours=24)  # la ubicación no vive más de 24 h desde la creación del pedido
    for p in sv.listar("pedido"):
        if p.get("ubicacion") and p.get("creado_at") and dt.datetime.fromisoformat(p["creado_at"]) <= tope:
            sv.actualizar(p["id"], {"ubicacion": None}, tipo="pedido")
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
    antes = {}
    try:
        nuevo = _transicion(sv, p["id"], {"confirmado", "pago_reportado"}, "pago_reportado",
                            {"metodo": "yappy", "referencia": referencia or "comprobante", "comprobante": nombre, "comprobante_at": sv.ahora().isoformat()}, antes)
    except ValueError:
        _borrar_archivo(sv, nombre)
        raise
    _borrar_archivo(sv, antes.get("comprobante"))  # el anterior según la base, no el `p` posiblemente obsoleto
    _aviso(sv, nuevo, p["id"], f"Comprobante de Yappy recibido: verificar y marcar pagado: pedido #{p['id']}, total {dinero(nuevo['total_c'])}.")
    return nuevo


def cancelar_pedido(sv, pedido_id):
    caducar_pedidos(sv)
    borrar_comprobante(sv, _transicion(sv, pedido_id, {"por_confirmar", "confirmado"}, "cancelado", {"ubicacion": None}))
    return {"ok": True}


PREVIOS = {"pagado": {"pago_reportado", "confirmado"}, "en_preparacion": {"pagado"}, "listo": {"en_preparacion"}, "en_camino": {"listo"}, "entregado": {"listo", "en_camino"}}


def marcar_pedido(sv, pedido_id, estado):
    """Uso del restaurante (comando `pedidos`), no del modelo."""
    if estado == "pago_rechazado":  # el pago no se pudo verificar: vuelve a confirmado para que el cliente pague de nuevo
        p = _transicion(sv, pedido_id, {"pago_reportado"}, "confirmado", {"pago_rechazado_at": sv.ahora().isoformat()})
        borrar_comprobante(sv, p)
        programar_mensaje(sv, p["telefono"], f"{sv.ahora():%Y-%m-%d %H:%M}", f"No pudimos verificar su pago del pedido #{pedido_id}. Revise los datos y envíe de nuevo su comprobante o la referencia.")
        _aviso(sv, p, pedido_id, f"Pago no válido del pedido #{pedido_id}: avisado el cliente.")
        return {"ok": True, "estado": "confirmado"}
    if estado not in PREVIOS:
        raise ValueError("Estado inválido: pagado, en_preparacion, listo, en_camino, entregado o pago_rechazado.")
    p = _transicion(sv, pedido_id, PREVIOS[estado], estado, {"ubicacion": None} if estado == "entregado" else None)  # el pago va antes de preparar; un cancelado no está en ningún conjunto
    if (sv.cfg.get("comprobantes") or {}).get("borrar_al_marcar", True):
        borrar_comprobante(sv, p)
    if estado == "listo":
        programar_mensaje(sv, p["telefono"], f"{sv.ahora():%Y-%m-%d %H:%M}", f"Hola {p['nombre']}, su pedido #{pedido_id} está listo.")
    if estado == "en_camino":
        programar_mensaje(sv, p["telefono"], f"{sv.ahora():%Y-%m-%d %H:%M}", f"Hola {p['nombre']}, su pedido #{pedido_id} va en camino.")
    return {"ok": True, "estado": estado}


ABIERTOS = {"por_confirmar", "confirmado", "pago_reportado", "pagado", "en_preparacion", "listo", "en_camino"}


def poner_ubicacion(sv, pedido_id, lat, lon, fuente, enlace=None, precision=None):
    """Guarda en el pedido abierto la ubicación (rango validado). Sin coordenadas basta un enlace de Google. Fuera del radio solo se marca
    `fuera_de_zona`: no se bloquea. Nunca la imprime ni la audita."""
    if lat is None and lon is None:
        if not enlace:
            raise ValueError("Ubicación no válida.")
        u = {"lat": None, "lon": None}
    else:
        lat, lon = ubicacion.validar(lat, lon)
        u = {"lat": lat, "lon": lon, "fuera_de_zona": ubicacion.fuera_de_zona(lat, lon, **ubicacion.zona(sv.cfg))}
    u.update(fuente=fuente, recibida_at=sv.ahora().isoformat())
    if enlace:
        u["enlace"] = str(enlace)[:300]  # tal cual lo pegó el cliente
    if isinstance(precision, (int, float)) and not isinstance(precision, bool) and 0 <= precision < 100000:
        u["precision"] = round(precision)
    return sv.actualizar(pedido_id, {"ubicacion": u}, tipo="pedido", desde=ABIERTOS)


def guardar_ubicacion(sv, pedido_id, enlace):
    """Herramienta del bot: solo acepta un enlace de Google Maps (o 'lat, lon') pegado por el cliente; las coordenadas las saca el código."""
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if not p or p["modalidad"] != "entrega":
        raise ValueError("La ubicación solo se guarda en pedidos de entrega.")
    lat, lon, url = ubicacion.desde_enlace(enlace)
    poner_ubicacion(sv, pedido_id, lat, lon, "enlace", url)
    return {"ok": True, "nota": "Ubicación guardada para el repartidor. No muestres ni repitas coordenadas."}


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
    "crear_pedido": (crear_pedido, "Crea el pedido con los ids del menú (el código calcula el total) y devuelve el resumen para leerlo al cliente tal cual. Llámala solo con TODOS los datos (nombre real, modalidad, dirección y zona si es entrega). Solo hay un pedido por confirmar por cliente y no puede tener otro pedido en curso (para cambiarlo usa modificar_pedido; aparte=true solo si pide OTRO pedido aparte): para añadir, quitar o cambiar algo antes de confirmar, vuelve a llamarla con la lista COMPLETA de ítems y datos; eso modifica el mismo pedido (actualizado: true).", _s({"items": {"type": "array", "items": _s({"id": S, "cantidad": {"type": "integer"}, "nota": S}, ["id", "cantidad"])}, "modalidad": {"type": "string", "enum": ["entrega", "recogida"]}, "nombre": S, "direccion": S, "hora_deseada": S, "zona": S, "notas": S, "aparte": {"type": "boolean", "description": "true SOLO si el cliente pide explícitamente OTRO pedido aparte teniendo ya uno en curso (máx. 3)"}}, ["items", "modalidad", "nombre"])),
    "modificar_pedido": (modificar_pedido, "Cambia un pedido existente (dirección, hora, notas, zona, modalidad o la lista COMPLETA de ítems); lo no indicado se conserva. Para cualquier cambio de un pedido existente usa esta herramienta, NUNCA crear_pedido. Si el total cambia en un pedido confirmado, vuelve a por_confirmar: lee el nuevo total y pide un sí en un mensaje nuevo antes de confirmar_pedido. En un pedido ya pagado solo se pueden cambiar dirección, hora y notas; lo demás se escala a una persona.", _s({"pedido_id": {"type": "integer"}, "items": {"type": "array", "items": _s({"id": S, "cantidad": {"type": "integer"}, "nota": S}, ["id", "cantidad"])}, "modalidad": {"type": "string", "enum": ["entrega", "recogida"]}, "direccion": S, "zona": S, "hora_deseada": S, "notas": S}, ["pedido_id"])),
    "confirmar_pedido": (confirmar_pedido, "Confirma un pedido por_confirmar tras un 'sí' explícito del cliente; devuelve los datos de pago.", _s({"pedido_id": {"type": "integer"}}, ["pedido_id"])),
    "reportar_pago": (reportar_pago, "Registra que el cliente dice haber pagado (método y referencia). No lo da por verificado.", _s({"pedido_id": {"type": "integer"}, "metodo": S, "referencia": S}, ["pedido_id", "metodo", "referencia"])),
    "guardar_ubicacion": (guardar_ubicacion, "Guarda la ubicación de entrega cuando el cliente pega un enlace de Google Maps (o 'lat, lon'). Pasa el enlace EXACTO que escribió; nunca inventes ni calcules coordenadas.", _s({"pedido_id": {"type": "integer"}, "enlace": S}, ["pedido_id", "enlace"])),
    "cancelar_pedido": (cancelar_pedido, "Cancela un pedido por_confirmar o confirmado.", _s({"pedido_id": {"type": "integer"}}, ["pedido_id"])),
}


def esquemas(nombres):
    faltan = [n for n in nombres if n not in CATALOGO]
    if faltan:
        raise ValueError(f"Herramientas desconocidas en servicio.yaml: {faltan}. Disponibles: {list(CATALOGO)}")
    return [{"name": n, "description": CATALOGO[n][1], "input_schema": CATALOGO[n][2]} for n in nombres]


CON_TELEFONO = {"agendar_cita", "programar_mensaje", "escalar_a_humano", "registrar", "buscar", "crear_pedido"}
CON_ID = {"cancelar_cita": ("cita_id", "cita"), "actualizar_registro": ("id", None), "confirmar_pedido": ("pedido_id", "pedido"),
          "reportar_pago": ("pedido_id", "pedido"), "cancelar_pedido": ("pedido_id", "pedido"), "modificar_pedido": ("pedido_id", "pedido"), "guardar_ubicacion": ("pedido_id", "pedido")}  # herramienta -> (clave del id, tipo esperado)
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
        if nombre == "guardar_ubicacion" and "".join(str(entrada.get("enlace", "")).split()) not in "".join(turno["texto"].split()):
            raise ValueError("Usa solo el enlace que el cliente escribió en este mensaje.")
        if nombre == "reportar_pago" and "".join(str(entrada.get("referencia", "")).split()).lower() not in "".join(turno["texto"].split()).lower():
            raise ValueError("Pide al cliente el número de referencia del pago; no lo inventes.")
    res = CATALOGO[nombre][0](sv, **entrada)
    if turno is not None and nombre == "crear_pedido":
        turno["creados"].add(res["pedido_id"])
    if turno is not None and nombre == "modificar_pedido" and res.get("reconfirmar"):
        turno["creados"].add(res["pedido_id"])  # el cliente debe ver el nuevo total y decir sí en otro mensaje
    return res
