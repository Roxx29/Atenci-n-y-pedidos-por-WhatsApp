"""Páginas 100 % propias de un cliente: servicios/<slug>/web/{index,panel,repartidor}.html y sus archivos (css, js, imágenes, fuentes)
servidos en /web/… SOLO en el dominio de ese cliente. Usan la misma API del motor: las reglas del negocio no cambian.
Solo stdlib (la vista previa de diseno/editable lo importa sin instalar nada). Decisiones en MOTOR.md, «Páginas propias»."""
import base64
import binascii
import io
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path

PAGINAS = {"web_index.html": "index.html", "panel_web.html": "panel.html", "repartidor.html": "repartidor.html"}  # página del motor -> la propia
MARCA = b"DeliFood"  # = web.MARCA: donde la página lo escriba sale la marca del cliente
ENLACE_TEMA = b'<link rel="stylesheet" href="estatico/tema.css">'  # = web.ENLACE_TEMA (páginas del motor y copias de base/)
MARCADOR_TEMA = b"<!--TEMA-->"  # el de las páginas propias: ahí va el tema del cliente + su personalizado.css
MIME = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".woff2": "font/woff2"}  # lo único que sale por /web/ (el HTML nunca: solo por sus rutas, con CSP)
FIRMA = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".webp": (b"RIFF",), ".woff2": (b"wOF2",)}
MAX_HTML, MAX_ARCHIVO, MAX_TOTAL, MAX_ARCHIVOS, MAX_NIVELES = 512_000, 1_000_000, 5_000_000, 60, 4
MAX_CUERPO = 7_500_000  # JSON de la subida: base64 de MAX_TOTAL (+33 %) y nombres
PARTE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")  # sin punto delante: ni «..», ni ocultos; sin \ ni :
HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
# SVG: ni scripts ni nada que cargue de fuera; como <img> el navegador ya no los ejecuta, esto cubre abrirlo a mano (y lo sirve con CSP sandbox)
SVG_MALO = re.compile(r"<\s*(script|foreignobject|iframe|embed|object)|<!(doctype|entity)|\son[a-z]+\s*=|javascript:|href\s*=\s*(?![\"']?\s*#)", re.I)
CSP_SVG = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def _enlace(p):
    """¿Es `p` un enlace (simbólico o junction de Windows, que is_symlink() no ve)?"""
    return p.is_symlink() or p.is_junction()


def _dentro(base, rel, maximo):
    """El archivo `rel` (con /) de la carpeta `base`, si existe, cabe en `maximo` y no sale de ella (ni por .., ni por enlaces); si no, None."""
    partes = rel.split("/")
    if not (1 <= len(partes) <= MAX_NIVELES and all(PARTE.fullmatch(p) for p in partes)):
        return None
    try:
        raiz = base.resolve()
        if raiz != base.parent.resolve() / base.name:  # web/ es un enlace a otra carpeta (p. ej. la de otro cliente)
            return None
        f = (base / rel).resolve()
        return f if f.is_relative_to(raiz) and f.is_file() and f.stat().st_size <= maximo else None
    except OSError:
        return None


def propia(carpeta, f):
    """Bytes de la página propia del cliente que sustituye a la del motor `f` (servicios/<slug>/web/), o None: sale la estándar."""
    p = _dentro(Path(carpeta) / "web", PAGINAS.get(Path(f).name, "-"), MAX_HTML)
    try:
        return p.read_bytes() or None if p else None
    except OSError:
        return None


def sustituir(html, css, marca):
    """Lo que el motor hace con cualquier página al servirla (propia o no): tema del cliente en lugar del marcador y su marca (ya escapada)."""
    estilo = b"<style>" + css + b"</style>"
    return html.replace(MARCADOR_TEMA, estilo).replace(ENLACE_TEMA, estilo).replace(MARCA, marca)


def permitidos(sv):
    """Dominios de `web.permitir` (servicio.yaml del cliente, nunca los ajustes del panel) que valen: nombres de host, sin comodines."""
    lista = ((getattr(sv, "_cfg", None) or {}).get("web") or {}).get("permitir") or []
    return [f"https://{h.lower()}" for h in lista[:10] if isinstance(h, str) and HOST.fullmatch(h.lower()) and h.lower() != "localhost"] if isinstance(lista, list) else []


