"""Códigos QR dinámicos y pedidos por mesa (módulo `mesas`). Sin dependencias; no importa el resto del motor.
El QR lleva solo `/m/<codigo>`: un código corto aleatorio guardado en la BD del cliente. El destino se resuelve en el servidor
(tabla qr_codigos) y el dueño lo cambia sin reimprimir; desactivar o regenerar un código lo invalida. Nada del código se deriva de la fecha."""
import base64
import datetime as dt
import html
import json
import re
import secrets

from . import qr

MAX_MESAS = 100
MAX_CODIGOS = 200
MAX_ACTIVOS_MESA = 5  # pedidos en curso a la vez en una misma mesa
MAX_LOGO = 200 * 1024
TIPOS = {"pedir": "Pedir en esta mesa", "menu": "Ver el menú (sin pedir)", "promo": "Promoción del día", "resena": "Dejar reseña", "pausado": "Pausado"}
MSG_PAUSA = "Mesa no disponible"
ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"  # sin 0/o/1/l/i
LARGO = 8
CID = re.compile(r"mesa(\d{1,3})-[0-9a-f]{8}")  # quien pide desde una mesa: va en la cookie firmada, nunca lo pone el cliente
HEX = re.compile(r"#[0-9a-f]{6}")
URL = re.compile(r"https?://[^\s<>\"'\\]{3,480}")
ESTILO0 = {"modulo": "cuadrado", "esquinas": "cuadrada", "color": "#000000", "fondo": "#ffffff", "texto": "Escanea y pide", "marco": True, "logo": True}
MODULOS = ("cuadrado", "redondeado", "puntos")
PLANTILLAS = ("mesa", "flyer", "pegatina")


def maximo(sv):
    """Mesas del local: `pedidos.mesas` de servicio.yaml (1..100); por defecto 100."""
    n = (sv.cfg.get("pedidos") or {}).get("mesas")
    return min(MAX_MESAS, n) if isinstance(n, int) and not isinstance(n, bool) and n > 0 else MAX_MESAS


def nuevo_cid(mesa):
    return f"mesa{mesa}-{secrets.token_hex(4)}"


def de_cid(cid):
    """Mesa de un cid de mesa, o None (un teléfono nunca coincide)."""
    m = CID.fullmatch(cid) if isinstance(cid, str) else None
    return int(m[1]) if m else None


# ---------- códigos ----------
def _texto(v, nombre, maximo_largo, obligatorio=False):
    if v is None or v == "":
        if obligatorio:
            raise ValueError(f"Falta {nombre}")
        return ""
    if not isinstance(v, str) or len(v.strip()) > maximo_largo or any(ord(c) < 32 for c in v):
        raise ValueError(f"{nombre.capitalize()} inválido (máx. {maximo_largo} caracteres, una línea)")
    return v.strip()


def _campos(sv, d):
    """Valida {etiqueta, tipo, destino, mesa} -> (etiqueta, tipo, destino, mesa)."""
    tipo = d.get("tipo", "pedir")
    if tipo not in TIPOS:
        raise ValueError("Tipo de destino inválido: " + ", ".join(TIPOS))
    etiqueta = _texto(d.get("etiqueta"), "la etiqueta", 40, True)
    mesa = d.get("mesa")
    if mesa is not None and (isinstance(mesa, bool) or not isinstance(mesa, int) or not 1 <= mesa <= maximo(sv)):
        raise ValueError(f"La mesa debe ser un número de 1 a {maximo(sv)}")
    if tipo == "pedir" and mesa is None:
        raise ValueError("«Pedir en esta mesa» necesita el número de mesa")
    destino = _texto(d.get("destino"), "el destino", 300)
    if tipo == "resena" and not URL.fullmatch(destino):
        raise ValueError("La reseña necesita un enlace https://…")
    if tipo == "promo" and not destino:
        raise ValueError("La promoción necesita un enlace o un texto")
    if tipo == "promo" and destino.lower().startswith(("javascript:", "data:", "vbscript:")):
        raise ValueError("Destino no permitido")
    if tipo in ("pedir", "menu", "pausado"):
        destino = ""
    return etiqueta, tipo, destino, mesa


