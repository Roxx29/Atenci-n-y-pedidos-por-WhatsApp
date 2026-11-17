"""Temas de las tres páginas (web de pedidos, panel y repartidor) y CSS personalizado por cliente.
Un solo origen: motor/estatico/temas.json (tokens de color). El tema.css de cada cliente se GENERA de ahí con generar_tema()."""
import json
import re
from pathlib import Path

ESTATICO = Path(__file__).with_name("estatico")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
MAX_CSS = 20_000  # bytes de personalizado.css
MAX_DATA = 8_192  # caracteres de cada imagen data: incrustada
# lo que un CSS personalizado no puede llevar. `<` cubre </style y <!--; `\` cubre los escapes CSS (\75rl = url), que saltarían esta lista
PROHIBIDO = re.compile(r"[<\\]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|@import|image-set\s*\(|expression\s*\(|javascript:|(?<![a-z-])behavior\s*:|-moz-binding", re.I)
URL = re.compile(r"url\s*\(", re.I)
DATA = re.compile(r"""url\(\s*(["']?)data:image/(?:png|jpeg|webp|svg\+xml)[;,][A-Za-z0-9+/=%;,._:~-]*\1\s*\)""")


def catalogo():
    return json.loads((ESTATICO / "temas.json").read_text(encoding="utf-8"))


def contraste(a, b):
    """Contraste WCAG entre dos #rrggbb (1 a 21)."""
    def lum(h):
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (f(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def generar_tema(tema_id="clasico", color=None):
    """CSS de un cliente: tema.css del motor + los tokens del tema + (opcional) su color primario #rrggbb. ValueError si algo no vale."""
    t = next((t for t in catalogo() if t["id"] == tema_id), None) if isinstance(tema_id, str) else None
    if t is None:
        raise ValueError("tema desconocido")
    if color is not None and not (isinstance(color, str) and COLOR.fullmatch(color)):
        raise ValueError("color inválido: usa el formato #rrggbb")
    css = (ESTATICO / "tema.css").read_text(encoding="utf-8")
    if tema_id != "clasico":  # el clásico ya es la base (y conserva su modo oscuro automático); los demás fijan todos los colores
        css += "\n:root{color-scheme:%s;%s}\n" % ("dark" if t["modo"] == "oscuro" else "light", ";".join(f"{k}:{v}" for k, v in t["tokens"].items()))
    if color:
        color = color.lower()
        sobre = max("#000000", "#ffffff", key=lambda c: contraste(c, color))
        css += f"\n:root{{--primary:{color};--primary-container:{color};--on-primary:{sobre}}}\n"
    return css


def sanear_css(css):
    """CSS personalizado de un cliente, recortado; ValueError si pasa del tope o trae algo fuera de la lista estricta.
    Va incrustado en <style>: no puede cerrarlo (sin `<`), cargar nada de fuera (sin @import ni url() salvo imágenes data: pequeñas) ni ejecutar nada."""
    if not isinstance(css, str):
        raise ValueError("css: debe ser texto")
    css = css.strip()
    if len(css.encode("utf-8", "replace")) > MAX_CSS:
        raise ValueError(f"css: máximo {MAX_CSS // 1000} KB")
    if PROHIBIDO.search(css):
        raise ValueError("css: no se admite <, \\, @import, image-set(, expression(, javascript:, behavior ni -moz-binding")
    for m in URL.finditer(css):
        d = DATA.match(css, m.start())
        if not d or len(d[0]) > MAX_DATA:
            raise ValueError("css: url() solo admite imágenes data: (png, jpeg, webp o svg+xml en base64) de hasta 8 KB")
    return css