def csp(sv, f, estandar):
    """CSP de la página `f`: la estándar si el cliente no tiene la suya. A la propia: scripts solo del propio dominio (nunca de fuera),
    peticiones solo a su API (connect-src 'self': ni a otro cliente ni a terceros) y de fuera solo fuentes, hojas de estilo e imágenes de `web.permitir`."""
    if not _dentro(Path(sv.dir) / "web", PAGINAS.get(Path(f).name, "-"), MAX_HTML):
        return estandar
    return csp_propia(permitidos(sv))


def csp_propia(fuera=()):
    p = "".join(" " + h for h in fuera)
    return (f"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'{p}; font-src 'self'{p}; "
            f"img-src 'self' blob: data: https://tile.openstreetmap.org{p}; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")


def archivo(base, rel):
    """(bytes, tipo) de base/rel si su extensión está en la lista blanca; si no, None."""
    tipo = MIME.get(Path(rel).suffix.lower())
    f = tipo and _dentro(Path(base), rel, MAX_ARCHIVO)
    try:
        return (f.read_bytes(), tipo) if f else None
    except OSError:
        return None


def servir(h, base, ruta, cache="public, max-age=300"):
    """GET /web/<rel> con el manejador `h` (web.py o la vista previa). Público y sin sesión: aquí no va nada privado."""
    a = archivo(base, ruta[len("/web/"):])
    if not a:
        return h._err(404, "No encontrado")
    datos, tipo = a
    h.send_response(200)
    for k, v in (("Content-Type", tipo), ("Content-Length", str(len(datos))), ("X-Content-Type-Options", "nosniff"),
                 ("Cache-Control", cache),  # ponytail: sin versión en la URL; 5 min de caché tras subir una página nueva
                 ("Cross-Origin-Resource-Policy", "same-origin"),  # la página de otro cliente no puede cargarlos
                 *((("Content-Security-Policy", CSP_SVG),) if tipo == "image/svg+xml" else ())):
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(datos)


# ---------------- subida desde la consola del operador ----------------
def _validar(rel, datos):
    """ValueError en español si `rel` o su contenido no se admiten."""
    partes = rel.split("/")
    ext = Path(rel).suffix.lower()
    if not (1 <= len(partes) <= MAX_NIVELES and all(PARTE.fullmatch(p) for p in partes)):
        raise ValueError(f"nombre no permitido: usa letras, números, - y _, sin carpetas de más de {MAX_NIVELES} niveles")
    if ext == ".html":
        if rel not in PAGINAS.values():
            raise ValueError("solo se admiten index.html, panel.html y repartidor.html, en la raíz")
        if len(datos) > MAX_HTML:
            raise ValueError(f"{rel}: máximo {MAX_HTML // 1000} KB")
    elif ext not in MIME:
        raise ValueError("extensión no permitida (html, css, js, png, jpg, webp, svg, woff2)")
    elif len(datos) > MAX_ARCHIVO:
        raise ValueError(f"archivo de más de {MAX_ARCHIVO // 1_000_000} MB")
    if not datos.startswith(FIRMA.get(ext, (b"",))) or (ext == ".webp" and datos[8:12] != b"WEBP"):
        raise ValueError(f"{Path(rel).name}: el contenido no es del tipo que dice su extensión")
    if ext in (".html", ".css", ".js", ".svg"):
        try:
            texto = datos.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{Path(rel).name}: debe estar en UTF-8") from None
        if ext == ".svg" and SVG_MALO.search(texto):
            raise ValueError(f"{Path(rel).name}: el SVG no puede llevar scripts, eventos, enlaces de fuera ni DOCTYPE")


def _de_base64(s):
    try:
        return base64.b64decode(s, validate=True) if isinstance(s, str) else None
    except (binascii.Error, ValueError):
        return None


