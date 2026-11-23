"""QR dinámicos y pedidos por mesa (módulo `mesas`). Ejecutar: python -m unittest tests.test_mesa"""
import binascii
import contextlib
import io
import json
import os
import struct
import unittest
import unittest.mock
import zlib
from pathlib import Path

from motor import herramientas, mesa, qr
from tests.test_alojar import Base as BaseAlojar
from tests.test_motor import herramienta, texto
from tests.test_web import CID, SECRETO, Base

ROOT = Path(__file__).resolve().parent.parent

try:
    import cv2
    import numpy as np
except ImportError:  # sin decodificador de referencia solo se prueban los vectores conocidos
    cv2 = None

PIN, PIN_P = "483920", "917364"
URL = "https://restaurante.example.com/m/k7x2m9qp"


def png(w=8, h=8):
    def trozo(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", binascii.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + trozo(b"IDAT", zlib.compress((b"\x00" + b"\xcc\x33\x33" * w) * h)) + trozo(b"IEND", b"")


def leer(m, logo=False, k=8):
    """Decodifica una matriz de QR con OpenCV (con el hueco del logo en negro: el peor caso)."""
    n = len(m)
    img = np.full(((n + 8) * k,) * 2, 255, np.uint8)
    for y, f in enumerate(m):
        for x, c in enumerate(f):
            if c:
                img[(y + 4) * k:(y + 5) * k, (x + 4) * k:(x + 5) * k] = 0
    if logo:
        a, b = qr.caja_logo(n)
        img[(a + 4) * k:(b + 4) * k, (a + 4) * k:(b + 4) * k] = 0
    for lector in (cv2.QRCodeDetectorAruco(), cv2.QRCodeDetector()):  # dos decodificadores independientes del generador: el primero que lea vale
        if t := lector.detectAndDecode(img)[0]:
            return t
    return ""


class GeneradorQR(unittest.TestCase):
    def test_vectores_conocidos(self):
        # ejemplo "HELLO WORLD" 1-M del tutorial de referencia: palabras de datos -> corrección Reed-Solomon
        datos = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.resto(datos, qr.divisor(10)), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])
        self.assertEqual(qr._formato("M", 0), 0b101010000010010)  # bits de formato del estándar
        self.assertEqual(qr._formato("H", 0), 0b001011010001001)

    @unittest.skipIf(cv2 is None, "sin OpenCV")
    def test_decodifica_en_todos_los_tamanos(self):
        for t in ("1", "HELLO WORLD", URL, "https://" + "d" * 60 + ".com/m/k7x2m9qp", "é" * 60, "u" * 200):
            self.assertEqual(leer(qr.matriz(t)), t)
            if len(t.encode()) <= 100:  # nivel H: menos capacidad
                self.assertEqual(leer(qr.matriz(t, "H")), t)

    @unittest.skipIf(cv2 is None, "sin OpenCV")
    def test_el_logo_no_rompe_la_lectura_y_respeta_el_area(self):
        for u in (URL, "https://x.io/m/k7x2m9qp", "https://" + "d" * 80 + ".com/m/k7x2m9qp"):
            m = qr.matriz(u, "H", qr.V_LOGO)
            a, b = qr.caja_logo(len(m))
            self.assertLessEqual((b - a) ** 2 / len(m) ** 2, 0.22)
            self.assertEqual(leer(m, logo=True), u)  # con el hueco ENTERO en negro
        # un hueco demasiado grande sí rompe un QR pequeño: por eso existe el límite
        with unittest.mock.patch.object(qr, "LOGO", 0.5):
            m = qr.matriz("https://x.io/m/k7x2m9qp", "H")
            self.assertNotEqual(leer(m, logo=True), "https://x.io/m/k7x2m9qp")

    def test_svg_estilos(self):
        for modulo in mesa.MODULOS:
            for esq in ("cuadrada", "redonda"):
                s = qr.svg(URL, modulo=modulo, esquinas=esq, color="#112233", fondo="#ffeedd", rotulo="Mesa 7 <&>")
                self.assertTrue(s.startswith("<svg") and s.endswith("</svg>"))
                self.assertIn("#112233", s)
                self.assertIn("Mesa 7 &lt;&amp;&gt;", s)  # el rótulo va escapado
        con = qr.svg(URL, logo="data:image/png;base64,AAAA")
        self.assertIn("<image", con)
        self.assertNotIn("<image", qr.svg(URL))


