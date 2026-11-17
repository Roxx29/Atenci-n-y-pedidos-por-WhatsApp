"""Temas del catálogo y personalización CSS por cliente. Ejecutar: python -m unittest tests.test_temas"""
import http.client
import json
import re
import threading
import unittest
import unittest.mock

from motor import clientes, operador, panel_web, temas, web
from motor.nucleo import Servicio
from tests.test_consola import Base

HEX = re.compile(r"#[0-9a-f]{6}")
BASE = (web.ESTATICO / "tema.css").read_text(encoding="utf-8")
NO_COLOR = {"--r4", "--r8", "--r12", "--display", "--texto", "--mono"}
SUP = ["--surface", "--sc-lowest", "--sc-low", "--sc", "--sc-high", "--sc-highest"]
# (texto, fondo) tal como los usan las tres páginas; los #hex son colores escritos a mano en su CSS
PARES = [("--on-surface", s) for s in SUP] + [("--on-variant", s) for s in SUP[:4]] + [
    ("--on-primary", "--primary"), ("#ffffff", "--primary-container"), ("--primary", "--surface"), ("--primary", "--sc-lowest"),
    ("--primary", "--primary-fixed"), ("--on-surface", "--primary-fixed"),
    ("--sobre-barra", "--barra"), ("#ffffff", "--barra"), ("--on-inverse", "--inverse"), ("#ffffff", "--inverse"),
    ("--on-secondary-container", "--secondary-container"), ("#17171a", "--secondary-container"), ("--on-surface", "--secondary-fixed"),
    ("--on-tertiary", "--tertiary"), ("--tertiary", "--surface"), ("--tertiary", "--sc-lowest"), ("#ffffff", "--tertiary-container"),
    ("--on-surface", "--tertiary-fixed"), ("--error", "--surface"), ("--error", "--sc-lowest"), ("--on-surface", "--error-container"),
    ("--azul", "--sc-lowest"), ("--violeta", "--sc-lowest"), ("--celeste", "--sc-lowest"),
    ("--on-surface", "--azul-suave"), ("--on-surface", "--violeta-suave"), ("--on-surface", "--celeste-suave")]
MALOS = ["@import 'x.css';", "@IMPORT url(x)", "a{background:url(https://malo.example/x.png)}", "a{background:url(/panel/salir)}",
         "a{background:URL( 'data:text/html;base64,AAAA')}", "a{background:url(data:image/gif;base64,AAAA)}",
         "a{background:image-set('https://malo.example/x.png' 1x)}", "</style><script>alert(1)</script>", "a{}</STYLE >", "a::before{content:'<'}",
         "a{width:expression(alert(1))}", "a{background:javascript:alert(1)}", "a{behavior:url(x.htc)}", "a{BEHAVIOR : x}",
         "a{-moz-binding:url(x)}", "a{color:r\\65 d}", "@\\69mport 'x';", "a{color:red}\x00", "a{}\x1b", "a{color:red}" * 2000,
         "a{background:url(data:image/png;base64," + "A" * 9000 + ")}", "a{background:url(data:image/svg+xml,<svg/>)}"]
BUENOS = ["", "  ", ".logo{border-radius:50%}\n:root{--primary:#123456}", "html{scroll-behavior:smooth}",
          "@media (max-width:600px){.btn{font-size:1rem}}", ".logo{background:url(data:image/png;base64,iVBORw0KGgo=) center/cover}",
          '.logo{background:url( "data:image/svg+xml;base64,PHN2Zy8+" )}', ".a::after{content:'ñ → é'}"]


def tokens_base():
    b = re.search(r":root\{(.*?)\}", BASE, re.S)[1]
    return {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+):([^;]+);", b) if k not in NO_COLOR}


