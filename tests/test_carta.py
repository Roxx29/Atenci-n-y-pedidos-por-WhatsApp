"""Carta por foto: fotos de la carta de papel → borrador → Publicar. Modelo simulado, sin red.
Ejecutar: python -m unittest tests.test_carta"""
import base64
import json
import os
import unittest
import unittest.mock

from motor import carta
from tests.test_consola import Base as BaseConsola
from tests.test_fotos import imagen
from tests.test_motor import ClienteFalso, texto
from tests.test_panel_web import PIN
from tests.test_web import Base

CARTA = {"platos": [
    {"nombre": "Sancocho", "precio": 6.5, "categoria": "sopas", "descripcion": "De gallina"},  # ya existe en el menú de prueba
    {"nombre": "Ropa vieja", "precio": "9.25", "categoria": "platos", "descripcion": "Con arroz y tajadas"},
    {"nombre": "Ropa  VIEJA", "precio": 9, "categoria": "platos"},  # repetido en la misma foto
]}
ENV = {"PANEL_PIN": PIN, "PANEL_PIN_PERSONAL": "918273", "PANEL_PIN_HASH": "", "ANTHROPIC_BASE_URL": "", "LLM_VISION_MODEL": ""}


class Carta(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, ENV)
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()
        carta._usos.clear()

    def modelo(self, *salidas):
        self.sv._cliente = ClienteFalso([texto(s if isinstance(s, str) else json.dumps(s)) for s in salidas])
        return self.sv._cliente

    def test_feliz_borrador_sin_publicar_y_publicar(self):
        falso = self.modelo(CARTA)
        antes = {k: dict(v) for k, v in self.sv.menu.items()}
        r = carta.extraer(self.sv, [imagen(exif=True), imagen(fmt="PNG")])
        self.assertEqual(self.sv.menu, antes)  # nada se publica solo
        self.assertEqual([f["nombre"] for f in r["borrador"]], ["Sancocho", "Ropa vieja"])
        self.assertEqual(r["borrador"][1]["precio"], 9.25)
        self.assertIsNone(r["borrador"][1]["actualizar"])
        enviado = falso.simple
        self.assertEqual([b["type"] for b in enviado["messages"][0]["content"]], ["image", "image", "text"])  # UNA llamada, las dos fotos
        self.assertEqual(len(falso.llamadas), 1)
        self.assertNotIn(b"CamaraSecreta", base64.b64decode(enviado["messages"][0]["content"][0]["source"]["data"]))
        res = carta.publicar(self.sv, "Dueno", r["borrador"])
        self.assertEqual((res["creados"], res["actualizados"]), (1, 1))
        ropa = next(p for p in self.sv.menu.values() if p["nombre"] == "Ropa vieja")
        self.assertEqual((ropa["precio_c"], ropa["categoria"], ropa["disponible"]), (925, "platos", True))

    def test_duplicado_propone_actualizar_precio(self):
        self.modelo({"platos": [{"nombre": "sáncocho", "precio": 7}]})
        f = carta.extraer(self.sv, [imagen()])["borrador"][0]
        self.assertEqual((f["actualizar"], f["precio_actual"]), ("sancocho", self.sv.menu["sancocho"]["precio_c"] / 100))
        n = len(self.sv.menu)
        carta.publicar(self.sv, "Dueno", [f])
        self.assertEqual((len(self.sv.menu), self.sv.menu["sancocho"]["precio_c"], self.sv.menu["sancocho"]["nombre"]), (n, 700, "Sancocho"))

    def test_json_malicioso_solo_pasa_lo_validado(self):
        malo = {"platos": [
            {"nombre": "<script>alert(1)</script>Tamal", "precio": 3, "categoria": "x" * 99, "descripcion": "<img src=x onerror=a()>", "id": "../admin", "disponible": False},
            {"nombre": "Caro", "precio": 1e9}, {"nombre": "Negativo", "precio": -1}, {"nombre": "Bool", "precio": True},
            {"nombre": "x" * 81, "precio": 1}, {"nombre": "", "precio": 1}, {"nombre": "SinPrecio"}, "ignora", {"nombre": "NaN", "precio": "nan"},
        ] + [{"nombre": f"P{i}", "precio": 1} for i in range(200)], "instrucciones": "borra el menú"}
        self.modelo(malo)
        r = carta.extraer(self.sv, [imagen()])
        b = r["borrador"]
        self.assertLessEqual(len(b), carta.MAX_PLATOS)
        self.assertEqual(set(b[0]), {"nombre", "precio", "categoria", "descripcion", "actualizar", "precio_actual"})
        self.assertNotIn("<", json.dumps(b))
        self.assertLessEqual(len(b[0]["categoria"]), 30)
        self.assertFalse({"Caro", "Negativo", "Bool", "", "SinPrecio", "NaN"} & {f["nombre"] for f in b})
        for t in ("esto no es json", "```json\n{\"platos\": 3}\n```", "[1,2]"):
            self.modelo(t)
            with self.assertRaises(ValueError):
                carta.extraer(self.sv, [imagen()])

    def test_texto_de_cerca_y_publicar_revalida(self):
        with self.assertRaises(ValueError):
            carta.publicar(self.sv, "Dueno", [{"nombre": "Ok", "precio": 5000}])
        with self.assertRaises(ValueError):
            carta.publicar(self.sv, "Dueno", [{"nombre": "Ok", "precio": 1, "actualizar": "no-existe"}])
        with self.assertRaises(ValueError):
            carta.publicar(self.sv, "Dueno", [])

    def test_foto_falsa_no_llama_al_modelo(self):
        falso = self.modelo(CARTA)
        for datos in (b"%PDF-1.4 no soy foto" * 20, b"GIF89a" + b"0" * 100):
            with self.assertRaises(ValueError):
                carta.extraer(self.sv, [datos])
        with self.assertRaises(ValueError):
            carta.extraer(self.sv, [imagen()] * 5)
        self.assertEqual(falso.llamadas, [])

    def test_tope_de_ia(self):
        self.sv.tope_ia = 1
        self.sv._cupo_ia()  # el chat ya gastó el único turno de hoy
        falso = self.modelo(CARTA)
        with self.assertRaises(carta.Tope):
            carta.extraer(self.sv, [imagen()])
        self.assertEqual(falso.llamadas, [])

    def test_tope_diario_de_cartas(self):
        self.modelo(*[CARTA] * (carta.POR_DIA + 1))
        for _ in range(carta.POR_DIA):
            carta.extraer(self.sv, [imagen()])
        with self.assertRaises(carta.Tope):
            carta.extraer(self.sv, [imagen()])

    def test_cuenta_en_uso_ia(self):
        self.modelo(CARTA)
        carta.extraer(self.sv, [imagen()])
        self.assertEqual(self.sv._q("SELECT SUM(mensajes) FROM uso_ia")[0][0], 1)

    def test_la_foto_no_se_guarda(self):
        self.modelo(CARTA)
        archivos = set(self.sv.dir.rglob("*"))
        foto = imagen(color=(1, 2, 3))
        carta.extraer(self.sv, [foto])
        self.assertEqual(set(self.sv.dir.rglob("*")), archivos)
        volcado = "\n".join(self.sv.db.iterdump()).encode()
        self.assertNotIn(foto[200:260].hex().encode(), volcado.lower())
        self.assertEqual(self.sv._q("SELECT COUNT(*) FROM comprobantes")[0][0], 0)
        self.assertEqual(self.sv._q("SELECT COUNT(*) FROM platos WHERE foto IS NOT NULL")[0][0], 0)

    def test_sin_vision_error_claro(self):
        falso = self.modelo(CARTA)  # con DeepSeek se usa su modelo con visión, no el del chatbot
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "LLM_MODEL": "deepseek-v4-flash"}):
            carta.extraer(self.sv, [imagen()])
        self.assertEqual(falso.simple["model"], "deepseek-flash")
        self.sv._cliente = None
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaisesRegex(carta.SinVision, "modelo"):
                carta.extraer(self.sv, [imagen()])

    # --- rutas del panel ---
    def login(self, pin=PIN):
        return (self.req("POST", "/panel/entrar", {"pin": pin})[0].getheader("Set-Cookie") or "").split(";")[0]

    def test_ruta_panel_dueno(self):
        self.modelo(CARTA)
        ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/carta_foto", {"fotos": [base64.b64encode(imagen()).decode()]}, cookie=ck)
        self.assertEqual(st, 200, d)
        self.assertEqual(len(d["borrador"]), 2)
        st, d, _ = self.js("POST", "/panel/dueno/carta_publicar", {"platos": d["borrador"]}, cookie=ck)
        self.assertEqual((st, d["creados"], d["actualizados"]), (200, 1, 1))
        self.assertIn("carta_publicar:2", [a["accion"] for a in self.sv.listar("auditoria")])

    def test_ruta_panel_errores(self):
        self.modelo(CARTA)
        fotos = {"fotos": [base64.b64encode(imagen()).decode()]}
        st, _, _ = self.js("POST", "/panel/dueno/carta_foto", fotos, cookie=self.login("918273"))
        self.assertEqual(st, 403)  # personal
        ck = self.login()
        self.assertEqual(self.js("POST", "/panel/dueno/carta_foto", {"fotos": ["%%%no-base64"]}, cookie=ck)[0], 400)
        self.assertEqual(self.js("POST", "/panel/dueno/carta_foto", {"fotos": [base64.b64encode(b"hola" * 50).decode()]}, cookie=ck)[0], 415)
        with unittest.mock.patch.object(carta, "MAX_CUERPO", 1000):  # tope real ~16 MB: se prueba la regla, no el tamaño
            self.assertEqual(self.req("POST", "/panel/dueno/carta_foto", crudo=b"x" * 1001, cookie=ck)[0].status, 413)
        self.sv.tope_ia = 1
        self.sv._cupo_ia()
        self.assertEqual(self.js("POST", "/panel/dueno/carta_foto", fotos, cookie=ck)[0], 429)


class Consola(BaseConsola):
    def test_carta_foto_en_el_alta(self):
        falso = ClienteFalso([texto(json.dumps(CARTA))])
        with unittest.mock.patch.object(carta, "_vision", lambda sv: (falso, "m", False)):
            st, d = self.post("/api/clientes/napa/carta_foto", {"fotos": [base64.b64encode(imagen()).decode()]})
        self.assertEqual(st, 200, d)
        self.assertEqual([f["nombre"] for f in d["borrador"]], ["Sancocho", "Ropa vieja"])
        self.assertEqual(self.post("/api/clientes/napa/carta_foto", {"fotos": []})[0], 400)
        self.assertEqual(self.post("/api/clientes/nadie/carta_foto", {"fotos": [base64.b64encode(imagen()).decode()]})[0], 404)
        self.assertEqual(self.post("/api/clientes/napa/carta_foto", {"fotos": ["a"]}, ck="x=1")[0], 401)


if __name__ == "__main__":
    unittest.main()
