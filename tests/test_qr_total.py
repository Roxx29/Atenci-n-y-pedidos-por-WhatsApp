"""Revisión total del QR: SVG fiel a la matriz, etiquetas raras, URL por Host, mesas extremas. Ejecutar: python -m pytest tests/test_qr_total.py"""
import re
import unittest

from motor import mesa, qr
from tests.test_mesa import Servidor, leer, cv2


def celdas(svg, finder_y_logo):
    """Reconstruye la matriz desde el <path> de módulos del SVG (cada subtrazado empieza dentro de su celda)."""
    d = re.findall(r'<path d="([^"]*)" fill="[^"]*"/>', svg)[0]
    return {(int(float(x)) - 4, int(float(y)) - 4) for x, y in re.findall(r"M(-?[\d.]+) (-?[\d.]+)", d)}


class SvgFiel(unittest.TestCase):
    URL = "https://restaurante.example.com/m/k7x2m9qp"

    def test_modulos_del_svg_igual_a_la_matriz(self):
        m = qr.matriz(self.URL)
        n = len(m)
        esperado = {(x, y) for y in range(n) for x in range(n) if m[y][x] and not ((x < 7 and y < 7) or (x >= n - 7 and y < 7) or (x < 7 and y >= n - 7))}
        for mod in ("cuadrado", "redondeado", "puntos"):
            self.assertEqual(celdas(qr.svg(self.URL, modulo=mod), 0), esperado, mod)

    def test_con_logo_solo_falta_el_hueco(self):
        m = qr.matriz(self.URL, "H", qr.V_LOGO)
        n = len(m)
        a, b = qr.caja_logo(n)
        dib = celdas(qr.svg(self.URL, logo="data:image/png;base64,AAAA"), 0)
        self.assertFalse(any(a <= x < b and a <= y < b for x, y in dib))
        self.assertTrue(all(m[y][x] for x, y in dib))
        fuera = {(x, y) for y in range(n) for x in range(n) if m[y][x] and not (a <= x < b and a <= y < b) and not ((x < 7 and y < 7) or (x >= n - 7 and y < 7) or (x < 7 and y >= n - 7))}
        self.assertEqual(dib, fuera)

    def test_margen_y_marco_dentro_del_lienzo(self):
        for rot in (None, "Escanea y pide", "x" * 30, "<&\"'>"):
            s = qr.svg(self.URL, rotulo=rot, etiqueta='Mesa "<7>" & ñ')
            n = len(qr.matriz(self.URL))
            self.assertIn(f'viewBox="0 0 {n + 8} {n + 8 + (4 if rot else 0)}"', s)  # margen de 4 módulos (norma)
            self.assertNotIn('"<7>"', s)  # etiqueta escapada
            if rot:
                self.assertNotIn("<&", s)
                fs = float(re.search(r'font-size="([\d.]+)"', s)[1])
                self.assertLessEqual(len(rot) * fs * 0.62, n + 8 + 1.5)  # el rótulo cabe en el ancho

    def test_decodifica_toda_la_longitud_del_codigo_real(self):
        if cv2 is None:
            self.skipTest("sin OpenCV")
        for host in ("a.io", "x" * 60 + ".example.com", "localhost:8080"):
            u = f"https://{host}/m/abcdefgh"
            self.assertEqual(leer(qr.matriz(u)), u)
            self.assertEqual(leer(qr.matriz(u, "H", qr.V_LOGO), logo=True), u)

    def test_texto_demasiado_largo_falla_limpio(self):
        with self.assertRaises(ValueError):
            qr.matriz("u" * 400)


class Extremos(Servidor):
    def test_etiquetas_raras_y_largas(self):
        self.listo()
        for et in ("Mesa <script>alert(1)</script>", 'Terraza "VIP" & Bar', "ñandú — 北京 🍽", "x" * 40):
            c = self.codigo(etiqueta=et, mesa=3)
            r, svg = self.req("GET", f"/panel/qr/svg/{c['id']}", cookie=self.ck)
            self.assertEqual(r.status, 200)
            self.assertNotIn(b"<script>alert", svg)
            r, h = self.req("GET", f"/panel/qr/hoja?plantilla=mesa&ids={c['id']}", cookie=self.ck)
            self.assertEqual(r.status, 200)
            self.assertNotIn(b"<script>alert", h)
        for malo in ("x" * 41, "a\nb", "   "):
            self.assertNotEqual(self.api("POST", "qr_codigo", {"etiqueta": malo, "tipo": "pedir", "mesa": 3})[0], 200)

    def test_mesas_1_y_100_y_fuera_de_rango(self):
        self.listo()
        for m in (1, 100):
            self.assertEqual(self.api("POST", "qr_codigo", {"etiqueta": f"M{m}", "tipo": "pedir", "mesa": m})[0], 200)
        for m in (0, 101, -1, True, "7", 7.5, None):
            self.assertEqual(self.api("POST", "qr_codigo", {"etiqueta": "x", "tipo": "pedir", "mesa": m})[0], 409, m)

    def test_url_del_qr_con_host_raro(self):
        self.listo()
        c = self.codigo()
        lista = self.api("GET", "qr_codigos")[1]["codigos"]
        self.assertTrue(all(re.fullmatch(r"https?://[a-z0-9.:-]+/m/[a-z2-9]{8}", x["url"]) for x in lista))
        self.assertTrue(lista[0]["url"].endswith("/m/" + c["codigo"]))

    def test_regenerado_y_desactivado_no_abren_pedido(self):
        self.listo()
        c = self.codigo()
        viejo = c["codigo"]
        self.api("POST", "qr_regenerar", {"id": c["id"]})
        self.assertEqual(self.req("GET", f"/m/{viejo}")[0].status, 404)
        self.assertNotIn("mesa", self.js("GET", f"/config?m={viejo}")[1])
        st = self.js("POST", "/entrar", {"nombre": "Ana", "codigo": viejo, "consentimiento": True, "version_aviso": "v1"})[0]
        self.assertEqual(st, 400)  # sin teléfono y sin mesa válida: no entra
        nuevo = next(x for x in self.api("GET", "qr_codigos")[1]["codigos"] if x["id"] == c["id"])["codigo"]
        self.assertNotEqual(nuevo, viejo)
        self.assertEqual(self.location(nuevo), (302, "/?m=" + nuevo))

    def test_codigos_basura_en_la_url(self):
        self.listo()
        for malo in ("ABCDEFGH", "abc", "a" * 9, "0o1liabc", "%00%00%00%00", "../etc/pw", "abcdefg%C3%B1"):
            self.assertEqual(self.req("GET", "/m/" + malo)[0].status, 404, malo)
            self.assertNotIn("mesa", self.js("GET", "/config?m=" + malo)[1])


if __name__ == "__main__":
    unittest.main()