def _codigo_libre(sv):
    while True:
        c = "".join(secrets.choice(ALFABETO) for _ in range(LARGO))
        if not sv.db.execute("SELECT 1 FROM qr_codigos WHERE codigo=?", (c,)).fetchall():  # el llamador ya tiene sv._db_lock
            return c


def crear(sv, d):
    if sv._q("SELECT COUNT(*) FROM qr_codigos")[0][0] >= MAX_CODIGOS:
        raise ValueError(f"Máximo {MAX_CODIGOS} códigos")
    etiqueta, tipo, destino, mesa = _campos(sv, d)
    with sv._db_lock, sv.db:
        c = _codigo_libre(sv)
        i = sv.db.execute("INSERT INTO qr_codigos(codigo, etiqueta, tipo, destino, mesa, activo, creado) VALUES (?,?,?,?,?,1,?)",
                          (c, etiqueta, tipo, destino, mesa, sv.ahora().isoformat())).lastrowid
    return {"id": i, "codigo": c}


def actualizar(sv, i, d):
    """Cambia etiqueta, tipo, destino, mesa y/o activo (lo no indicado se conserva). El código NO cambia: el QR impreso sigue valiendo."""
    f = sv._q("SELECT etiqueta, tipo, destino, mesa, activo FROM qr_codigos WHERE id=?", (i,))
    if not f:
        raise LookupError("qr")
    act = d.get("activo", bool(f[0][4]))
    if not isinstance(act, bool):
        raise ValueError("Activo debe ser sí o no")
    etiqueta, tipo, destino, mesa = _campos(sv, {"etiqueta": f[0][0], "tipo": f[0][1], "destino": f[0][2], "mesa": f[0][3], **{k: v for k, v in d.items() if k != "activo"}})
    sv._q("UPDATE qr_codigos SET etiqueta=?, tipo=?, destino=?, mesa=?, activo=? WHERE id=?", (etiqueta, tipo, destino, mesa, int(act), i))


def regenerar(sv, i):
    """Código nuevo (el viejo deja de funcionar al instante): para un QR sospechoso o filtrado."""
    if not sv._q("SELECT 1 FROM qr_codigos WHERE id=?", (i,)):
        raise LookupError("qr")
    with sv._db_lock, sv.db:
        c = _codigo_libre(sv)
        sv.db.execute("UPDATE qr_codigos SET codigo=? WHERE id=?", (c, i))
    return c


def borrar(sv, i):
    with sv._db_lock, sv.db:
        sv.db.execute("DELETE FROM qr_dias WHERE codigo_id=?", (i,))
        if sv.db.execute("DELETE FROM qr_codigos WHERE id=?", (i,)).rowcount != 1:
            raise LookupError("qr")


def por_codigo(sv, codigo):
    """Fila del código ACTIVO, o None (desconocido, desactivado y regenerado dan lo mismo: no se distingue)."""
    if not (isinstance(codigo, str) and len(codigo) == LARGO and all(c in ALFABETO for c in codigo)):
        return None
    f = sv._q("SELECT id, etiqueta, tipo, destino, mesa FROM qr_codigos WHERE codigo=? AND activo=1", (codigo,))
    return dict(zip(("id", "etiqueta", "tipo", "destino", "mesa"), f[0])) if f else None


def contar(sv, i):
    """Un escaneo más hoy (agregado por código y día; sin IP ni datos personales)."""
    dia = f"{sv.ahora():%Y-%m-%d}"
    sv._q("INSERT INTO qr_dias(codigo_id, dia, n) VALUES (?,?,1) ON CONFLICT(codigo_id, dia) DO UPDATE SET n = n + 1", (i, dia))
    sv._q("UPDATE qr_codigos SET escaneos = escaneos + 1 WHERE id=?", (i,))


