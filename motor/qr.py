"""Código QR propio (ISO 18004), sin dependencias: solo modo bytes, corrección M o H (H cuando lleva logo), versiones 1 a 10.
`matriz(texto, nivel)` -> filas de bool (True = oscuro, sin margen); `svg(...)` -> <svg> con margen de 4 módulos, colores, estilo de módulo y esquinas, logo central y rótulo."""
import html
import re

_ECC = {"M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26), "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28)}   # bytes de corrección por bloque, por versión
_BLOQUES = {"M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5), "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8)}                    # bloques, por versión
_NIVEL = {"M": 0, "H": 2}                                                                                          # bits de nivel en el formato
LOGO = 0.28  # lado del hueco del logo respecto al lado del QR (~9 % del área; probado con decodificador: 0.36 ya falla en códigos pequeños)
V_LOGO = 5   # con logo, versión mínima: en un QR pequeño el hueco se come demasiado
_FINDER = ("10111010000", "00001011101")


def _mul(x, y):  # producto en GF(256), polinomio 0x11D
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D) ^ (((y >> i) & 1) * x)
    return z


def divisor(grado):
    r, raiz = [0] * (grado - 1) + [1], 1
    for _ in range(grado):
        for j in range(grado):
            r[j] = _mul(r[j], raiz) ^ (r[j + 1] if j + 1 < grado else 0)
        raiz = _mul(raiz, 2)
    return r


def resto(datos, div):
    r = [0] * len(div)
    for b in datos:
        f = b ^ r.pop(0)
        r.append(0)
        r = [x ^ _mul(c, f) for x, c in zip(r, div)]
    return r


def _crudos(v):  # módulos que llevan datos+corrección (sin patrones de función)
    n = (16 * v + 128) * v + 64
    if v >= 2:
        a = v // 7 + 2
        n -= (25 * a - 10) * a - 55 + (36 if v >= 7 else 0)
    return n


def _palabras(datos, v, nivel):
    """Palabras de datos con relleno, partidas en bloques, con su corrección, entrelazadas."""
    total, nb, ecc = _crudos(v) // 8, _BLOQUES[nivel][v], _ECC[nivel][v]
    cap = total - ecc * nb
    bits = "0100" + format(len(datos), "08b" if v < 10 else "016b") + "".join(format(b, "08b") for b in datos)
    if len(bits) > cap * 8:
        raise ValueError("Texto demasiado largo para el QR")
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    pal = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pal += [0xEC, 0x11] * cap
    pal = pal[:cap]
    cortos, largo = nb - total % nb, total // nb
    div, bloques, k = divisor(ecc), [], 0
    for i in range(nb):
        d = pal[k:k + largo - ecc + (i >= cortos)]
        k += len(d)
        bloques.append(d + ([0] if i < cortos else []) + resto(d, div))  # el 0 marca el hueco de los bloques cortos
    return [b[i] for i in range(len(bloques[-1])) for j, b in enumerate(bloques) if i != largo - ecc or j >= cortos]