class Catalogo(unittest.TestCase):
    def test_ocho_temas_distintos_y_completos(self):
        cat = temas.catalogo()
        self.assertEqual(len(cat), 8)
        self.assertEqual(len({t["id"] for t in cat}), 8)
        self.assertEqual(len({(t["tokens"]["--primary"], t["tokens"]["--surface"]) for t in cat}), 8)  # distintos entre sí
        self.assertEqual({t["modo"] for t in cat}, {"claro", "oscuro"})
        for t in cat:
            self.assertEqual(set(t), {"id", "nombre", "descripcion", "modo", "muestra", "tokens"}, t["id"])
            self.assertRegex(t["id"], r"^[a-z0-9-]{1,30}$")
            self.assertTrue(t["nombre"] and 0 < len(t["descripcion"]) <= 90, t["id"])
            self.assertEqual(len(t["muestra"]), 3)
            self.assertTrue(all(HEX.fullmatch(c) for c in t["muestra"]), t["id"])
            self.assertEqual(set(t["tokens"]), set(tokens_base()), t["id"])  # todos los tokens de color de tema.css, ni uno más
            for k, v in t["tokens"].items():  # van dentro de <style>: nada que lo cierre ni que abra otra regla
                self.assertRegex(v, r"^[#a-zA-Z0-9(),. %-]+$", (t["id"], k))
            self.assertEqual(temas.contraste(t["tokens"]["--surface"], "#000000") > temas.contraste(t["tokens"]["--surface"], "#ffffff"), t["modo"] == "claro", t["id"])

    def test_el_clasico_es_el_tema_del_motor(self):
        self.assertEqual(temas.catalogo()[0]["id"], "clasico")
        self.assertEqual(temas.catalogo()[0]["tokens"], tokens_base())

    def test_contraste_de_texto_4_5(self):
        self.assertAlmostEqual(temas.contraste("#000000", "#ffffff"), 21, places=1)
        self.assertAlmostEqual(temas.contraste("#777777", "#ffffff"), 4.48, places=2)
        for t in temas.catalogo():
            g = lambda x: x if x.startswith("#") else t["tokens"][x]
            for a, b in PARES:
                self.assertGreaterEqual(temas.contraste(g(a), g(b)), 4.5, f"{t['id']}: {a} sobre {b}")


class Generar(unittest.TestCase):
    def test_base_mas_tokens_mas_color(self):
        self.assertEqual(temas.generar_tema("clasico"), BASE)  # el clásico ES la base (conserva su modo oscuro automático)
        t = next(t for t in temas.catalogo() if t["id"] == "oceano")
        css = temas.generar_tema("oceano")
        self.assertTrue(css.startswith(BASE))
        cola = css[len(BASE):]
        self.assertIn("color-scheme:light", cola)
        for k, v in t["tokens"].items():
            self.assertIn(f"{k}:{v}", cola)
        self.assertIn("color-scheme:dark", temas.generar_tema("parrilla"))
        con = temas.generar_tema("oceano", "#1A73E8")
        self.assertTrue(con.startswith(css))  # el color va después de los tokens: manda
        self.assertIn("--primary:#1a73e8;--primary-container:#1a73e8", con[len(css):])
        self.assertIn("--primary:#1a73e8", temas.generar_tema("clasico", "#1a73e8"))

    def test_el_texto_sobre_el_color_elegido_se_lee(self):
        self.assertIn("--on-primary:#000000", temas.generar_tema("clasico", "#ffe000"))
        self.assertIn("--on-primary:#ffffff", temas.generar_tema("clasico", "#101080"))

    def test_invalidos(self):
        for malo in ("nadie", "", None, 5, "../clasico", ["clasico"]):
            with self.assertRaises(ValueError, msg=malo):
                temas.generar_tema(malo)
        for malo in ("red", "#fff", "#12345g", "#123456;}", 5, ""):
            with self.assertRaises(ValueError, msg=malo):
                temas.generar_tema("clasico", malo)


class Saneo(unittest.TestCase):
    def test_rechaza_lo_peligroso(self):
        for css in MALOS:
            with self.assertRaises(ValueError, msg=css[:60]):
                temas.sanear_css(css)
        for malo in (None, 5, ["a{}"], b"a{}"):
            with self.assertRaises(ValueError):
                temas.sanear_css(malo)

    def test_acepta_css_normal(self):
        for css in BUENOS:
            self.assertEqual(temas.sanear_css(css), css.strip())
        self.assertEqual(len(temas.sanear_css("a{}" + " " * 19_000)), 3)
        temas.sanear_css("/*" + "a" * (temas.MAX_CSS - 4) + "*/")  # justo en el tope


