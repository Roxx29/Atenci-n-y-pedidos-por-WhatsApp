"""Comprobante de Yappy. Ejecutar: python -m unittest tests.test_comprobante"""
import datetime as dt
import http.client
import os
import struct
import time
import unittest
import zlib

from motor import herramientas, panel
from tests.test_web import PEDIDO, TEL, Base

EXIF = b"Exif\x00\x00GPS-SECRETO"


def jpeg(extra=True):
    seg = lambda m, d: b"\xff" + bytes([m]) + struct.pack(">H", len(d) + 2) + d
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + (seg(0xE1, EXIF) + seg(0xFE, b"COMENTARIO") if extra else b"")
            + seg(0xDB, b"\x00" * 65) + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\xff\xd9")


def png(extra=True):
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (herramientas.PNG_SIG + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)) + (ch(b"tEXt", b"Author\x00SECRETO") + ch(b"eXIf", EXIF) + ch(b"tIME", b"\x00" * 7) if extra else b"")
            + ch(b"IDAT", zlib.compress(b"\x00\x00")) + ch(b"IEND", b""))


class Comprobante(Base):
    def setUp(self):
        self.arrancar()
        _, _, self.ck = self.entrar()
        self.pid = herramientas.crear_pedido(self.sv, PEDIDO["items"], "recogida", "Ana", TEL)["pedido_id"]
        herramientas.confirmar_pedido(self.sv, self.pid)
        self.carpeta = self.sv.dir / "comprobantes"

    def subir(self, datos, tipo="image/jpeg", pid=None, ck="", ref=""):
        return self.js("POST", f"/comprobante?pedido={pid or self.pid}" + (f"&referencia={ref}" if ref else ""), crudo=datos, tipo=tipo, cookie=ck or self.ck)

    def archivos(self):
        return list(self.carpeta.iterdir()) if self.carpeta.exists() else []

    def pedido(self):
        return next(p for p in self.sv.listar("pedido") if p["id"] == self.pid)

    def test_rechazos(self):
        self.assertEqual(self.req("POST", f"/comprobante?pedido={self.pid}", crudo=jpeg(), tipo="image/jpeg")[0].status, 401)
        self.assertEqual(self.subir(jpeg(), pid=999)[0], 404)
        _, _, otro = self.entrar("50760002222", "Luis")
        self.assertEqual(self.subir(jpeg(), ck=otro)[0], 404)  # ajeno = inexistente
        self.assertEqual(self.subir(jpeg(), tipo="image/gif")[0], 415)
        self.assertEqual(self.subir(b"%PDF-1.4 falso")[0], 415)  # cabecera mágica falsa
        self.assertEqual(self.subir(png(), tipo="image/jpeg")[0], 415)  # no coincide con Content-Type
        self.assertEqual(self.subir(jpeg()[:30])[0], 400)  # truncada
        self.assertEqual(self.subir(jpeg(), ref="x" * 41)[0], 400)
        self.assertEqual(self.archivos(), [])
        self.assertEqual(self.pedido()["estado"], "confirmado")

    def test_demasiado_grande_sin_leer(self):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.request("POST", f"/comprobante?pedido={self.pid}", jpeg(), {"Content-Type": "image/jpeg", "Cookie": self.ck, "Content-Length": str(5 * 1024 * 1024 + 1)})
        self.assertEqual(c.getresponse().status, 413)

    def test_estado_incorrecto_409(self):
        self.sv.actualizar(self.pid, {"estado": "por_confirmar"})
        st, d, _ = self.subir(jpeg())
        self.assertEqual((st, d["error"]), (409, "El pedido no está esperando pago"))

    def test_flujo_jpeg_y_png_sin_metadatos(self):
        for datos, ext, marca in ((jpeg(), "jpg", b"image/jpeg"), (png(), "png", b"image/png")):
            st, d, _ = self.subir(datos, tipo=marca.decode(), ref="YP123")
            self.assertEqual((st, d["pedido"]["estado"]), (200, "pago_reportado"))
            self.assertNotIn("comprobante", json_txt(d))
            f, = self.archivos()  # el reenvío borró el anterior
            self.assertRegex(f.name, rf"[0-9a-f]{{32}}\.{ext}")
            b = f.read_bytes()
            self.assertNotIn(b"SECRETO", b)
            self.assertNotIn(b"Exif", b)
            self.assertNotIn(b"COMENTARIO", b)
            self.assertEqual(b, jpeg(False) if ext == "jpg" else png(False))
            self.sv.actualizar(self.pid, {"estado": "confirmado"})  # para la vuelta con PNG
        p = self.pedido()
        self.assertEqual((p["metodo"], p["referencia"]), ("yappy", "YP123"))
        av = self.sv.listar("aviso")[-1]["texto"]
        self.assertEqual(av, f"Comprobante de Yappy recibido: verificar y marcar pagado: pedido #{self.pid}, total B/. 10.70.")

    def test_reenvio_reemplaza_y_referencia_por_defecto(self):
        self.subir(jpeg())
        a = self.archivos()[0].name
        self.assertEqual(self.pedido()["referencia"], "comprobante")
        self.assertEqual(self.subir(png(), tipo="image/png", ref="NUEVA")[0], 200)
        b, = self.archivos()
        self.assertNotEqual(a, b.name)
        self.assertEqual(self.pedido()["referencia"], "NUEVA")

    def test_web_no_sirve_ni_filtra(self):
        self.subir(jpeg())
        nombre = self.pedido()["comprobante"]
        for ruta in (f"/comprobantes/{nombre}", f"/{nombre}", f"/../comprobantes/{nombre}"):
            self.assertEqual(self.req("GET", ruta, cookie=self.ck)[0].status, 404)
        _, raw = self.req("GET", "/pedidos", cookie=self.ck)
        self.assertNotIn(nombre.encode(), raw)
        self.assertNotIn(b"comprobante", raw)

    def test_modelo_no_ve_archivo(self):
        self.subir(jpeg())
        nombre = self.pedido()["comprobante"]
        r = herramientas.buscar(self.sv, "pedido", "", TEL)
        self.assertNotIn(nombre, json_txt(r))
        self.assertNotIn("comprobantes", json_txt(r))
        self.assertEqual((r[0]["estado"], r[0]["metodo"], r[0]["referencia"]), ("pago_reportado", "yappy", "comprobante"))
        self.assertNotIn(nombre, json_txt(self.sv.listar("aviso")))

    def test_borrado_al_marcar_y_cancelar(self):
        self.subir(jpeg())
        p0 = self.pedido()
        herramientas.marcar_pedido(self.sv, self.pid, "pagado")
        p = self.pedido()
        self.assertEqual((self.archivos(), p["comprobante"], p["referencia"], p["metodo"], p["total_c"]), ([], "", "comprobante", "yappy", p0["total_c"]))
        self.assertIn("comprobante_borrado_at", p)
        # cancelar (desde confirmado un comprobante no existe; se fuerza el caso con el campo puesto a mano)
        self.sv.actualizar(self.pid, {"estado": "confirmado", "comprobante": "a" * 32 + ".jpg"})
        self.carpeta.mkdir(exist_ok=True)
        (self.carpeta / ("a" * 32 + ".jpg")).write_bytes(jpeg())
        herramientas.cancelar_pedido(self.sv, self.pid)
        self.assertEqual(self.archivos(), [])

    def test_purgar(self):
        self.subir(jpeg())
        viejo = self.archivos()[0]
        huerfano = self.carpeta / ("b" * 32 + ".png")
        huerfano.write_bytes(png())
        reciente = self.carpeta / ("c" * 32 + ".png")
        reciente.write_bytes(png())
        os.utime(huerfano, (time.time() - 3600,) * 2)
        herramientas.purgar_comprobantes(self.sv)
        self.assertEqual(sorted(f.name for f in self.archivos()), sorted([viejo.name, reciente.name]))  # ni el reciente ni el del pedido
        self.sv.actualizar(self.pid, {"comprobante_at": (self.sv.ahora() - dt.timedelta(days=8)).isoformat()})
        herramientas.purgar_comprobantes(self.sv)
        self.assertNotIn(viejo, self.archivos())
        self.assertEqual(self.pedido()["comprobante"], "")
        herramientas.purgar_comprobantes(self.sv)  # idempotente / archivo ya inexistente

    def test_ruta_comprobante_segura(self):
        self.subir(jpeg())
        p = self.pedido()
        self.assertEqual(panel.ruta_comprobante(self.sv, p), self.carpeta.resolve() / p["comprobante"])
        for malo in ("../servicio.yaml", "..\\servicio.yaml", "x.jpg", "a" * 32 + ".exe", "a" * 32 + ".jpg", "/etc/passwd", "", None):
            self.assertIsNone(panel.ruta_comprobante(self.sv, {"comprobante": malo}))
        self.assertIsNone(panel.ruta_comprobante(self.sv, {}))


def json_txt(x):
    import json
    return json.dumps(x, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
