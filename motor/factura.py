"""Resumen para factura: los datos de un pedido ordenados como los pide una factura, para teclearlos en la caja fiscal o el PAC del restaurante.
NO es una factura fiscal y la app no emite nada a nombre de nadie. Todo el dinero son centavos enteros; los importes salen como texto "12.34"."""
import csv
import datetime as dt
import html
import io
import re
from decimal import Decimal

ROTULO = "Resumen para factura — no es una factura fiscal"
AVISO = "Resumen para facturar: no es una factura fiscal. Emítala en su caja fiscal o con su proveedor (PAC)."
NO_VENTA = {"cancelado", "caducado", "por_confirmar"}
RUC = re.compile(r"[A-Za-z0-9-]{1,20}")
DV = re.compile(r"\d{1,3}")


def _d(c):
    return f"{c // 100}.{c % 100:02d}"


def _pct(x):
    try:
        v = Decimal(str(x if x is not None else 0))
    except Exception:
        return Decimal(0)
    return v if 0 <= v <= 100 else Decimal(0)


def _num(v):
    return str(v.normalize()) if v else "0"


def _reparto(total, pesos):
    """Reparte `total` centavos en proporción a `pesos` (enteros) sin perder ni inventar centavos (mayor resto)."""
    s = sum(pesos)
    if not s:
        return [0] * len(pesos)
    base = [total * w // s for w in pesos]
    falta = total - sum(base)
    for i in sorted(range(len(pesos)), key=lambda i: -(total * pesos[i] % s))[:falta]:
        base[i] += 1
    return base


def receptor(d=None):
    """Valida lo que teclea el personal. ValueError legible si no vale. Sin datos = consumidor final."""
    d = d or {}
    if not isinstance(d, dict):
        raise ValueError("Receptor inválido")
    tipo = d.get("tipo") or ("contribuyente" if d.get("ruc") else "consumidor_final")
    if tipo not in ("consumidor_final", "contribuyente"):
        raise ValueError("Tipo de receptor inválido")
    if tipo == "consumidor_final":
        return {"tipo": tipo, "ruc": "", "dv": "", "nombre": ""}
    ruc, dv, nombre = d.get("ruc") or "", d.get("dv") or "", d.get("nombre") or ""
    if not (isinstance(ruc, str) and RUC.fullmatch(ruc.strip())):
        raise ValueError("RUC o cédula: solo letras, números y guiones (máx. 20)")
    if not (isinstance(dv, str) and (not dv.strip() or DV.fullmatch(dv.strip()))):
        raise ValueError("DV inválido (hasta 3 números)")
    if not (isinstance(nombre, str) and len(nombre.strip()) <= 80 and nombre.isprintable()):
        raise ValueError("Nombre del cliente: máximo 80 letras")
    return {"tipo": tipo, "ruc": ruc.strip(), "dv": dv.strip(), "nombre": " ".join(nombre.split())}


def resumen(sv, pedido, rec=None):
    cfg = sv.cfg
    ped = cfg.get("pedidos") or {}
    tasa = _pct(ped.get("impuesto_pct"))
    tasa_envio = _pct((cfg.get("factura") or {}).get("envio_itbms_pct", ped.get("impuesto_pct")))
    lineas = pedido["lineas"]
    itbms_l = _reparto(pedido["itbms_c"], [l["importe_c"] for l in lineas])  # el motor calcula el ITBMS sobre el subtotal: se reparte por línea
    filas = [{"descripcion": l["nombre"] + (f" ({l['nota']})" if l.get("nota") else ""), "cantidad": l["cantidad"], "precio_unitario": _d(l["precio_c"]),
              "base": _d(l["importe_c"]), "tasa_itbms": _num(tasa if i else 0), "itbms": _d(i), "total": _d(l["importe_c"] + i)} for l, i in zip(lineas, itbms_l)]
    envio = pedido.get("envio_c") or 0
    # ponytail: el envío se toma con el ITBMS incluido en lo cobrado (el motor no se lo suma); por confirmar con su contador
    i_env = int(Decimal(envio) * tasa_envio / (100 + tasa_envio) + Decimal("0.5")) if envio else 0
    env = {"descripcion": "Envío a domicilio", "cantidad": 1, "precio_unitario": _d(envio), "base": _d(envio - i_env), "tasa_itbms": _num(tasa_envio),
           "itbms": _d(i_env), "total": _d(envio), "nota": "ITBMS del envío por confirmar con su contador"} if envio else None
    suma = sum(l["importe_c"] for l in lineas) + sum(itbms_l) + envio
    creado = dt.datetime.fromisoformat(pedido["creado_at"]).astimezone(sv.tz) if pedido.get("creado_at") else None
    emisor = {"razon_social": cfg.get("razon_social") or "", "ruc": cfg.get("ruc") or "", "dv": cfg.get("dv") or "", "sucursal": cfg.get("sucursal") or "",
              "direccion": cfg.get("direccion") or "", "telefono": cfg.get("telefono") or ""}
    return {"aviso": AVISO, "emisor": emisor, "faltan": [k for k in ("razon_social", "ruc", "dv", "sucursal") if not emisor[k]],
            "documento": {"pedido_id": pedido["id"], "fecha": f"{creado:%Y-%m-%d %H:%M}" if creado else "",
                          "modalidad": {"recogida": "recoger"}.get(pedido["modalidad"], pedido["modalidad"])},
            "receptor": receptor(rec), "lineas": filas, "envio": env,
            "totales": {"subtotal": _d(pedido["subtotal_c"]), "itbms": _d(sum(itbms_l) + i_env), "envio": _d(envio), "total": _d(suma),
                        "pagado": bool(pedido.get("pagado_at") or pedido.get("cobrado_at")), "forma_pago": pedido.get("metodo") or ""},
            "coincide": suma == pedido["total_c"] and pedido["subtotal_c"] == sum(l["importe_c"] for l in lineas)}


def _t(x):
    """Texto de CSV: neutraliza fórmulas (=, +, -, @ al inicio) para que Excel no las ejecute."""
    x = str(x)
    return "'" + x if x[:1] in ("=", "+", "-", "@", "\t", "\r") else x


def to_csv(r):
    o = io.StringIO(newline="")
    w = csv.writer(o, lineterminator="\r\n")
    w.writerow([ROTULO])
    for sec in ("emisor", "documento", "receptor"):
        for k, v in r[sec].items():
            w.writerow([f"{sec}_{k}", _t(v)])
    w.writerow([])
    cols = ["descripcion", "cantidad", "precio_unitario", "base", "tasa_itbms", "itbms", "total"]
    w.writerow(cols)
    for l in r["lineas"] + ([r["envio"]] if r["envio"] else []):
        w.writerow([_t(l[c]) if c == "descripcion" else l[c] for c in cols])
    w.writerow([])
    for k, v in r["totales"].items():
        w.writerow([f"total_{k}", _t(v)])
    w.writerow(["coincide_con_pedido", "si" if r["coincide"] else "no"])
    return b"\xef\xbb\xbf" + o.getvalue().encode("utf-8")  # BOM: Excel abre bien las tildes


def to_html(r):
    e = lambda x: html.escape(str(x))
    fila = lambda l: "<tr>" + "".join(f"<td{'' if c == 'descripcion' else ' class=n'}>{e(l[c])}</td>" for c in ("descripcion", "cantidad", "precio_unitario", "base", "tasa_itbms", "itbms", "total")) + "</tr>"
    em, rc, d, t = r["emisor"], r["receptor"], r["documento"], r["totales"]
    rec = "Consumidor final" if rc["tipo"] == "consumidor_final" else f"{rc['nombre']} · RUC/cédula {rc['ruc']}" + (f"-{rc['dv']}" if rc["dv"] else "")
    falta = f"<p class=al>Faltan datos del emisor: {e(', '.join(r['faltan']))}.</p>" if r["faltan"] else ""
    nota = f"<p><small>{e(r['envio']['nota'])}</small></p>" if r["envio"] else ""
    ok = "" if r["coincide"] else "<p class=al>El total del resumen no coincide con el del pedido: no lo use.</p>"
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(ROTULO)} #{e(d["pedido_id"])}</title>'
            "<style>body{font:15px/1.5 system-ui,sans-serif;margin:24px;max-width:760px}h1{font-size:1.1rem;border:2px solid #000;padding:8px}table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #999;padding:4px 6px;text-align:left}.n{text-align:right}.al{font-weight:700}@media print{body{margin:0}}</style></head><body>"
            f"<h1>{e(ROTULO)}</h1><p>{e(r['aviso'])}</p>{falta}{ok}"
            f"<p><b>Emisor:</b> {e(em['razon_social'] or '—')} · RUC {e(em['ruc'] or '—')}-{e(em['dv'] or '—')} · Sucursal {e(em['sucursal'] or '—')}<br>{e(em['direccion'])} {e(em['telefono'])}</p>"
            f"<p><b>Pedido #{e(d['pedido_id'])}</b> · {e(d['fecha'])} · {e(d['modalidad'])}<br><b>Cliente:</b> {e(rec)}</p>"
            "<table><thead><tr><th>Descripción</th><th class=n>Cant.</th><th class=n>Precio</th><th class=n>Base</th><th class=n>ITBMS %</th><th class=n>ITBMS</th><th class=n>Total</th></tr></thead><tbody>"
            + "".join(fila(l) for l in r["lineas"] + ([r["envio"]] if r["envio"] else [])) + f"</tbody></table>{nota}"
            f"<p>Subtotal B/. {e(t['subtotal'])} · ITBMS B/. {e(t['itbms'])} · Envío B/. {e(t['envio'])}<br><b>Total B/. {e(t['total'])}</b> · {'Pagado' if t['pagado'] else 'No pagado aún'}"
            f"{' (' + e(t['forma_pago']) + ')' if t['forma_pago'] else ''}</p></body></html>").encode("utf-8")


CSP_HTML = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"


def atender(sv, pedido_id, formato, rec):
    """(codigo, cuerpo, tipo, extra) para /panel/factura. El pedido sale de la tabla del servicio: nunca se acepta uno del cliente."""
    p = next((p for p in sv.listar("pedido") if p["id"] == pedido_id), None)
    if not p:
        return 404, {"error": "No existe ese pedido"}, "application/json", ()
    if p["estado"] in NO_VENTA:
        return 409, {"error": "Ese pedido no es una venta (sin confirmar, cancelado o caducado)"}, "application/json", ()
    try:
        r = resumen(sv, p, rec)
    except ValueError as e:
        return 400, {"error": str(e)}, "application/json", ()
    if formato == "csv":
        return 200, to_csv(r), "text/csv", [("Content-Disposition", f'attachment; filename="resumen-factura-{pedido_id}.csv"')]
    if formato == "html":
        return 200, to_html(r), "text/html", [("Content-Security-Policy", CSP_HTML)]
    return 200, {"resumen": r}, "application/json", ()