class Estilo(unittest.TestCase):
    def test_contraste(self):
        self.assertEqual(mesa.estilo_valido({})["color"], "#000000")
        self.assertEqual(mesa.estilo_valido({"color": "#1a237e", "fondo": "#ffffe0"})["fondo"], "#ffffe0")
        for malo in ({"color": "#ffffff", "fondo": "#000000"},  # invertido
                     {"color": "#cccccc", "fondo": "#ffffff"},  # poco contraste
                     {"color": "red"}, {"color": "#12345"}, {"modulo": "estrella"}, {"inventado": 1}, {"texto": "x" * 31}, {"marco": "si"}):
            with self.assertRaises(ValueError, msg=malo):
                mesa.estilo_valido(malo)

    def test_logo_validado_por_contenido(self):
        for bueno, tipo in ((png(), "image/png"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
                            (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><circle r="1"/></svg>', "image/svg+xml")):
            self.assertEqual(mesa._tipo_logo(bueno), tipo)
        for malo in (b"GIF89a....", b"", b"jpeg", b'<svg><script>alert(1)</script></svg>', b'<svg onload="x()"></svg>',
                     b'<svg><a href="https://x"/></svg>', b"<html><svg></svg></html>", b'<svg><foreignObject/></svg>'):
            self.assertIsNone(mesa._tipo_logo(malo), malo[:30])
        with self.assertRaises(ValueError):
            mesa.guardar_logo(None, b"x" * (mesa.MAX_LOGO + 1))  # el tamaño se mira antes de tocar la BD


class Servidor(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN, "PANEL_PIN_PERSONAL": PIN_P, "PANEL_PIN_HASH": ""})
        p.start()
        self.addCleanup(p.stop)

    def listo(self, respuestas=(), modulos=("mesas",)):
        self.arrancar(respuestas)
        self.sv._cfg["modulos"] = list(modulos)
        self.sv._cfg["herramientas"] = [*self.sv._cfg["herramientas"], "pagar_efectivo"]
        self.ck = self.login()

    def login(self, pin=PIN):
        return (self.req("POST", "/panel/entrar", {"pin": pin})[0].getheader("Set-Cookie") or "").split(";")[0]

    def api(self, metodo, ruta, cuerpo=None, ck=None):
        return self.js(metodo, "/panel/dueno/" + ruta, cuerpo if metodo == "POST" else None, cookie=ck or self.ck)

    def codigo(self, **k):
        d = {"etiqueta": "Mesa 7", "tipo": "pedir", "mesa": 7, **k}
        i = self.api("POST", "qr_codigo", d)[1]["id"]
        return next(c for c in self.api("GET", "qr_codigos")[1]["codigos"] if c["id"] == i)

    def location(self, codigo):
        r, _ = self.req("GET", f"/m/{codigo}")
        return r.status, r.getheader("Location")

    def test_codigos_aleatorios_y_unicos(self):
        self.listo()
        self.assertEqual(self.api("POST", "qr_generar", {"desde": 1, "hasta": 40})[1]["creados"], 40)
        self.assertEqual(self.api("POST", "qr_generar", {"desde": 1, "hasta": 45})[1]["creados"], 5)  # las que ya existen no se repiten
        cs = self.api("GET", "qr_codigos")[1]["codigos"]
        self.assertEqual(len({c["codigo"] for c in cs}), 45)
        self.assertTrue(all(len(c["codigo"]) == 8 and set(c["codigo"]) <= set(mesa.ALFABETO) for c in cs))
        self.assertEqual(cs[0]["url"], f"http://127.0.0.1:{self.puerto}/m/{cs[0]['codigo']}")
        self.assertNotIn("2026", cs[0]["codigo"])  # nada deriva de la fecha

    def test_destino_cambia_sin_cambiar_el_qr(self):
        self.listo()
        c = self.codigo()
        self.assertEqual(self.location(c["codigo"]), (302, f"/?m={c['codigo']}"))
        for cambios, esperado in (({"tipo": "menu"}, (302, "/?solo=menu")),
                                  ({"tipo": "resena", "destino": "https://g.page/r/abc"}, (302, "https://g.page/r/abc")),
                                  ({"tipo": "promo", "destino": "https://promo.example.com/hoy"}, (302, "https://promo.example.com/hoy")),
                                  ({"tipo": "pedir", "mesa": 7, "destino": ""}, (302, f"/?m={c['codigo']}"))):
            self.assertEqual(self.api("POST", "qr_codigo", {"id": c["id"], **cambios})[0], 200)
            self.assertEqual(self.location(c["codigo"]), esperado)  # el MISMO código lleva a otro destino
        self.api("POST", "qr_codigo", {"id": c["id"], "tipo": "promo", "destino": "2x1 en bebidas <hoy>"})
        r, raw = self.req("GET", f"/m/{c['codigo']}")
        self.assertEqual(r.status, 200)
        self.assertIn(b"2x1 en bebidas &lt;hoy&gt;", raw)  # texto escapado
        self.api("POST", "qr_codigo", {"id": c["id"], "tipo": "pausado"})
        r, raw = self.req("GET", f"/m/{c['codigo']}")
        self.assertIn("Mesa no disponible".encode(), raw)
        self.assertEqual(self.api("GET", "qr_codigos")[1]["codigos"][0]["codigo"], c["codigo"])

    def test_destinos_invalidos(self):
        self.listo()
        for malo in ({"tipo": "resena", "destino": "javascript:alert(1)"}, {"tipo": "resena", "destino": "http://x y"}, {"tipo": "resena", "destino": ""},
                     {"tipo": "promo", "destino": "javascript:alert(1)"}, {"tipo": "promo", "destino": ""}, {"tipo": "pedir", "mesa": None},
                     {"mesa": 101}, {"mesa": 0}, {"mesa": True}, {"etiqueta": ""}, {"etiqueta": "a\nb"}, {"tipo": "otro"}):
            self.assertEqual(self.api("POST", "qr_codigo", {"etiqueta": "Mesa 1", "tipo": "pedir", "mesa": 1, **malo})[0], 409, malo)
        self.assertEqual(self.api("GET", "qr_codigos")[1]["codigos"], [])

    def test_desactivar_y_regenerar_invalidan_el_viejo(self):
        self.listo()
        c = self.codigo()
        self.api("POST", "qr_codigo", {"id": c["id"], "activo": False})
        self.assertEqual(self.location(c["codigo"])[0], 404)
        self.api("POST", "qr_codigo", {"id": c["id"], "activo": True})
        self.assertEqual(self.location(c["codigo"])[0], 302)
        nuevo = self.api("POST", "qr_regenerar", {"id": c["id"]})[1]["codigo"]
        self.assertNotEqual(nuevo, c["codigo"])
        self.assertEqual(self.location(c["codigo"])[0], 404)  # el viejo ya no sirve...
        self.assertEqual(self.location(nuevo)[0], 302)  # ...y el nuevo sí
        st, d, _ = self.js("POST", "/entrar", {"nombre": "", "codigo": c["codigo"], "consentimiento": True, "version_aviso": "v1"})
        self.assertEqual(st, 400)  # con el código viejo no se entra sin teléfono
        self.assertEqual(self.api("POST", "qr_borrar", {"id": c["id"]})[0], 200)
        self.assertEqual(self.location(nuevo)[0], 404)

    def test_escaneos_contados_sin_datos_personales(self):
        self.listo()
        c = self.codigo()
        for _ in range(3):
            self.location(c["codigo"])
        self.location("zzzzzzzz")  # desconocido: no cuenta
        c = self.api("GET", "qr_codigos")[1]["codigos"][0]
        self.assertEqual((c["hoy"], c["escaneos"], c["dias7"][-1], len(c["dias7"])), (3, 3, {"dia": "2026-11-02", "n": 3}, 7))
        self.assertEqual({t for t, in self.sv._q("SELECT name FROM pragma_table_info('qr_dias')")}, {"codigo_id", "dia", "n"})

    def test_rate_limit_de_escaneos_por_ip(self):
        self.listo()
        c = self.codigo()
        estados = [self.req("GET", f"/m/{c['codigo']}")[0].status for _ in range(32)]
        self.assertEqual((estados[:30].count(302), estados[30:]), (30, [429, 429]))

    def test_sin_el_modulo_no_hay_nada(self):
        self.listo(modulos=())
        self.assertEqual(self.api("GET", "qr_codigos")[0], 404)
        self.assertEqual(self.req("GET", "/panel/qr", cookie=self.ck)[0].status, 404)
        self.assertEqual(self.req("GET", "/m/abcdefgh")[0].status, 404)

    def test_solo_el_dueno(self):
        self.listo()
        per = self.login(PIN_P)
        self.assertEqual(self.api("GET", "qr_codigos", ck=per)[0], 403)
        self.assertEqual(self.api("POST", "qr_codigo", {"etiqueta": "x", "tipo": "menu"}, ck=per)[0], 403)
        self.assertEqual(self.req("GET", "/panel/qr", cookie=per)[0].status, 403)
        self.assertEqual(self.req("GET", "/panel/qr")[0].status, 401)

    def test_pedido_de_mesa_de_punta_a_punta(self):
        PED = {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "mesa", "nombre": "Mesa 7"}
        self.listo([herramienta("crear_pedido", PED), texto("¿confirma?"), herramienta("confirmar_pedido", {"pedido_id": 4}), texto("ok"),
                    herramienta("pagar_efectivo", {"pedido_id": 4}), texto("pago en mesa")])
        c = self.codigo()
        self.assertEqual(self.js("GET", f"/config?m={c['codigo']}")[1]["mesa"], 7)
        self.assertNotIn("mesa", self.js("GET", "/config?m=zzzzzzzz")[1])  # código inválido: se ignora, sin error
        st, d, r = self.js("POST", "/entrar", {"nombre": "Ana", "codigo": c["codigo"], "consentimiento": True, "version_aviso": "v1"})
        ck = (r.getheader("Set-Cookie") or "").split(";")[0]
        self.assertEqual((st, d["mesa"]), (200, 7))
        self.assertIn("Max-Age=14400", r.getheader("Set-Cookie"))  # una visita, no un día
        cid = self.sv.listar("consentimiento")[0]["telefono"]
        self.assertEqual(mesa.de_cid(cid), 7)
        self.js("POST", "/chat", {"texto": "dos sancochos"}, cookie=ck)
        pre = self.llamadas[0][1]
        self.assertIn("MESA 7", pre)
        self.assertIn("NO online", pre)
        self.assertIn("NO preguntes dirección, teléfono, entrega ni recogida", pre)
        self.assertIn("Mesa 7", pre)
        p = self.sv.listar("pedido")[0]
        self.assertEqual((p["modalidad"], p["mesa"], p["envio_c"], p["direccion"], p["telefono"]), ("mesa", 7, 0, "", cid))
        with contextlib.redirect_stdout(io.StringIO()):
            self.js("POST", "/chat", {"texto": "sí"}, cookie=ck)
            self.js("POST", "/chat", {"texto": "pago en la mesa"}, cookie=ck)
        p = self.sv.listar("pedido")[0]
        self.assertEqual((p["estado"], p["espera_personal"], p["metodo"]), ("confirmado", True, "caja"))  # espera al personal
        mio = self.js("GET", "/pedidos", cookie=ck)[1]["pedidos"][0]  # seguimiento por la cookie, sin cuenta
        self.assertEqual((mio["mesa"], mio["modalidad"], mio["espera_personal"]), (7, "mesa", True))
        pan = self.js("GET", "/panel/estado", cookie=self.ck)[1]["pedidos"][0]
        self.assertEqual((pan["mesa"], pan["telefono"], pan["espera_personal"]), (7, None, True))  # «Mesa 7» y el cid interno no sale
        self.assertNotIn(cid, json.dumps(self.js("GET", f"/panel/pedido/{p['id']}", cookie=self.ck)[1]))
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": p["id"], "estado": "pagado"}, cookie=self.ck)[0], 200)  # el toque del personal
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": p["id"], "estado": "en_preparacion"}, cookie=self.ck)[0], 200)
        self.assertNotIn("en_camino", self.js("POST", "/panel/marcar", {"pedido_id": p["id"], "estado": "listo"}, cookie=self.ck)[1]["pedido"]["acciones"])
        self.assertTrue(any("Mesa 7" in a["texto"] for a in self.sv.listar("aviso")))  # el aviso dice la mesa
        self.assertFalse(self.sv.listar("pedido")[0]["espera_personal"])
        self.js("POST", "/panel/marcar", {"pedido_id": p["id"], "estado": "entregado"}, cookie=self.ck)
        self.assertEqual(self.js("GET", "/ultimo", cookie=ck)[1]["pedido"]["estado"], "entregado")  # el final se ve 30 min (gracias y repetir)
        self.assertEqual(self.js("GET", "/pedidos", cookie=ck)[1], {"pedidos": []})
        self.assertEqual(self.req("GET", "/ultimo")[0].status, 401)
        # recargar la página con el mismo código conserva la sesión de la mesa
        _, _, r2 = self.js("POST", "/entrar", {"nombre": "Ana", "codigo": c["codigo"], "consentimiento": True, "version_aviso": "v1"}, cookie=ck)
        self.assertEqual((r2.getheader("Set-Cookie") or "").split(";")[0].split(".")[0], ck.split(".")[0])

    def test_mesa_exige_nombre_y_nunca_telefono(self):
        self.listo()
        c = self.codigo()
        base = {"codigo": c["codigo"], "consentimiento": True, "version_aviso": "v1"}
        for malo in ({}, {"nombre": ""}, {"nombre": "   "}, {"nombre": "x" * 61}, {"nombre": 5}):
            self.assertEqual(self.js("POST", "/entrar", {**base, **malo})[0], 400)
        st, d, r = self.js("POST", "/entrar", {**base, "nombre": "Ana"})
        self.assertEqual((st, d["mesa"], d["nombre"]), (200, 7, "Ana"))
        self.assertEqual(mesa.de_cid(self.sv.listar("consentimiento")[0]["telefono"]), 7)  # sin teléfono real
        # con teléfono y código: flujo normal (no es de mesa)
        st, d, _ = self.js("POST", "/entrar", {**base, "nombre": "Luis", "telefono": "50761234567"})
        self.assertEqual((st, "mesa" in d), (200, False))
        html = (ROOT / "motor" / "web_index.html").read_text(encoding="utf-8")
        self.assertNotIn("nombre (opcional)", html)

    def test_la_pestana_qr_existe_solo_para_dueno_con_modulo(self):
        html = (ROOT / "motor" / "panel_web.html").read_text(encoding="utf-8")
        self.assertIn('data-vista="qr" id="nav-qr" hidden', html)
        self.assertIn('id="v-qr"', html)
        self.assertIn('st.rol !== "dueno" || !m.includes("mesas")', html)
        self.assertNotIn('window.open("/panel/qr"', html)
        self.listo()
        r, pag = self.req("GET", "/panel/qr?embed=1", cookie=self.ck)
        self.assertIn("frame-ancestors 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn("Cómo funciona para tu cliente".encode(), pag)

    def test_sin_aceptacion_va_directo_a_cocina(self):
        self.listo()
        self.sv._cfg["pedidos"]["mesa_aceptar"] = False
        cid = mesa.nuevo_cid(3)
        pid = herramientas.crear_pedido(self.sv, telefono=cid, items=[{"id": "sancocho", "cantidad": 2}], modalidad="mesa", nombre="")["pedido_id"]
        self.assertEqual(self.sv.listar("pedido")[0]["nombre"], "Mesa 3")  # nombre opcional
        with contextlib.redirect_stdout(io.StringIO()):
            herramientas.confirmar_pedido(self.sv, pid)
            herramientas.pagar_efectivo(self.sv, pid)
        self.assertEqual(self.sv.listar("pedido")[0]["estado"], "pagado")

    def test_la_mesa_no_se_inventa(self):
        self.listo()
        with self.assertRaises(ValueError):  # un teléfono no puede pedir «mesa»
            herramientas.crear_pedido(self.sv, telefono="50760001111", items=[{"id": "sancocho", "cantidad": 2}], modalidad="mesa", nombre="Ana")
        for cid in ("mesa7", "mesa7-xyz", "mesa0007-aaaaaaaa", "../mesa7-aabbccdd", "507mesa7-aabbccdd"):
            self.assertIsNone(mesa.de_cid(cid))
        # sin código válido, /entrar sin teléfono es el error de siempre y la cookie normal no pide mesa
        st, d, _ = self.js("POST", "/entrar", {"nombre": "Ana", "codigo": "zzzzzzzz", "consentimiento": True, "version_aviso": "v1"})
        self.assertEqual((st, d["error"]), (400, "Teléfono inválido"))
        ck = self.entrar()[2]
        self.assertNotIn("mesa", self.js("GET", "/config", cookie=ck)[1])

    def test_ultimo_pedido_es_solo_de_quien_lo_hizo(self):
        self.listo()
        pid = herramientas.crear_pedido(self.sv, telefono=CID, **{k: v for k, v in {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "recogida", "nombre": "Ana"}.items()})["pedido_id"]
        self.sv.actualizar(pid, {"estado": "entregado", "estado_at": self.sv.ahora().isoformat()}, tipo="pedido")
        mio, otro = self.entrar()[2], self.entrar(tel="50762223333")[2]
        self.assertEqual(self.js("GET", "/ultimo", cookie=mio)[1]["pedido"]["id"], pid)
        self.assertIsNone(self.js("GET", "/ultimo", cookie=otro)[1]["pedido"])

    def test_limite_de_pedidos_por_mesa(self):
        self.listo()
        for i in range(mesa.MAX_ACTIVOS_MESA):
            herramientas.crear_pedido(self.sv, telefono=mesa.nuevo_cid(9), items=[{"id": "sancocho", "cantidad": 2}], modalidad="mesa", nombre="")
        with self.assertRaisesRegex(ValueError, "mesa 9 ya tiene"):
            herramientas.crear_pedido(self.sv, telefono=mesa.nuevo_cid(9), items=[{"id": "sancocho", "cantidad": 2}], modalidad="mesa", nombre="")
        herramientas.crear_pedido(self.sv, telefono=mesa.nuevo_cid(10), items=[{"id": "sancocho", "cantidad": 2}], modalidad="mesa", nombre="")  # otra mesa, sin problema

    def test_estilo_logo_svg_y_hoja(self):
        self.listo()
        self.sv._cfg["marca"] = "Sazón"
        c = self.codigo()
        self.assertEqual(self.api("POST", "qr_estilo", {"estilo": {"color": "#fff", "fondo": "#000"}})[0], 409)  # ilegible: se rechaza
        self.assertEqual(self.api("POST", "qr_vista", {"estilo": {"color": "#fff", "fondo": "#000"}})[0], 409)
        self.assertEqual(self.api("POST", "qr_estilo", {"estilo": {"color": "#8b1a1a", "modulo": "puntos", "esquinas": "redonda", "texto": "Escanea y pide"}})[0], 200)
        self.assertIn("<svg", self.api("POST", "qr_vista", {"estilo": {"modulo": "redondeado"}})[1]["svg"])
        for tipo, cuerpo, esperado in (("image/png", png(), 200), ("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', 200),
                                       ("image/svg+xml", b"<svg><script/></svg>", 415), ("image/gif", b"GIF89a", 415), ("image/png", b"no soy png", 415),
                                       ("image/png", png() + b"x" * mesa.MAX_LOGO, 413)):
            r, _ = self.req("POST", "/panel/dueno/qr_logo", crudo=cuerpo, tipo=tipo, cookie=self.ck)
            self.assertEqual(r.status, esperado, tipo)
        self.api("POST", "qr_logo_borrar", {})
        self.req("POST", "/panel/dueno/qr_logo", crudo=png(), tipo="image/png", cookie=self.ck)
        r, svg = self.req("GET", f"/panel/qr/svg/{c['id']}", cookie=self.ck)
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertIn(b"<image", svg)  # con logo
        self.assertIn(b"#8b1a1a", svg)  # con el estilo guardado
        for plantilla in mesa.PLANTILLAS:
            r, h = self.req("GET", f"/panel/qr/hoja?plantilla={plantilla}", cookie=self.ck)
            self.assertEqual(r.status, 200)
            self.assertIn(f'class="{plantilla}"'.encode(), h)
            self.assertIn(b"Mesa 7", h)
        self.assertEqual(self.req("GET", "/panel/qr/hoja?plantilla=x", cookie=self.ck)[0].status, 400)
        pag = self.req("GET", "/panel/qr", cookie=self.ck)[1]
        self.assertIn("Sazón".encode(), pag)  # la marca del cliente, no la del motor
        self.assertNotIn(b"DeliFood", pag)
        self.assertNotIn(b"estatico/tema.css", pag)  # el tema ya va incrustado

    @unittest.skipIf(cv2 is None, "sin OpenCV")
    def test_el_qr_de_un_codigo_apunta_a_su_url(self):
        self.listo()
        c = self.codigo()
        self.assertEqual(leer(qr.matriz(c["url"])), c["url"])
        self.assertEqual(leer(qr.matriz(c["url"], "H", qr.V_LOGO), logo=True), c["url"])


class Aislamiento(BaseAlojar):
    def setUp(self):
        super().setUp()
        (self.d / "clientes.yaml").write_text("clientes:\n  a: {carpeta: a, dominio: a.com, modulos: [mesas]}\n  b: {carpeta: b, dominio: b.com, modulos: [mesas]}\n  c: {carpeta: c, dominio: c.com, modulos: []}\n", encoding="utf-8")
        self.arrancar()

    def test_el_codigo_de_un_cliente_no_vale_en_otro(self):
        a, b = self.srv.servicios["a"], self.srv.servicios["b"]
        ca = mesa.crear(a, {"etiqueta": "Mesa 7", "tipo": "pedir", "mesa": 7})["codigo"]
        cb = mesa.crear(b, {"etiqueta": "Mesa 7", "tipo": "pedir", "mesa": 7})["codigo"]
        self.assertEqual(self.req("a.com", "GET", f"/m/{ca}")[0], 302)
        self.assertEqual(self.req("b.com", "GET", f"/m/{ca}")[0], 404)  # el de A en B no existe
        self.assertEqual(self.req("a.com", "GET", f"/m/{cb}")[0], 404)
        self.assertNotIn("mesa", json.loads(self.req("b.com", "GET", f"/config?m={ca}")[1]))
        entrar = lambda host, c: self.req(host, "POST", "/entrar", {"nombre": "Ana", "codigo": c, "consentimiento": True, "version_aviso": "v1"})
        self.assertEqual(entrar("b.com", ca)[0], 400)
        st, raw, ck = entrar("a.com", ca)
        self.assertEqual(st, 200)
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)  # la cookie de mesa de A no abre B
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("c.com", "GET", f"/m/{ca}")[0], 404)  # c no tiene el módulo (ni el código)


if __name__ == "__main__":
    unittest.main()
