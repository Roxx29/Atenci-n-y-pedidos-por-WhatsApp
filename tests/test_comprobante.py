"""Comprobante de Yappy. Ejecutar: python -m unittest tests.test_comprobante"""
import datetime as dt
import http.client
import os
import socket
import threading
import socket
import threading
import struct
import types
import time
import unittest
import zlib

from motor import herramientas, panel_web, web
from tests.test_web import CID, PEDIDO, Base

EXIF = b"Exif\x00\x00GPS-SECRETO"


def _jpeg_real():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (4, 4), (180, 40, 40)).save(b, "JPEG")
    return b.getvalue()


JPEG_REAL = _jpeg_real()  # el servidor decodifica con Pillow: hace falta una imagen de verdad


def jpeg(extra=True):
    seg = lambda m, d: b"\xff" + bytes([m]) + struct.pack(">H", len(d) + 2) + d
    return JPEG_REAL[:2] + (seg(0xE1, EXIF) + seg(0xFE, b"COMENTARIO") if extra else b"") + JPEG_REAL[2:]


def limpio(ext):
    """Lo que guarda el servidor: la imagen sin metadatos, recodificada (motor.herramientas.recodificar_imagen)."""
    return herramientas.recodificar_imagen(jpeg(False) if ext == "jpg" else png(False), ext)


def png(extra=True):
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (herramientas.PNG_SIG + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)) + (ch(b"tEXt", b"Author\x00SECRETO") + ch(b"eXIf", EXIF) + ch(b"tIME", b"\x00" * 7) if extra else b"")
            + ch(b"IDAT", zlib.compress(b"\x00\x00")) + ch(b"IEND", b""))


class Comprobante(Base):
    def setUp(self):
        self.arrancar()
        _, _, self.ck = self.entrar()
        self.pid = herramientas.crear_pedido(self.sv, PEDIDO["items"], "recogida", "Ana", CID)["pedido_id"]
        herramientas.confirmar_pedido(self.sv, self.pid)
        self.carpeta = self.sv.dir / "comprobantes"

    def subir(self, datos, tipo="image/jpeg", pid=None, ck="", ref=""):
        return self.js("POST", f"/comprobante?pedido={pid or self.pid}" + (f"&referencia={ref}" if ref else ""), crudo=datos, tipo=tipo, cookie=ck or self.ck)

    def archivos(self):
        return [types.SimpleNamespace(name=n, read_bytes=lambda d=d: bytes(d)) for n, d in self.sv._q("SELECT nombre, datos FROM comprobantes ORDER BY nombre")]

    def meter(self, nombre, datos, antiguedad=0):
        self.sv._q("INSERT INTO comprobantes(nombre, datos, creado) VALUES (?,?,?)", (nombre, datos, time.time() - antiguedad))

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
            self.assertEqual(b, limpio(ext))
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
        r = herramientas.buscar(self.sv, "pedido", "", CID)
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
        self.meter("a" * 32 + ".jpg", jpeg())
        herramientas.cancelar_pedido(self.sv, self.pid)
        self.assertEqual(self.archivos(), [])

    def test_purgar(self):
        self.subir(jpeg())
        viejo = self.archivos()[0]
        self.meter("b" * 32 + ".png", png(), 3600)
        self.meter("c" * 32 + ".png", png())
        reciente = types.SimpleNamespace(name="c" * 32 + ".png")
        herramientas.purgar_comprobantes(self.sv)
        self.assertEqual(sorted(f.name for f in self.archivos()), sorted([viejo.name, reciente.name]))  # ni el reciente ni el del pedido
        self.sv.actualizar(self.pid, {"comprobante_at": (self.sv.ahora() - dt.timedelta(days=8)).isoformat()})
        herramientas.purgar_comprobantes(self.sv)
        self.assertNotIn(viejo, self.archivos())
        self.assertEqual(self.pedido()["comprobante"], "")
        herramientas.purgar_comprobantes(self.sv)  # idempotente / archivo ya inexistente

    def test_leer_comprobante_seguro(self):
        self.subir(jpeg())
        p = self.pedido()
        self.assertEqual(herramientas.leer_comprobante(self.sv, p), (limpio("jpg"), "jpg"))
        for malo in ("../servicio.yaml", "..\servicio.yaml", "x.jpg", "a" * 32 + ".exe", "a" * 32 + ".jpg", "/etc/passwd", "", None):
            self.assertIsNone(herramientas.leer_comprobante(self.sv, {"comprobante": malo}))
        self.assertIsNone(herramientas.leer_comprobante(self.sv, {}))

    def test_panel_sirve_desde_la_bd(self):
        self.subir(jpeg())
        self.assertEqual(panel_web.comprobante(self.sv, self.pid), (limpio("jpg"), "image/jpeg"))

    def test_importa_archivos_viejos_una_vez(self):
        self.carpeta.mkdir(exist_ok=True)
        (self.carpeta / ("d" * 32 + ".png")).write_bytes(png())
        (self.carpeta / "otro.txt").write_text("x")
        herramientas.importar_comprobantes(self.sv)
        self.assertEqual([(f.name, f.read_bytes()) for f in self.archivos()], [("d" * 32 + ".png", png())])
        self.assertEqual(sorted(f.name for f in self.carpeta.iterdir()), ["otro.txt"])  # el importado se borra, lo ajeno no se toca
        herramientas.importar_comprobantes(self.sv)
        self.assertEqual(len(self.archivos()), 1)

    def test_importar_no_borra_si_la_bd_no_tiene_lo_mismo(self):
        self.carpeta.mkdir(exist_ok=True)
        n = "e" * 32 + ".png"
        (self.carpeta / n).write_bytes(png())
        self.sv._q("INSERT INTO comprobantes(nombre, datos, creado) VALUES (?,?,0)", (n, b"otra cosa"))  # el nombre ya existe con otro contenido
        herramientas.importar_comprobantes(self.sv)
        self.assertTrue((self.carpeta / n).exists())


