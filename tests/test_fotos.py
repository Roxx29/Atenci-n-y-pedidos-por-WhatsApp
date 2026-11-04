"""Fotos de platos. Ejecutar: python -m unittest tests.test_fotos"""
import io
import json
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from PIL import Image

from motor.nucleo import Servicio
from tests.test_motor import ClienteFalso
from tests.test_panel_web import PIN
from tests.test_web import Base

RUTA = "/panel/dueno/foto?plato=sancocho"


def imagen(fmt="JPEG", size=(800, 600), exif=False, color=(200, 80, 30)):
    im, b = Image.new("RGB", size, color), io.BytesIO()
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "CamaraSecreta"  # Make
        kw["exif"] = e.tobytes()
    im.save(b, fmt, **kw)
    return b.getvalue()


class Fotos(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN, "PANEL_PIN_PERSONAL": "918273", "PANEL_PIN_HASH": ""})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    def login(self, pin=PIN):
        r = self.req("POST", "/panel/entrar", {"pin": pin})[0]
        return r.status, (r.getheader("Set-Cookie") or "").split(";")[0]

    def subir(self, datos, tipo="image/jpeg", ruta=RUTA, ck=None):
        ck = ck or self.login()[1]
        r, raw = self.req("POST", ruta, cookie=ck, crudo=datos, tipo=tipo)
        return r.status, json.loads(raw)

    def test_subida_valida_sirve_webp_sin_exif(self):
        st, d = self.subir(imagen(exif=True))
        self.assertEqual(st, 200, d)
        self.assertRegex(d["foto"], r"^/menu/foto/sancocho\.webp\?v=[0-9a-f]{12}$")
        self.assertEqual(d["foto2x"], d["foto"] + "&s=2")
        for url, lado, tope in ((d["foto"], 160, 12_000), (d["foto2x"], 320, 40_000)):
            r, raw = self.req("GET", url)
            self.assertEqual(r.status, 200)
            self.assertEqual(r.getheader("Content-Type"), "image/webp")
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("immutable", r.getheader("Cache-Control"))
            self.assertLessEqual(len(raw), tope)
            im = Image.open(io.BytesIO(raw))
            self.assertEqual((im.format, im.size), ("WEBP", (lado, lado)))
            self.assertNotIn(b"CamaraSecreta", raw)
            self.assertNotIn(b"EXIF", raw)
        menu = {p["id"]: p for p in self.js("GET", "/menu")[1]}
        self.assertEqual((menu["sancocho"]["foto"], menu["sancocho"]["foto2x"]), (d["foto"], d["foto2x"]))
        self.assertIsNone(menu["chicha"]["foto"])
        self.assertIsNone(menu["chicha"]["foto2x"])
        aud = [a["accion"] for a in self.sv.listar("auditoria")]
        self.assertIn("foto_subir:sancocho", aud)
        self.assertEqual(self.js("GET", "/panel/dueno/menu", cookie=self.login()[1])[1]["menu"][0]["foto"], d["foto"])

    def test_png_y_webp_con_transparencia(self):
        for fmt, tipo in (("PNG", "image/png"), ("WEBP", "image/webp")):
            b = io.BytesIO()
            Image.new("RGBA", (300, 500), (0, 0, 0, 0)).save(b, fmt)
            self.assertEqual(self.subir(b.getvalue(), tipo)[0], 200)

    def test_tipo_falso_y_tipo_no_permitido(self):
        self.assertEqual(self.subir(b"hola, no soy imagen" * 10, "image/png")[0], 415)
        b = io.BytesIO()
        Image.new("RGB", (50, 50)).save(b, "GIF")
        self.assertEqual(self.subir(b.getvalue(), "image/png")[0], 415)  # GIF real con tipo PNG
        self.assertEqual(self.subir(imagen(), "text/plain")[0], 415)  # un formulario ajeno no puede mandar image/*

    def test_demasiado_grande(self):
        self.assertEqual(self.subir(b"\xff\xd8\xff" + b"0" * (3 * 1024 * 1024))[0], 413)

    def test_imagen_bomba(self):
        b = io.BytesIO()
        Image.new("1", (12000, 12000)).save(b, "PNG")  # 144 Mpx en ~20 KB
        st, d = self.subir(b.getvalue(), "image/png")
        self.assertEqual(st, 400, d)

    def test_bomba_sin_globales(self):
        from PIL import Image as I
        antes = I.MAX_IMAGE_PIXELS
        self.test_imagen_bomba()
        self.assertEqual(I.MAX_IMAGE_PIXELS, antes)

    def test_limite_de_subidas(self):
        ck = self.login()[1]
        cods = [self.subir(imagen(size=(40, 40)), ck=ck)[0] for _ in range(11)]
        self.assertEqual(cods[:10], [200] * 10)
        self.assertEqual(cods[10], 429)

    def test_cache_solo_con_version(self):
        url = self.subir(imagen())[1]["foto"]
        self.assertIn("immutable", self.req("GET", url)[0].getheader("Cache-Control"))
        self.assertEqual(self.req("GET", url.split("?")[0])[0].getheader("Cache-Control"), "no-cache")

    def test_csp_endurecida(self):
        csp = self.req("GET", "/panel")[0].getheader("Content-Security-Policy")
        for d in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"):
            self.assertIn(d, csp)

    def test_personal_403_plato_404_y_sin_sesion(self):
        ck = self.login("918273")[1]
        self.assertEqual(self.subir(imagen(), ck=ck)[0], 403)
        self.assertEqual(self.subir(imagen(), ruta="/panel/dueno/foto?plato=nada")[0], 404)
        self.assertEqual(self.subir(imagen(), ruta="/panel/dueno/foto?plato=../x")[0], 404)
        r, _ = self.req("POST", RUTA, crudo=imagen(), tipo="image/jpeg")
        self.assertEqual(r.status, 401)

    def test_borrar(self):
        url = self.subir(imagen())[1]["foto"]
        ck = self.login()[1]
        self.assertEqual(self.js("POST", "/panel/dueno/foto_borrar", {"plato": "sancocho"}, cookie=ck)[:2], (200, {"ok": True}))
        self.assertIsNone(next(p for p in self.js("GET", "/menu")[1] if p["id"] == "sancocho")["foto"])
        self.assertEqual(self.req("GET", url)[0].status, 404)
        self.assertEqual(self.js("POST", "/panel/dueno/foto_borrar", {"plato": "nada"}, cookie=ck)[0], 404)
        self.assertIn("foto_borrar:sancocho", [a["accion"] for a in self.sv.listar("auditoria")])

    def test_foto_inexistente_404(self):
        for u in ("/menu/foto/sancocho.webp", "/menu/foto/nada.webp", "/menu/foto/..%2Fx.webp"):
            self.assertEqual(self.req("GET", u)[0].status, 404)


class Migracion(unittest.TestCase):
    def test_tabla_vieja_conserva_platos(self):
        from tests.test_motor import MENU, SERVICIO_PEDIDOS
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        db = sqlite3.connect(d / "datos.sqlite")
        db.execute("CREATE TABLE platos(orden INTEGER PRIMARY KEY, id TEXT UNIQUE, datos TEXT)")
        db.execute("INSERT INTO platos(id, datos) VALUES ('viejo', ?)", (json.dumps({"nombre": "Viejo", "precio_c": 300}),))
        db.commit()
        db.close()
        sv = Servicio(d, cliente=ClienteFalso([]))
        self.addCleanup(sv.cerrar)
        self.assertEqual(list(sv.menu), ["viejo"])
        self.assertEqual(sv.fotos(), {})
        Servicio(d, cliente=ClienteFalso([])).cerrar()  # segunda apertura: migración idempotente


if __name__ == "__main__":
    unittest.main()