def listar(sv, base=""):
    hoy = sv.ahora().date()
    ult = [(hoy - dt.timedelta(days=k)).isoformat() for k in range(6, -1, -1)]
    dias = {}
    for i, dia, n in sv._q("SELECT codigo_id, dia, n FROM qr_dias WHERE dia >= ?", (ult[0],)):
        dias.setdefault(i, {})[dia] = n
    return [{"id": i, "codigo": c, "url": f"{base}/m/{c}", "etiqueta": e, "tipo": t, "destino": dest, "mesa": m, "activo": bool(a), "escaneos": esc,
             "hoy": dias.get(i, {}).get(ult[-1], 0), "dias7": [{"dia": d, "n": dias.get(i, {}).get(d, 0)} for d in ult]}
            for i, c, e, t, dest, m, a, esc in sv._q("SELECT id, codigo, etiqueta, tipo, destino, mesa, activo, escaneos FROM qr_codigos ORDER BY COALESCE(mesa, 999), id")]


# ---------- estilo y logo (por cliente; el mismo para todos sus QR) ----------
def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def contraste(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def estilo_valido(d):
    """Estilo completo y validado. Rechaza colores que no escanean: módulos más claros que el fondo o contraste < 4."""
    if not isinstance(d, dict) or set(d) - set(ESTILO0):
        raise ValueError("Estilo inválido")
    e = {**ESTILO0, **d}
    if e["modulo"] not in MODULOS or e["esquinas"] not in ("cuadrada", "redonda"):
        raise ValueError("Estilo de módulo o esquinas inválido")
    for k in ("color", "fondo"):
        e[k] = e[k].lower() if isinstance(e[k], str) else ""
        if not HEX.fullmatch(e[k]):
            raise ValueError("Los colores son #rrggbb")
    if not isinstance(e["marco"], bool) or not isinstance(e["logo"], bool):
        raise ValueError("Marco y logo son sí o no")
    e["texto"] = _texto(e["texto"], "el texto", 30)
    if _lum(e["color"]) >= _lum(e["fondo"]) or contraste(e["color"], e["fondo"]) < 4:
        raise ValueError("Colores ilegibles: el código debe ser más oscuro que el fondo y con buen contraste (mínimo 4:1)")
    return e


def estilo(sv):
    f = sv._q("SELECT valor FROM qr_config WHERE clave='estilo'")
    try:
        if f:
            return estilo_valido(json.loads(f[0][0]))
        from . import temas  # sin estilo guardado: el color de marca del cliente, si es lo bastante oscuro para escanear
        return estilo_valido({"color": temas.tema_de(sv.dir)["color"] or ESTILO0["color"]})
    except (ValueError, TypeError):
        return dict(ESTILO0)


def guardar_estilo(sv, d):
    e = estilo_valido(d)
    sv._q("INSERT OR REPLACE INTO qr_config(clave, valor) VALUES ('estilo', ?)", (json.dumps(e),))
    return e


def _tipo_logo(datos):
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    from . import paginas  # el mismo filtro de SVG que las páginas propias
    try:
        t = datos.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if re.match(r"\s*(<\?xml[^>]*\?>\s*)?<svg[\s>]", t) and not paginas.SVG_MALO.search(t):
        return "image/svg+xml"
    return None


def guardar_logo(sv, datos):
    """Logo del centro del QR: PNG, WebP o SVG (tipo comprobado por su contenido), <= 200 KB; el SVG sin scripts ni enlaces."""
    if not datos or len(datos) > MAX_LOGO:
        raise ValueError("El logo debe pesar como máximo 200 KB")
    tipo = _tipo_logo(datos)
    if not tipo:
        raise ValueError("El logo debe ser PNG, WebP o SVG sin scripts")
    sv._q("INSERT OR REPLACE INTO qr_config(clave, valor) VALUES ('logo', ?)", (base64.b64encode(datos).decode(),))
    sv._q("INSERT OR REPLACE INTO qr_config(clave, valor) VALUES ('logo_tipo', ?)", (tipo,))


def quitar_logo(sv):
    sv._q("DELETE FROM qr_config WHERE clave IN ('logo', 'logo_tipo')")


def logo_uri(sv):
    f = dict(sv._q("SELECT clave, valor FROM qr_config WHERE clave IN ('logo', 'logo_tipo')"))
    return f"data:{f['logo_tipo']};base64,{f['logo']}" if "logo" in f and f.get("logo_tipo") else None


def svg_de(sv, url, est=None, logo=..., etiqueta="Código QR"):
    """SVG del QR de `url` con el estilo del cliente (o el borrador `est`) y su logo."""
    est = est or estilo(sv)
    logo = logo_uri(sv) if logo is ... else logo
    return qr.svg(url, color=est["color"], fondo=est["fondo"], modulo=est["modulo"], esquinas=est["esquinas"], logo=logo if est["logo"] else None,
                  rotulo=est["texto"] if est["marco"] and est["texto"] else None, etiqueta=etiqueta)


# ---------- hoja imprimible ----------
def hoja(marca, tarjetas, plantilla, acento="#000000", est=None):
    """Página imprimible. `tarjetas`: [{etiqueta, svg}]. Plantillas: mesa (rejilla de tarjetas), flyer (una por hoja, vertical), pegatina (redonda)."""
    if plantilla not in PLANTILLAS or not HEX.fullmatch(acento):
        raise ValueError("Plantilla inválida")
    m, texto = html.escape(marca), html.escape((est or ESTILO0)["texto"])
    cuerpo = "".join(f'<article class="{plantilla}"><div class="cab">{m}</div><div class="qr">{t["svg"]}</div><h2>{html.escape(t["etiqueta"])}</h2><p>{texto}</p></article>' for t in tarjetas)
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QR · {m}</title><style>'
            f':root{{--ac:{acento}}}*{{box-sizing:border-box}}body{{margin:0;font:16px/1.4 system-ui,sans-serif;color:#000;background:#fff}}'
            '.barra{display:flex;gap:12px;align-items:center;padding:12px 16px;background:#f3f3f3;border-bottom:1px solid #ccc}.barra button{min-height:44px;padding:0 16px;font:inherit;font-weight:700}'
            'main{display:grid;gap:16px;padding:16px;grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}'
            'article{border:3px solid var(--ac);border-radius:14px;padding:12px;text-align:center;break-inside:avoid;overflow:hidden}'
            '.cab{font-weight:800;letter-spacing:.04em;text-transform:uppercase;font-size:.85rem;color:var(--ac)}.qr svg{width:100%;height:auto;display:block;margin:6px auto;max-width:210px}'
            'h2{margin:0;font-size:2rem;line-height:1.1}p{margin:2px 0 0;font-size:.95rem}'
            '.flyer{min-height:260mm;display:flex;flex-direction:column;justify-content:center;gap:10mm;page-break-after:always;border-width:6mm}'
            '.flyer .cab{font-size:2.2rem}.flyer .qr svg{max-width:150mm}.flyer h2{font-size:4rem}.flyer p{font-size:1.6rem}'
            '.pegatina{border-radius:50%;aspect-ratio:1;display:flex;flex-direction:column;justify-content:center;align-items:center;padding:8%}.pegatina .qr svg{max-width:62%}.pegatina h2{font-size:1.5rem}.pegatina .cab{font-size:.7rem}'
            f'@media print{{.barra{{display:none}}main{{gap:6mm;padding:0;grid-template-columns:repeat({"1" if plantilla == "flyer" else "3"},1fr)}}}}'
            '</style></head><body><div class="barra"><button type="button" id="imprimir">Imprimir</button><span>Los QR no caducan: se pueden imprimir una sola vez.</span></div>'
            f'<main>{cuerpo}</main><script>document.getElementById("imprimir").onclick=()=>print()</script></body></html>').encode()