class Consola(Base):
    def setUp(self):
        super().setUp()
        self.op.nuevo({"nombre": "Otra", "slug": "otra", "dominio": "otra.example.com", "pin": "739155"})
        self.otra = self.raiz / "servicios" / "otra"

    def cliente(self, slug="napa"):
        return next(c for c in self.get("/api/clientes")[1]["clientes"] if c["slug"] == slug)

    def paginas(self, carpeta):
        sv = Servicio(carpeta, env={})
        try:
            return [web.armar_pagina(sv, f).decode() for f in (web.PAGINA, web.PAGINA_PANEL, web.PAGINA_REP)]
        finally:
            sv.cerrar()

    def foto(self, carpeta=None):
        c = carpeta or self.carpeta
        return {f: (c / f).read_bytes() if (c / f).exists() else None for f in ("tema.css", "tema.json", "personalizado.css", "servicio.yaml")}

    def test_catalogo_por_http(self):
        st, j = self.get("/api/temas")
        self.assertEqual((st, j), (200, temas.catalogo()))
        self.assertEqual(self.get("/api/temas", ck="x")[0], 401)

    def test_alta_con_tema(self):
        st, j = self.post("/api/clientes", {"nombre": "Mar", "slug": "mar", "dominio": "mar.example.com", "tema": "oceano", "color": "#1A73E8"})
        self.assertEqual(st, 200)
        self.assertEqual((self.raiz / "servicios" / "mar" / "tema.css").read_text(encoding="utf-8"), temas.generar_tema("oceano", "#1a73e8"))
        c = self.cliente("mar")
        self.assertEqual((c["tema"], c["color"], c["personalizado"]), ("oceano", "#1a73e8", False))
        c = self.cliente("napa")  # sin tema ni color: el clásico del motor, sin archivo propio
        self.assertEqual((c["tema"], c["color"], c["personalizado"]), ("clasico", None, False))
        self.assertFalse((self.carpeta / "tema.css").exists())
        for malo in ("nadie", 5, "../x"):
            self.assertEqual(self.post("/api/clientes", {"nombre": "X", "slug": "equis", "dominio": "x.example.com", "tema": malo})[0], 400, malo)
            self.assertFalse((self.raiz / "servicios" / "equis").exists())

    def test_cambiar_el_tema_de_uno_no_toca_al_otro(self):
        self.assertEqual(self.post("/api/clientes/otra/tema", {"tema": "dulce"})[0], 200)
        self.assertEqual(self.post("/api/clientes/otra/personalizado", {"css": ".logo{outline:3px solid lime}"})[0], 200)
        antes, y = self.foto(self.otra), self.ruta.read_bytes()
        st, j = self.post("/api/clientes/napa/tema", {"tema": "elegante", "color": "#C9A227"})
        self.assertEqual((st, j), (200, {"ok": True, "tema": "elegante", "color": "#c9a227"}))
        self.assertEqual(self.post("/api/clientes/napa/personalizado", {"css": ".logo{outline:3px solid aqua}"})[0], 200)
        self.assertEqual((self.carpeta / "tema.css").read_text(encoding="utf-8"), temas.generar_tema("elegante", "#c9a227"))
        self.assertEqual((self.foto(self.otra), self.ruta.read_bytes()), (antes, y))  # ni un byte del otro, ni clientes.yaml
        self.assertEqual((self.cliente("napa")["tema"], self.cliente("otra")["tema"]), ("elegante", "dulce"))
        for h in self.paginas(self.otra):
            self.assertIn(temas.generar_tema("dulce"), h)
            self.assertIn("solid lime", h); self.assertNotIn("solid aqua", h); self.assertNotIn("#c9a227", h)
        st, j = self.post("/api/clientes/napa/tema", {"tema": "fresco"})  # sin color: los del tema
        self.assertEqual((j["color"], self.cliente("napa")["color"]), (None, None))
        self.assertEqual((self.carpeta / "tema.css").read_text(encoding="utf-8"), temas.generar_tema("fresco"))

    def test_tema_invalido_o_fallo_a_mitad_no_cambia_nada(self):
        self.assertEqual(self.post("/api/clientes/napa/tema", {"tema": "oceano"})[0], 200)
        antes = self.foto()
        for d in ({}, {"tema": "nadie"}, {"tema": 5}, {"tema": "dulce", "color": "red"}, {"tema": "dulce", "color": "#fff;}"}, {"tema": "dulce", "otro": 1}):
            self.assertEqual(self.post("/api/clientes/napa/tema", d)[0], 400, d)
            self.assertEqual(self.foto(), antes, d)
        self.assertEqual(self.post("/api/clientes/nadie/tema", {"tema": "dulce"})[0], 404)
        self.assertEqual(self.post("/api/clientes/..%2Fnapa/tema", {"tema": "dulce"})[0], 400)
        self.assertEqual(self.post("/api/clientes/napa/tema", {"tema": "dulce"}, ck="x")[0], 401)
        real = operador.os.replace

        def falla(a, b):
            if str(b).endswith("tema.json"):
                raise OSError("disco lleno")
            return real(a, b)
        with unittest.mock.patch.object(operador.os, "replace", falla):
            st, j = self.post("/api/clientes/napa/tema", {"tema": "dulce"})
        self.assertEqual(st, 500); self.assertNotIn("disco", j["error"])
        self.assertEqual(self.foto(), antes)
        self.assertEqual([f.name for f in self.carpeta.glob("*.tmp")], [])
        self.assertEqual((self.registro()[-1]["accion"], self.registro()[-1]["resultado"]), ("tema", "error 500"))

    def test_ajustes_de_color_conserva_tema_y_personalizado(self):
        self.post("/api/clientes/napa/tema", {"tema": "moderno"})
        self.post("/api/clientes/napa/personalizado", {"css": ".logo{border-radius:50%}"})
        self.assertEqual(self.post("/api/clientes/napa/ajustes", {"color": "#1A73E8", "nombre": "Otro nombre"})[0], 200)
        self.assertEqual((self.carpeta / "tema.css").read_text(encoding="utf-8"), temas.generar_tema("moderno", "#1a73e8"))
        self.assertEqual((self.carpeta / "personalizado.css").read_text(encoding="utf-8"), ".logo{border-radius:50%}")
        c = self.cliente()
        self.assertEqual((c["tema"], c["color"], c["personalizado"]), ("moderno", "#1a73e8", True))
        antes = self.foto()
        with unittest.mock.patch.object(self.op, "_guardar", side_effect=RuntimeError("disco lleno")):
            self.assertEqual(self.post("/api/clientes/napa/ajustes", {"dominio": "n.example.com", "color": "#000000"})[0], 500)
        self.assertEqual(self.foto(), antes)  # tema.json también vuelve

    def test_personalizado_llega_a_las_tres_paginas_solo_de_ese_cliente(self):
        css = ".logo{outline:3px solid lime}\n.barra{background:url(data:image/png;base64,iVBORw0KGgo=)}"
        self.assertEqual(self.get("/api/clientes/napa/personalizado")[1], {"ok": True, "css": ""})
        self.assertEqual(self.post("/api/clientes/napa/personalizado", {"css": f"  {css}\n"}), (200, {"ok": True, "bytes": len(css)}))
        self.assertEqual(self.get("/api/clientes/napa/personalizado")[1], {"ok": True, "css": css})
        for h, limpia in zip(self.paginas(self.carpeta), self.paginas(self.otra)):
            self.assertTrue(h.split("</style>")[0].endswith(css))  # al final del tema: manda sobre él
            self.assertEqual(h.lower().count("</style"), limpia.lower().count("</style"))
        for h in self.paginas(self.otra):
            self.assertNotIn("lime", h)
        self.assertNotIn("lime", (self.raiz / "registro.jsonl").read_text(encoding="utf-8"))  # el registro no guarda el contenido
        self.assertEqual({k: self.registro()[-1][k] for k in ("accion", "slug", "resultado")}, {"accion": "personalizado", "slug": "napa", "resultado": "ok"})
        self.assertEqual(self.post("/api/clientes/napa/tema", {"tema": "minimal"})[0], 200)  # cambiar de tema no lo borra
        self.assertIn("lime", self.paginas(self.carpeta)[1])
        self.assertEqual(self.post("/api/clientes/napa/personalizado", {"css": " "}), (200, {"ok": True, "bytes": 0}))  # vacío = borra
        self.assertFalse((self.carpeta / "personalizado.css").exists())
        self.assertNotIn("lime", self.paginas(self.carpeta)[0])

    def test_personalizado_hostil(self):
        self.post("/api/clientes/napa/personalizado", {"css": ".logo{color:red}"})
        antes = self.foto()
        for css in MALOS:
            st, j = self.post("/api/clientes/napa/personalizado", {"css": css})
            self.assertEqual(st, 400, css[:60]); self.assertEqual(self.foto(), antes)
            self.assertLess(len(j["error"]), 200)  # el error no devuelve el contenido
        for d in ({}, {"css": 5}, {"css": None}, {"css": ["a{}"]}):
            self.assertEqual(self.post("/api/clientes/napa/personalizado", d)[0], 400, d)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.post("/api/clientes/nadie/personalizado", {"css": "a{}"})[0], 404)
        self.assertEqual(self.get("/api/clientes/nadie/personalizado")[0], 404)
        self.assertEqual(self.post("/api/clientes/napa/personalizado", {"css": "a{}"}, ck="x")[0], 401)
        self.assertEqual(self.get("/api/clientes/napa/personalizado", ck="x")[0], 401)
        self.assertEqual(self.post("/api/clientes/napa/personalizado", {"css": "/*" + "a" * (temas.MAX_CSS - 4) + "*/"})[0], 200)  # 20 KB caben en el cuerpo

    def test_un_personalizado_puesto_a_mano_no_cierra_el_style(self):
        (self.carpeta / "personalizado.css").write_text("a{}</style><script>alert(1)</script>", encoding="utf-8")
        for h, limpia in zip(self.paginas(self.carpeta), self.paginas(self.otra)):
            self.assertNotIn("alert(1)", h)
            self.assertEqual(h.lower().count("</style"), limpia.lower().count("</style"))
        (self.carpeta / "personalizado.css").write_bytes(b"\xff\xfe a{}")  # ni UTF-8: se ignora, la página sale
        self.assertIn("--primary:", self.paginas(self.carpeta)[0])

    def test_la_csp_del_panel_admite_el_css_incrustado(self):
        self.post("/api/clientes/napa/tema", {"tema": "parrilla"})
        self.post("/api/clientes/napa/personalizado", {"css": ".logo{background:url(data:image/png;base64,iVBORw0KGgo=)}"})
        srv = web.crear_alojamiento(clientes.cargar(self.ruta), 0, ruta=self.ruta)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [sv.cerrar() for sv in srv.servicios.values()])
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        for ruta in ("/", "/panel"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", ruta, headers={"Host": "napa.example.com"})
            r = c.getresponse(); h = r.read().decode(); c.close()
            self.assertEqual(r.status, 200, ruta)
            self.assertIn("iVBORw0KGgo=", h); self.assertIn("color-scheme:dark", h)
            csp = dict(p.strip().split(" ", 1) for p in r.getheader("Content-Security-Policy").split(";"))
            self.assertIn("'unsafe-inline'", csp["style-src"])  # el tema va en <style>
            self.assertFalse(re.search(r"'(nonce|sha\d+)-", csp["style-src"]))  # un nonce o hash anularía 'unsafe-inline'
            self.assertIn("data:", csp["img-src"].split())  # las imágenes data: del personalizado
            self.assertNotIn("http:", csp["img-src"]); self.assertEqual(csp["default-src"], "'self'")  # y nada externo
        # el panel sigue funcionando con el tema nuevo
        st, _, ck = __import__("tests.test_alta", fromlist=["pedir"]).pedir(srv.server_address[1], "POST", "/panel/entrar", {"pin": "482913"}, host="napa.example.com")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