class Extra(Base):
    def setUp(self):
        orig = web.TIMEOUT_S
        web.TIMEOUT_S = 1  # el handler lo lee al crear el servidor
        self.addCleanup(setattr, web, "TIMEOUT_S", orig)
        Comprobante.setUp(self)

    subir = Comprobante.subir
    archivos = Comprobante.archivos
    pedido = Comprobante.pedido

    def test_purga_periodica(self):
        self.subir(jpeg())
        self.sv.actualizar(self.pid, {"comprobante_at": (self.sv.ahora() - dt.timedelta(days=8)).isoformat()})
        parar = web.iniciar_purga(self.sv, cada=0.05)
        self.addCleanup(parar.set)
        for _ in range(100):
            if self.pedido()["comprobante"] == "":
                break
            time.sleep(0.05)
        self.assertEqual((self.pedido()["comprobante"], self.archivos()), ("", []))

    def test_pago_rechazado(self):
        self.subir(jpeg())
        herramientas.marcar_pedido(self.sv, self.pid, "pago_rechazado")
        p = self.pedido()
        self.assertEqual((p["estado"], p["comprobante"], self.archivos()), ("confirmado", "", []))
        self.assertIn("pago_rechazado_at", p)
        self.assertIn("comprobante_borrado_at", p)
        m = self.sv.pendientes()[-1]["texto"]
        self.assertEqual(m, f"No pudimos verificar su pago del pedido #{self.pid}. Revise los datos y envíe de nuevo su comprobante o la referencia.")
        with self.assertRaises(ValueError):  # ya está confirmado: no es rechazable
            herramientas.marcar_pedido(self.sv, self.pid, "pago_rechazado")
        self.assertEqual(self.subir(png(), tipo="image/png")[0], 200)  # puede reenviar
        self.assertNotIn("pago_rechazado", herramientas.CATALOGO)
        with self.assertRaises(ValueError):
            herramientas.ejecutar(self.sv, "pago_rechazado", {"pedido_id": self.pid}, CID)

    def test_modelo_no_ve_fechas(self):
        self.subir(jpeg())
        herramientas.marcar_pedido(self.sv, self.pid, "pago_rechazado")
        self.subir(jpeg())
        herramientas.marcar_pedido(self.sv, self.pid, "pagado")
        r, = herramientas.buscar(self.sv, "pedido", "", CID)
        for k in ("comprobante", "comprobante_at", "comprobante_borrado_at", "pago_rechazado_at"):
            self.assertNotIn(k, r)

    def test_timeout_cuerpo_incompleto(self):
        cab = f"POST /comprobante?pedido={self.pid} HTTP/1.1\r\nHost: x\r\nContent-Type: image/jpeg\r\nCookie: {self.ck}\r\nContent-Length: 5000\r\n\r\n"
        with socket.create_connection(("127.0.0.1", self.puerto), timeout=10) as s:
            s.sendall(cab.encode() + b"0123456789")
            t0, resp = time.time(), b""
            try:
                while c := s.recv(4096):
                    resp += c
            except OSError:
                pass
        self.assertLess(time.time() - t0, 8)
        self.assertTrue(resp == b"" or b" 400 " in resp.split(b"\r\n")[0], resp[:40])
        self.assertEqual(self.archivos(), [])

    def test_jpeg_limpieza(self):
        seg = lambda m, d: b"\xff" + bytes([m]) + struct.pack(">H", len(d) + 2) + d
        base = (b"\xff\xd8" + seg(0xE0, b"JFIF\x00") + seg(0xE2, b"ICC_PROFILE\x00") + seg(0xEE, b"Adobe") + seg(0xE1, EXIF) + seg(0xED, b"IPTC")
                + seg(0xEB, b"XXX") + seg(0xEF, b"YYY") + seg(0xFE, b"COMM") + b"\xff\x01" + b"\xff\xd0" + seg(0xDB, b"\x00" * 65)
                + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\xff\xd9")
        out = herramientas.limpiar_imagen(base + b"BASURA-POSTERIOR", "jpg")
        for q in (b"Exif", b"IPTC", b"XXX", b"YYY", b"COMM", b"BASURA"):
            self.assertNotIn(q, out)
        for q in (b"JFIF", b"ICC_PROFILE", b"Adobe", b"\xff\x01\xff\xd0"):  # RST/TEM sin longitud intactos
            self.assertIn(q, out)
        self.assertTrue(out.endswith(b"\xff\xd9"))

    def test_subidas_concurrentes_sin_huerfanos(self):
        self.subir(jpeg())
        viejo = self.pedido()  # `p` obsoleto para todos los hilos
        hilos = [threading.Thread(target=herramientas.subir_comprobante, args=(self.sv, viejo, "jpg", jpeg())) for _ in range(5)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual([f.name for f in self.archivos()], [self.pedido()["comprobante"]])


def json_txt(x):
    import json
    return json.dumps(x, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