def _de_zip(crudo):
    """{ruta: bytes} de un zip, leyendo cada entrada con tope (aunque mienta sobre su tamaño). Si todo va dentro de una carpeta, se quita."""
    try:
        z = zipfile.ZipFile(io.BytesIO(crudo))
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_ARCHIVOS:
            raise ValueError(f"máximo {MAX_ARCHIVOS} archivos")
        nombres = [i.filename for i in infos]
        tope = nombres[0].split("/")[0] + "/" if nombres and "/" in nombres[0] else None
        quitar = tope if tope and all(n.startswith(tope) for n in nombres) else ""
        out, total = {}, 0
        for i in infos:
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("el zip trae enlaces simbólicos")
            with z.open(i) as h:
                datos = h.read(MAX_ARCHIVO + 1)
            total += len(datos)
            if len(datos) > MAX_ARCHIVO or total > MAX_TOTAL:
                raise ValueError("el zip descomprimido es demasiado grande")
            out[i.filename[len(quitar):]] = datos
        return out
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError, OSError):
        raise ValueError("zip inválido") from None


def instalar(carpeta, d):
    """Página propia nueva para el cliente de `carpeta`: `{archivos: {ruta: base64}}` o `{zip: base64}`. Valida TODO antes de tocar el disco
    y cambia la carpeta web/ entera de una vez (o queda la nueva o la de antes). -> estado()."""
    if not isinstance(d, dict) or ("archivos" in d) == ("zip" in d) or set(d) - {"archivos", "zip"}:
        raise ValueError("indica archivos (ruta: base64) o zip (base64)")
    if "zip" in d:
        crudo = _de_base64(d["zip"])
        if crudo is None:
            raise ValueError("zip: base64 inválido")
        archivos = _de_zip(crudo)
    else:
        if not isinstance(d["archivos"], dict) or len(d["archivos"]) > MAX_ARCHIVOS:
            raise ValueError(f"archivos: objeto {{ruta: base64}} de hasta {MAX_ARCHIVOS}")
        archivos = {}
        for rel, s in d["archivos"].items():
            archivos[rel] = _de_base64(s)
            if archivos[rel] is None:
                raise ValueError("base64 inválido")
    if sum(map(len, archivos.values())) > MAX_TOTAL:
        raise ValueError(f"máximo {MAX_TOTAL // 1_000_000} MB en total")
    for rel, datos in archivos.items():
        _validar(rel, datos)
    if len({r.lower() for r in archivos}) != len(archivos):
        raise ValueError("hay dos archivos con el mismo nombre (mayúsculas aparte)")
    if not set(archivos) & set(PAGINAS.values()):
        raise ValueError("falta la página: index.html, panel.html o repartidor.html")
    carpeta = Path(carpeta)
    web, tok = carpeta / "web", secrets.token_hex(4)
    nueva, vieja = carpeta / f".web-nueva-{tok}", carpeta / f".web-vieja-{tok}"
    if _enlace(web):
        raise ValueError("su carpeta web/ es un enlace: arréglelo en el servidor")
    try:
        for rel, datos in archivos.items():
            (nueva / rel).parent.mkdir(parents=True, exist_ok=True)
            (nueva / rel).write_bytes(datos)
        if web.exists():
            os.rename(web, vieja)
        try:
            os.rename(nueva, web)
        except OSError:
            if vieja.exists():
                os.rename(vieja, web)
            raise
    finally:
        shutil.rmtree(nueva, ignore_errors=True)
        shutil.rmtree(vieja, ignore_errors=True)
    return estado(carpeta)


def quitar(carpeta):
    """Vuelve a la página estándar: retira web/ entera. -> estado()."""
    web = Path(carpeta) / "web"
    if _enlace(web):  # nunca se borra a través de un enlace: podría ser la carpeta de otro
        raise ValueError("su carpeta web/ es un enlace: arréglelo en el servidor")
    if web.exists():
        vieja = Path(carpeta) / f".web-vieja-{secrets.token_hex(4)}"
        os.rename(web, vieja)  # de golpe: no queda media página servida
        shutil.rmtree(vieja, ignore_errors=True)
    return estado(carpeta)


def estado(carpeta):
    """{activa, paginas, archivos, bytes}: activa = tiene al menos una de las tres páginas."""
    web = Path(carpeta) / "web"
    if not web.is_dir() or _enlace(web):
        return {"activa": False, "paginas": [], "archivos": 0, "bytes": 0}
    fs = [f for f in web.rglob("*") if f.is_file()]
    pags = sorted(p for p in PAGINAS.values() if (web / p).is_file())
    return {"activa": bool(pags), "paginas": pags, "archivos": len(fs), "bytes": sum(f.stat().st_size for f in fs)}