def _alineacion(v):
    if v == 1:
        return []
    n, tam = v // 7 + 2, v * 4 + 17
    paso = (v * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    return [6] + [tam - 7 - i * paso for i in range(n - 2, -1, -1)]


def _formato(nivel, m):  # 15 bits de formato (nivel + máscara) con su BCH y la máscara fija 0x5412
    m |= _NIVEL[nivel] << 3
    r = m
    for _ in range(10):
        r = (r << 1) ^ ((r >> 9) * 0x537)
    return ((m << 10) | r) ^ 0x5412


def _celdas_formato(i, tam):  # dónde va el bit i del formato: junto al buscador superior izquierdo y repartido en los otros dos
    return ((8, i) if i < 6 else (8, 7) if i == 6 else (8, 8) if i == 7 else (7, 8) if i == 8 else (14 - i, 8)), ((tam - 1 - i, 8) if i < 8 else (8, tam - 15 + i))


def _mascara(m, x, y):
    return (x + y) % 2 == 0 if m == 0 else y % 2 == 0 if m == 1 else x % 3 == 0 if m == 2 else (x + y) % 3 == 0 if m == 3 \
        else (x // 3 + y // 2) % 2 == 0 if m == 4 else x * y % 2 + x * y % 3 == 0 if m == 5 \
        else (x * y % 2 + x * y % 3) % 2 == 0 if m == 6 else ((x + y) % 2 + x * y % 3) % 2 == 0


def _penalizacion(mod):
    tam = len(mod)
    filas = ["".join("1" if c else "0" for c in f) for f in mod]
    cols = ["".join(f[x] and "1" or "0" for f in mod) for x in range(tam)]
    p = sum(len(s) - 2 for t in filas + cols for s in re.findall(r"0{5,}|1{5,}", t))
    p += 3 * sum(a[i] == a[i + 1] == b[i] == b[i + 1] for a, b in zip(filas, filas[1:]) for i in range(tam - 1))
    p += 40 * sum(t.count(f) for t in filas + cols for f in _FINDER)
    oscuros = sum(t.count("1") for t in filas)
    return p + 10 * max(0, -(-abs(oscuros * 20 - tam * tam * 10) // (tam * tam)) - 1)


def matriz(texto, nivel="M", vmin=1):
    datos = texto.encode("utf-8")
    v = next((v for v in range(vmin, 11) if len(datos) * 8 + 4 + (8 if v < 10 else 16) <= (_crudos(v) // 8 - _ECC[nivel][v] * _BLOQUES[nivel][v]) * 8), None)
    if v is None:
        raise ValueError("Texto demasiado largo para el QR")
    tam = v * 4 + 17
    mod, fun = [[False] * tam for _ in range(tam)], [[False] * tam for _ in range(tam)]

    def poner(x, y, oscuro):
        mod[y][x], fun[y][x] = oscuro, True
    for i in range(tam):
        poner(6, i, i % 2 == 0)
        poner(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (tam - 4, 3), (3, tam - 4)):  # buscadores con su separador
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < tam and 0 <= cy + dy < tam:
                    poner(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _alineacion(v)
    for i, cx in enumerate(pos):
        for j, cy in enumerate(pos):
            if (i, j) not in ((0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)):
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        poner(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(15):  # reserva del formato (se escribe con cada máscara)
        for x, y in _celdas_formato(i, tam):
            poner(x, y, False)
    poner(8, tam - 8, True)
    if v >= 7:
        r = v
        for _ in range(12):
            r = (r << 1) ^ ((r >> 11) * 0x1F25)
        bits = (v << 12) | r
        for i in range(18):
            poner(tam - 11 + i % 3, i // 3, (bits >> i) & 1 == 1)
            poner(i // 3, tam - 11 + i % 3, (bits >> i) & 1 == 1)
    bit = iter(b for p in _palabras(datos, v, nivel) for b in format(p, "08b"))
    for derecha in range(tam - 1, 0, -2):  # zigzag de dos columnas, de abajo arriba y de arriba abajo
        derecha = 5 if derecha == 6 else derecha
        for vert in range(tam):
            for j in (0, 1):
                x, y = derecha - j, tam - 1 - vert if (derecha + 1) & 2 == 0 else vert
                if not fun[y][x]:
                    mod[y][x] = next(bit, "0") == "1"
    mejor = None
    for m in range(8):
        t = [[c ^ (not fun[y][x] and _mascara(m, x, y)) for x, c in enumerate(f)] for y, f in enumerate(mod)]
        bits = _formato(nivel, m)
        for i in range(15):
            for x, y in _celdas_formato(i, tam):
                t[y][x] = (bits >> i) & 1 == 1
        p = _penalizacion(t)
        if mejor is None or p < mejor[0]:
            mejor = (p, t)
    return mejor[1]


def caja_logo(n):
    """(desde, hasta) de módulos, en x e y, del hueco central del logo (sin incluir `hasta`)."""
    c = round(n * LOGO) // 2 * 2 + 1  # impar: centrado
    return (n - c) // 2, (n - c) // 2 + c


def _rr(x, y, w, r):  # cuadrado de lado w con esquinas de radio r, como trazo de <path>
    a = w - 2 * r
    return (f"M{x + r:g} {y:g}h{a:g}a{r:g} {r:g} 0 0 1 {r:g} {r:g}v{a:g}a{r:g} {r:g} 0 0 1 -{r:g} {r:g}h-{a:g}a{r:g} {r:g} 0 0 1 -{r:g} -{r:g}v-{a:g}a{r:g} {r:g} 0 0 1 {r:g} -{r:g}z")


def svg(texto, *, nivel="M", color="#000000", fondo="#ffffff", modulo="cuadrado", esquinas="cuadrada", logo=None, rotulo=None, etiqueta="Código QR"):
    """`logo`: URI data: de la imagen (PNG/WebP/SVG ya validada); con logo el nivel pasa a H. `rotulo`: texto bajo el código (dentro de un marco).
    `color`/`fondo`: #rrggbb ya validados por quien llama (mesa.estilo_valido)."""
    nivel = "H" if logo else nivel
    mod = matriz(texto, nivel, V_LOGO if logo else 1)
    n = len(mod)
    t, alto = n + 8, n + 8 + (4 if rotulo else 0)
    c0, c1 = caja_logo(n) if logo else (-1, -1)
    finder = lambda x, y: (x < 7 and y < 7) or (x >= n - 7 and y < 7) or (x < 7 and y >= n - 7)
    celda = {"cuadrado": lambda x, y: f"M{x} {y}h1v1h-1z",
             "redondeado": lambda x, y: _rr(x + .04, y + .04, .92, .3),
             "puntos": lambda x, y: f"M{x + .05:g} {y + .5}a.45 .45 0 1 0 .9 0a.45 .45 0 1 0 -.9 0z"}[modulo]
    d = "".join(celda(x + 4, y + 4) for y, f in enumerate(mod) for x, c in enumerate(f) if c and not finder(x, y) and not (c0 <= x < c1 and c0 <= y < c1))
    rf = (2, 1.2, .8) if esquinas == "redonda" else (0, 0, 0)
    dd = ""
    for fx, fy in ((4, 4), (n - 3, 4), (4, n - 3)):  # localizadores: anillo de 7, hueco de 5 y centro de 3 (evenodd)
        dd += "".join(_rr(fx + o, fy + o, w, r) if r else f"M{fx + o} {fy + o}h{w}v{w}h-{w}z" for o, w, r in ((0, 7, rf[0]), (1, 5, rf[1]), (2, 3, rf[2])))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {t} {alto}" role="img" aria-label="{html.escape(etiqueta)}" shape-rendering="{"crispEdges" if modulo == "cuadrado" and not rf[0] else "geometricPrecision"}">',
           f'<rect width="{t}" height="{alto}" fill="{fondo}"/>', f'<path d="{d}" fill="{color}"/>', f'<path d="{dd}" fill="{color}" fill-rule="evenodd"/>']
    if logo:
        a, b = c0 + 4, c1 - c0
        out += [f'<rect x="{a}" y="{a}" width="{b}" height="{b}" rx="{b * .12:g}" fill="{fondo}"/>',
                f'<image x="{a + .6:g}" y="{a + .6:g}" width="{b - 1.2:g}" height="{b - 1.2:g}" href="{logo}" preserveAspectRatio="xMidYMid meet"/>']
    if rotulo:
        fs = min(2.6, 1.6 * t / max(len(rotulo), 1))
        out += [f'<rect x=".5" y=".5" width="{t - 1}" height="{alto - 1}" rx="2" fill="none" stroke="{color}" stroke-width=".6"/>',
                f'<text x="{t / 2:g}" y="{t + 1.4:g}" text-anchor="middle" font-family="system-ui,Arial,sans-serif" font-weight="700" font-size="{fs:.2f}" fill="{color}">{html.escape(rotulo)}</text>']
    return "".join(out) + "</svg>"
