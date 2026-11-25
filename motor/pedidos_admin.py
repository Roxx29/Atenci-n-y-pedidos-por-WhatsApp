"""Gestión de pedidos por el dueño: editar un pedido abierto (precios siempre del servidor) y exportar a CSV
pedidos y resumen de facturas de un rango de fechas (cierre de período). Solo dinero en centavos enteros."""
import csv
import datetime as dt
import io
import re

from . import factura, herramientas

ABIERTOS = herramientas.ABIERTOS - {"en_camino"}  # un pedido en camino ya salió: se corrige el estado, no los ítems
MAX_RANGO = 366
FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")


def editar(sv, quien, d):
    """POST /panel/dueno/pedido_editar {pedido_id, items?, notas?, estado?}. Recalcula subtotal/ITBMS/envío/total con el menú del servidor
    (las líneas que ya estaban conservan su precio); el estado pasa por panel_web.marcar (mismas reglas). ValueError legible si no procede."""
    from . import panel_web  # tardío: panel_web importa este módulo
    pid = d.get("pedido_id")
    p = next((x for x in sv.listar("pedido") if x["id"] == pid), None) if isinstance(pid, int) and not isinstance(pid, bool) else None
    if not p:
        raise ValueError("No existe ese pedido")
    items, notas, estado = d.get("items"), d.get("notas"), d.get("estado")
    if items is None and notas is None and estado is None:
        raise ValueError("Nada que cambiar")
    que = []
    if items is not None or notas is not None:
        if p["estado"] not in ABIERTOS:
            raise ValueError("Este pedido ya no se puede editar")
        with sv.pedidos_lock:
            lineas, subtotal = (p["lineas"], p["subtotal_c"])
            if items is not None:
                lineas, subtotal = herramientas._lineas(sv, items)  # valida contra el menú: nunca un precio del cliente
                previo = {l["id"]: l["precio_c"] for l in p["lineas"]}
                for l in lineas:
                    l["precio_c"] = previo.get(l["id"], l["precio_c"])
                    l["importe_c"] = l["precio_c"] * l["cantidad"]
                subtotal = sum(l["importe_c"] for l in lineas)
                que.append("items")
            if notas is not None:
                que.append("notas")
            nuevo = herramientas._armar(sv, lineas, subtotal, p["modalidad"], p["nombre"], p["telefono"], p.get("direccion", ""), p.get("hora_deseada", ""), p.get("zona", ""),
                                        p.get("notas", "") if notas is None else notas, validar_hora=False)
            nuevo.pop("nombre", None)  # el nombre no se toca (en mesa se reasigna igual)
            sv.actualizar(pid, {**nuevo, "editado_por": quien, "editado_at": sv.ahora().isoformat()}, tipo="pedido", desde=ABIERTOS)
            panel_web.auditar(sv, quien, f"pedido_editar:{'+'.join(que)}:{herramientas.dinero(p['total_c'])}->{herramientas.dinero(nuevo['total_c'])}", pid)
    if estado is not None:
        panel_web.marcar(sv, pid, estado, quien)
        panel_web.auditar(sv, quien, f"pedido_estado:{estado}", pid)
    return {"ok": True, "pedido": panel_web.pedido_json(next(x for x in sv.listar("pedido") if x["id"] == pid), True)}


def _rango(d):
    v = [d.get(k) for k in ("desde", "hasta")]
    try:
        if not all(isinstance(x, str) and FECHA.fullmatch(x) for x in v):
            raise ValueError
        a, b = (dt.date.fromisoformat(x) for x in v)
    except ValueError:
        raise ValueError("Fechas inválidas: usa AAAA-MM-DD en desde y hasta")
    if a > b:
        raise ValueError("'desde' va antes que 'hasta'")
    if (b - a).days >= MAX_RANGO:
        raise ValueError(f"Rango máximo: {MAX_RANGO} días")
    return a.isoformat(), b.isoformat()


def _csv(cols, filas):
    o = io.StringIO(newline="")
    w = csv.writer(o, lineterminator="\r\n")
    w.writerow(cols)
    w.writerows([[factura._t(c) if isinstance(c, str) else c for c in f] for f in filas])
    return o.getvalue()  # sin BOM: el panel lo añade al descargar


def exportar(sv, quien, d):
    """POST /panel/dueno/exportar {tipo: pedidos|facturas, desde, hasta} → {nombre, csv}. Por fecha de creación del pedido.
    pedidos: todos (también cancelados) sin teléfono ni dirección. facturas: una fila por línea de las ventas (no cancelados/caducados/sin confirmar)."""
    tipo = d.get("tipo")
    if tipo not in ("pedidos", "facturas"):
        raise ValueError("Tipo inválido: pedidos o facturas")
    a, b = _rango(d)
    ps = [p for p in sv.listar("pedido") if a <= (p.get("creado_at") or "")[:10] <= b]
    if tipo == "pedidos":
        cols = ["pedido_id", "creado", "estado", "estado_actualizado", "pagado", "entregado", "modalidad", "mesa", "zona", "nombre", "metodo_pago", "referencia", "por_cobrar", "cobrado_por",
                "repartidor", "subtotal", "itbms", "envio", "total", "items", "notas"]
        f = lambda p: [p["id"], p.get("creado_at"), p["estado"], p.get("estado_at"), p.get("pagado_at"), p.get("entregado_at"), p.get("modalidad"), p.get("mesa"), p.get("zona"), p.get("nombre"),
                       p.get("metodo"), p.get("referencia"), "si" if p.get("por_cobrar") else "no", p.get("cobrado_por"), p.get("repartidor"), factura._d(p["subtotal_c"]), factura._d(p["itbms_c"]),
                       factura._d(p["envio_c"]), factura._d(p["total_c"]), "; ".join(f"{l['cantidad']} x {l['nombre']}" + (f" ({l['nota']})" if l.get("nota") else "") for l in p["lineas"]), p.get("notas")]
        filas = [f(p) for p in ps]
    else:
        cols = ["pedido_id", "fecha", "modalidad", "receptor", "descripcion", "cantidad", "precio_unitario", "base", "tasa_itbms", "itbms", "total", "pagado", "forma_pago", "coincide_con_pedido"]
        filas = []
        for p in ps:
            if p["estado"] in factura.NO_VENTA:
                continue
            r = factura.resumen(sv, p)
            t, doc = r["totales"], r["documento"]
            filas += [[p["id"], doc["fecha"], doc["modalidad"], "consumidor_final", l["descripcion"], l["cantidad"], l["precio_unitario"], l["base"], l["tasa_itbms"], l["itbms"], l["total"],
                       "si" if t["pagado"] else "no", t["forma_pago"], "si" if r["coincide"] else "no"] for l in r["lineas"] + ([r["envio"]] if r["envio"] else [])]
    from . import panel_web
    panel_web.auditar(sv, quien, f"exportar_{tipo}:{a}..{b}")
    return {"nombre": f"{tipo}-{a}-a-{b}.csv", "csv": _csv(cols, filas), "filas": len(filas)}
