"""Páginas propias por cliente (servicios/<slug>/web/), su subida desde la consola y el kit de diseno/editable.
Ejecutar: python -m unittest tests.test_paginas"""
import base64
import datetime as dt
import http.client
import importlib.util
import io
import json
import os
import re
import tempfile
import threading
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from motor import paginas, web
from motor.nucleo import Servicio
from tests import test_alojar
from tests.test_consola import Base as BaseConsola
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso, herramienta, texto

RAIZ = Path(__file__).resolve().parent.parent
KIT = RAIZ / "diseno" / "editable"
PROPIA = b"<!doctype html><title>DeliFood</title><!--TEMA--><h1>CARTA-PROPIA DeliFood</h1><script src=\"/web/js/carta.js\"></script>"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


def b64(b):
    return base64.b64encode(b if isinstance(b, bytes) else b.encode()).decode()


def symlink(destino, enlace, carpeta=False):
    """Enlace simbólico; en Windows sin permiso, una carpeta se enlaza con una junction (mklink /J, no pide permisos)."""
    try:
        os.symlink(destino, enlace, target_is_directory=carpeta)
    except (OSError, NotImplementedError):
        if not (carpeta and os.name == "nt"):
            raise unittest.SkipTest("este sistema no deja crear enlaces simbólicos")
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(destino)], check=True, capture_output=True)


class Motor(test_alojar.Base):
    """Dos clientes en `alojar`: solo `a` tiene página propia."""

    def setUp(self):
        super().setUp()
        self.web = self.d / "a" / "web"
        (self.web / "js").mkdir(parents=True)
        (self.web / "index.html").write_bytes(PROPIA)
        (self.web / "js" / "carta.js").write_text("console.log(1)", encoding="utf-8")
        (self.web / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (self.web / "f.woff2").write_bytes(b"wOF2" + b"\0" * 10)

    def get(self, host, ruta):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.putrequest("GET", ruta, skip_host=True)
        c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        cuerpo = r.read()
        c.close()
        return r.status, cuerpo, r

    def test_la_propia_solo_para_ese_cliente(self):
        st, cuerpo, _ = self.get("a.com", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"CARTA-PROPIA Marca-a", cuerpo)  # marca sustituida
        self.assertNotIn(b"DeliFood", cuerpo)
        self.assertIn(b"<style>", cuerpo)  # <!--TEMA--> -> tema del cliente
        self.assertNotIn(b"<!--TEMA-->", cuerpo)
        st, cuerpo, _ = self.get("b.com", "/")
        self.assertEqual(st, 200)
        self.assertNotIn(b"CARTA-PROPIA", cuerpo)
        self.assertIn(b"Marca-b", cuerpo)

    def test_panel_y_repartidor_propios(self):
        (self.web / "panel.html").write_bytes(b"<p>PANEL-PROPIO</p>")
        (self.web / "repartidor.html").write_bytes(b"<p>REP-PROPIO</p>")
        self.assertIn(b"PANEL-PROPIO", self.get("a.com", "/panel")[1])
        self.assertIn(b"REP-PROPIO", self.get("a.com", "/repartidor/")[1])
        self.assertNotIn(b"PANEL-PROPIO", self.get("b.com", "/panel")[1])
        self.assertNotIn(b"REP-PROPIO", self.get("a.com", "/")[1])  # cada página con su archivo

    def test_personalizado_css_tambien_en_la_propia(self):
        (self.d / "a" / "personalizado.css").write_text(".x{color:red}", encoding="utf-8")
        self.assertIn(b".x{color:red}", self.get("a.com", "/")[1])

    def test_cae_a_la_estandar(self):
        estandar = self.get("b.com", "/")[1].replace(b"Marca-b", b"Marca-a")
        (self.web / "index.html").write_bytes(b"x" * (paginas.MAX_HTML + 1))  # demasiado grande
        self.assertEqual(self.get("a.com", "/")[1], estandar)
        (self.web / "index.html").write_bytes(b"")  # vacía
        self.assertEqual(self.get("a.com", "/")[1], estandar)
        (self.web / "index.html").unlink()  # no existe
        self.assertEqual(self.get("a.com", "/")[1], estandar)
        self.assertEqual(self.get("a.com", "/")[2].getheader("Content-Security-Policy"), web.CSP)

    def test_html_enlazado_fuera_no_se_sirve(self):
        fuera = self.d / "secreto.html"
        fuera.write_bytes(b"SECRETO")
        (self.web / "index.html").unlink()
        symlink(fuera, self.web / "index.html")
        self.assertNotIn(b"SECRETO", self.get("a.com", "/")[1])

    def test_archivos_propios(self):
        st, cuerpo, r = self.get("a.com", "/web/js/carta.js")
        self.assertEqual((st, cuerpo), (200, b"console.log(1)"))
        self.assertEqual(r.getheader("Content-Type").split(";")[0], "text/javascript")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("max-age=", r.getheader("Cache-Control"))
        self.assertEqual(r.getheader("Cross-Origin-Resource-Policy"), "same-origin")
        st, _, r = self.get("a.com", "/web/logo.svg")
        self.assertEqual((st, r.getheader("Content-Type").split(";")[0]), (200, "image/svg+xml"))
        self.assertIn("sandbox", r.getheader("Content-Security-Policy"))  # abierto a mano, un SVG no ejecuta nada
        self.assertEqual(self.get("a.com", "/web/f.woff2")[2].getheader("Content-Type").split(";")[0], "font/woff2")
        self.assertEqual(self.get("b.com", "/web/js/carta.js")[0], 404)  # b no ve lo de a

    def test_rutas_hostiles_dan_404(self):
        (self.web / ".oculto.css").write_text("x", encoding="utf-8")
        (self.web / "x.py").write_text("x", encoding="utf-8")
        for ruta in ("/web/../servicio.yaml", "/web/%2e%2e/servicio.yaml", "/web/..%2fservicio.yaml", "/web/js/../../servicio.yaml",
                     "/web/..\\servicio.yaml", "/web/.oculto.css", "/web/x.py", "/web/index.html", "/web/", "/web/js", "/web/js/",
                     "/web//js/carta.js", "/web/C:/Windows/win.ini", "/web/a/b/c/d/e/f.css", "/web/no-existe.css"):
            self.assertEqual(self.get("a.com", ruta)[0], 404, ruta)

    def test_enlace_simbolico_fuera_da_404(self):
        (self.d / "a" / "secreto.css").write_text("SECRETO", encoding="utf-8")
        symlink(self.d / "a" / "secreto.css", self.web / "s.css")
        self.assertEqual(self.get("a.com", "/web/s.css")[0], 404)

    def test_subcarpeta_enlazada_fuera_da_404(self):
        symlink(self.d / "b", self.web / "otra", carpeta=True)
        (self.d / "b" / "x.css").write_text("DE-B", encoding="utf-8")
        self.assertEqual(self.get("a.com", "/web/otra/x.css")[0], 404)

    def test_carpeta_web_enlazada_no_vale(self):
        (self.d / "b" / "web").mkdir()
        (self.d / "b" / "web" / "x.css").write_text("DE-B", encoding="utf-8")
        import shutil
        shutil.rmtree(self.web)
        symlink(self.d / "b" / "web", self.web, carpeta=True)
        self.assertEqual(self.get("a.com", "/web/x.css")[0], 404)

    def test_archivo_demasiado_grande(self):
        (self.web / "g.png").write_bytes(PNG + b"\0" * paginas.MAX_ARCHIVO)
        self.assertEqual(self.get("a.com", "/web/g.png")[0], 404)

    def test_csp_de_la_propia(self):
        csp = self.get("a.com", "/")[2].getheader("Content-Security-Policy")
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertNotIn("b.com", csp)
        script = re.search(r"script-src ([^;]*)", csp)[1]
        self.assertEqual(script, "'self' 'unsafe-inline'")  # nunca scripts de fuera
        self.assertEqual(self.get("b.com", "/")[2].getheader("Content-Security-Policy"), web.CSP)

    def test_permitir_por_cliente_solo_fuentes_e_imagenes(self):
        sv = self.srv.servicios["a"]  # servicio.yaml se lee al abrir el cliente
        sv._cfg = {**sv._cfg, "web": {**sv._cfg["web"], "permitir": ["fonts.gstatic.com", "Fonts.Googleapis.com", "*", "*.x.com", "https://x.com",
                                                                     "evil.com; script-src *", "b.com/x", "localhost", 7]}}
        csp = self.get("a.com", "/")[2].getheader("Content-Security-Policy")
        dir_ = dict(p.strip().split(" ", 1) for p in csp.split(";"))
        fuera = {"https://fonts.gstatic.com", "https://fonts.googleapis.com"}
        self.assertEqual(set(dir_["font-src"].split()), {"'self'", *fuera})
        self.assertTrue(fuera <= set(dir_["img-src"].split()) and fuera <= set(dir_["style-src"].split()))
        self.assertEqual(dir_["script-src"], "'self' 'unsafe-inline'")
        self.assertEqual(dir_["connect-src"], "'self'")
        self.assertNotIn("x.com", csp)
        self.assertNotIn("evil", csp)
        self.assertNotIn("b.com", csp)

    def test_la_cookie_del_cliente_es_de_su_host_y_no_vale_en_otro(self):
        st, _, ck = self.entrar("a.com")
        self.assertEqual(st, 200)
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.request("POST", "/entrar", json.dumps({"nombre": "Ana", "telefono": "50760001111", "consentimiento": True, "version_aviso": "v1"}),
                  {"Host": "a.com", "Content-Type": "application/json"})
        crudo = c.getresponse().getheader("Set-Cookie")
        c.close()
        self.assertIn("SameSite=Strict", crudo)
        self.assertIn("HttpOnly", crudo)
        self.assertNotIn("Domain", crudo)  # solo para a.com: el navegador no la manda a b.com
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)


class Subida(BaseConsola):
    def subir(self, archivos=None, zip_=None, slug="napa", **extra):
        d = {**({"archivos": {k: b64(v) for k, v in archivos.items()}} if archivos is not None else {}), **({"zip": b64(zip_)} if zip_ is not None else {}), **extra}
        return self.post(f"/api/clientes/{slug}/pagina", d)

    def web(self):
        w = self.carpeta / "web"
        return {str(p.relative_to(w)).replace("\\", "/"): p.read_bytes() for p in sorted(w.rglob("*")) if p.is_file()} if w.exists() else None

    def test_sube_y_activa(self):
        st, j = self.subir({"index.html": PROPIA, "css/carta.css": "h1{color:red}", "img/logo.png": PNG,
                            "img/i.svg": '<svg xmlns="http://www.w3.org/2000/svg"><use href="#a"/></svg>'})
        self.assertEqual(st, 200, j)
        self.assertEqual((j["ok"], j["activa"], j["paginas"], j["archivos"]), (True, True, ["index.html"], 4))
        self.assertEqual(self.web()["css/carta.css"], b"h1{color:red}")
        self.assertEqual(self.get("/api/clientes/napa/pagina")[1]["activa"], True)
        sv = Servicio(self.carpeta, env={})
        try:
            self.assertIn(b"CARTA-PROPIA Fonda La", web.armar_pagina(sv, web.PAGINA))
        finally:
            sv.cerrar()
        self.assertEqual(self.registro()[-1], {**self.registro()[-1], "accion": "pagina", "slug": "napa", "resultado": "ok"})
        self.assertNotIn("carta", (self.raiz / "registro.jsonl").read_text(encoding="utf-8"))  # sin nombres ni contenido

    def test_zip_y_carpeta_comun(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mi-pagina/", "")
            z.writestr("mi-pagina/index.html", PROPIA)
            z.writestr("mi-pagina/panel.html", b"<p>panel</p>")
            z.writestr("mi-pagina/js/carta.js", "1")
        st, j = self.subir(zip_=buf.getvalue())
        self.assertEqual(st, 200, j)
        self.assertEqual(sorted(self.web()), ["index.html", "js/carta.js", "panel.html"])
        self.assertEqual(j["paginas"], ["index.html", "panel.html"])

    def test_reemplaza_entera(self):
        self.subir({"index.html": PROPIA, "viejo.css": "a{}"})
        self.subir({"index.html": b"<p>nueva</p>"})
        self.assertEqual(self.web(), {"index.html": b"<p>nueva</p>"})

    def test_quitar_vuelve_a_la_estandar(self):
        for quitar in (lambda: self.post("/api/clientes/napa/pagina", {"quitar": True}),
                       lambda: test_alojar_pedir(self.srv.server_port, "DELETE", "/api/clientes/napa/pagina", self.ck)):
            self.subir({"index.html": PROPIA})
            st, j = quitar()
            self.assertEqual((st, j["ok"], j["activa"]), (200, True, False))
            self.assertIsNone(self.web())
            self.assertFalse(any(p.name.startswith(".web") for p in self.carpeta.iterdir()))
        self.assertEqual(test_alojar_pedir(self.srv.server_port, "DELETE", "/api/clientes/napa/pagina", "")[0], 401)

    def test_subida_hostil_no_cambia_nada(self):
        self.subir({"index.html": PROPIA})
        antes = self.web()
        svg = lambda s: {"index.html": "x", "i.svg": s}
        malos = [{"../x.css": "a"}, {"/x.css": "a"}, {"a\\b.css": "a"}, {"C:x.css": "a"}, {".htaccess": "a"}, {"x.php": "a"},
                 {"otra.html": "a", "index.html": "a"}, {"sub/index.html": "a"}, {"a/b/c/d/e.css": "a", "index.html": "x"},
                 {"x.css": "a"},  # sin ninguna de las tres páginas
                 {"index.html": "x", "X.CSS": "a", "x.css": "b"},  # iguales en Windows
                 {"index.html": "x", "logo.png": b"no-es-png"}, {"index.html": b"\xff\xfe no utf-8"},
                 svg("<svg><script>alert(1)</script></svg>"), svg('<svg onload="alert(1)"/>'), svg('<svg><a href="javascript:alert(1)"/></svg>'),
                 svg("<svg><foreignObject/></svg>"), svg('<!DOCTYPE svg [<!ENTITY x "y">]><svg/>'), svg('<svg><image href="https://malo.example/x.png"/></svg>'),
                 {"index.html": "x" * (paginas.MAX_HTML + 1)}, {"index.html": "x", "g.png": PNG + b"\0" * paginas.MAX_ARCHIVO},
                 {"index.html": "x", **{f"f{i}.css": "a" for i in range(paginas.MAX_ARCHIVOS)}}]
        for m in malos:
            st, j = self.subir(m)
            self.assertEqual(st, 400, (m.keys() if len(str(m)) > 300 else m, j))
            self.assertTrue(j["error"])
        for d in ({"archivos": {"index.html": "no es base64!"}}, {"archivos": []}, {"zip": "AAAA"}, {}, {"archivos": {"index.html": b64("x")}, "zip": b64(b"x")}):
            self.assertEqual(self.post("/api/clientes/napa/pagina", d)[0], 400, d)
        self.assertEqual(self.web(), antes)
        self.assertFalse(any(p.name.startswith(".web") for p in self.carpeta.iterdir()))

    def test_zip_hostil(self):
        def z(*entradas):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as f:
                for info, datos in entradas:
                    f.writestr(info, datos)
            return buf.getvalue()
        enlace = zipfile.ZipInfo("s.css")
        enlace.external_attr = 0o120777 << 16
        bomba = z(("index.html", "x"), ("b.css", b"a" * (paginas.MAX_ARCHIVO * 3)))
        for malo in (z(("index.html", "x"), ("../x.css", "a")), z(("index.html", "x"), (enlace, "/etc/passwd")), bomba,
                     z(*[(f"f{i}.css", "a") for i in range(paginas.MAX_ARCHIVOS + 1)], ("index.html", "x"))):
            st, j = self.subir(zip_=malo)
            self.assertEqual(st, 400, j)
        self.assertIsNone(self.web())

    def test_cuerpo_demasiado_grande_413(self):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.putrequest("POST", "/api/clientes/napa/pagina")
        for k, v in {"Content-Type": "application/json", "Cookie": self.ck, "Content-Length": str(paginas.MAX_CUERPO + 1)}.items():
            c.putheader(k, v)
        c.endheaders()
        st = c.getresponse().status
        c.close()
        self.assertEqual(st, 413)

    def test_cliente_desconocido_o_sin_sesion(self):
        self.assertEqual(self.subir({"index.html": "x"}, slug="nadie")[0], 404)
        self.assertEqual(self.subir({"index.html": "x"}, slug="../x")[0], 404)
        self.assertEqual(self.post("/api/clientes/napa/pagina", {"archivos": {"index.html": b64("x")}}, ck="operador=1.x")[0], 401)

    def test_fallo_a_mitad_deja_la_anterior(self):
        self.subir({"index.html": PROPIA})
        antes = self.web()
        real = os.rename

        def falla(a, b):
            if Path(a).name.startswith(".web-nueva"):  # justo al poner la nueva, con la vieja ya retirada
                raise OSError("disco")
            return real(a, b)
        with unittest.mock.patch.object(paginas.os, "rename", falla):
            self.assertEqual(self.subir({"index.html": b"nueva"})[0], 500)
        self.assertEqual(self.web(), antes)
        self.assertFalse(any(p.name.startswith(".web") for p in self.carpeta.iterdir()))

    def test_web_enlazada_no_se_toca(self):
        """Ni subir ni quitar atraviesan un enlace: podría apuntar a la carpeta de otro cliente."""
        otra = self.raiz / "otra-web"
        otra.mkdir()
        (otra / "index.html").write_bytes(b"DE-OTRO")
        symlink(otra, self.carpeta / "web", carpeta=True)
        for st in (self.subir({"index.html": b"x"})[0], self.post("/api/clientes/napa/pagina", {"quitar": True})[0], self.get("/api/clientes/napa/pagina")[0]):
            self.assertEqual(st, 400)  # 1.ª barrera: clientes.cargar ya no admite un cliente con algo enlazado fuera de su carpeta
        for hacer in (lambda: paginas.instalar(self.carpeta, {"archivos": {"index.html": b64("x")}}), lambda: paginas.quitar(self.carpeta)):
            with self.assertRaises(ValueError):  # 2.ª barrera: paginas.py tampoco escribe ni borra a través del enlace
                hacer()
        self.assertEqual((otra / "index.html").read_bytes(), b"DE-OTRO")
        self.assertEqual(paginas.estado(self.carpeta)["activa"], False)

    def test_estado_sin_pagina(self):
        self.assertEqual(self.get("/api/clientes/napa/pagina")[1], {"ok": True, "activa": False, "paginas": [], "archivos": 0, "bytes": 0})


def test_alojar_pedir(puerto, metodo, ruta, cookie):
    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=10)
    c.request(metodo, ruta, None, {"Cookie": cookie})
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, json.loads(raw)


# ---------------- kit ----------------
def cargar_vista():
    spec = importlib.util.spec_from_file_location("vista", KIT / "vista.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Http:
    def __init__(self, puerto):
        self.puerto, self.ck = puerto, {}

    def __call__(self, metodo, ruta, cuerpo=None, quien="c"):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        h = {"Content-Type": "application/json", **({"Cookie": self.ck[quien]} if quien in self.ck else {})}
        c.request(metodo, ruta, json.dumps(cuerpo) if cuerpo is not None else None, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        if r.getheader("Set-Cookie"):
            self.ck[quien] = r.getheader("Set-Cookie").split(";")[0]
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw


def recorrido(pedir):
    """Lo que hacen las tres páginas, en orden. -> {nombre: respuesta}. Mismo guion para el motor real y para la vista previa."""
    o = {}
    o["config"] = pedir("GET", "/config")[1]
    o["menu"] = pedir("GET", "/menu")[1]
    o["aviso"] = pedir("GET", "/aviso")[1]
    o["entrar"] = pedir("POST", "/entrar", {"nombre": "Ana", "telefono": "60001111", "acepta_privacidad": True, "version_aviso": o["aviso"]["version"]})[1]
    o["config_sesion"] = pedir("GET", "/config")[1]
    o["chat"] = pedir("POST", "/chat", {"texto": "quiero 2 sancochos"})[1]
    pid = o["chat"]["pedido"]["id"]
    o["confirmar"] = pedir("POST", "/chat", {"texto": "sí, confirmo"})[1]
    o["ubicacion"] = pedir("POST", "/ubicacion", {"lat": 7.961, "lon": -80.431, "precision": 15})[1]
    o["efectivo"] = pedir("POST", "/efectivo", {"pedido": pid})[1]
    o["pedidos"] = pedir("GET", "/pedidos")[1]
    o["avisos"] = pedir("GET", "/avisos")[1]
    o["panel_entrar"] = pedir("POST", "/panel/entrar", {"pin": "482913"}, "p")[1]
    o["estado"] = pedir("GET", "/panel/estado?desde=0", quien="p")[1]
    o["detalle"] = pedir("GET", f"/panel/pedido/{pid}", quien="p")[1]
    o["panel_menu"] = pedir("GET", "/panel/menu", quien="p")[1]
    o["asignar"] = pedir("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Juan"}, "p")[1]
    o["marcar"] = pedir("POST", "/panel/marcar", {"pedido_id": pid, "estado": "en_preparacion"}, "p")[1]
    pedir("POST", "/panel/marcar", {"pedido_id": pid, "estado": "listo"}, "p")
    o["agotado"] = pedir("POST", "/panel/agotado", {"plato": "chicha", "disponible": False}, "p")[1]
    o["tienda"] = pedir("POST", "/panel/tienda", {"cerrado": False}, "p")[1]
    o["ajustes"] = pedir("GET", "/panel/dueno/ajustes", quien="p")[1]
    o["rep_entrar"] = pedir("POST", "/repartidor/entrar", {"pin": "271828"}, "r")[1]
    o["rep_aviso"] = pedir("POST", "/repartidor/aviso_ubicacion", {"version": o["rep_entrar"]["aviso_ubicacion"]["version"]}, "r")[1]
    o["rep_marcar"] = pedir("POST", "/repartidor/marcar", {"pedido_id": pid, "estado": "en_camino"}, "r")[1]
    o["rep_posicion"] = pedir("POST", "/repartidor/posicion", {"lat": 7.962, "lon": -80.432, "precision": 10}, "r")[1]
    o["rep_pedidos"] = pedir("GET", "/repartidor/pedidos", quien="r")[1]
    o["pedidos_camino"] = pedir("GET", "/pedidos")[1]
    o["mapa"] = pedir("GET", "/panel/repartidores_mapa", quien="p")[1]
    o["sin_sesion"] = pedir("POST", "/chat", {"texto": "x"}, "nadie")
    return o


def forma(x):
    """Estructura sin valores: claves de los dicts y forma del primer elemento de las listas."""
    if isinstance(x, dict):
        return {k: forma(v) for k, v in x.items()}
    if isinstance(x, list):
        return [forma(x[0])] if x and not isinstance(x[0], (str, int, float)) else "lista"
    return type(x).__name__ if x is not None else "null"


DE_DATOS = {"envio", "pago"}  # claves que son datos: las zonas; yappy y/o transferencia solo si el restaurante las tiene


def mezclar(a, b):
    """Dos formas son compatibles si tienen las mismas claves; un null en un lado acepta cualquier tipo del otro."""
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(k in DE_DATOS or mezclar(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return not a or not b or mezclar(a[0], b[0])
    return a == b or "null" in (a, b) or {a, b} <= {"int", "float"} or "lista" in (a, b)


def documentada(ruta, api):
    """La ruta sale en API.md como código: `GET /x`, `POST /x …` o `/x…`."""
    return re.search(r"`(?:[A-Z]+ )?" + re.escape(ruta) + r"(?![a-z_])", api) is not None


class Kit(unittest.TestCase):
    def setUp(self):
        self.vista = cargar_vista()

    def servir(self, carpeta):
        srv = self.vista.crear(carpeta, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return Http(srv.server_address[1])

    def motor_real(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS.replace("envio: {costo: 2.50}", "envio: {zonas: {Centro: 2.50}}")
                                         + 'web: {aviso_version: "v1", aviso: "aviso.md"}\n', encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        (d / "aviso.md").write_text("Aviso", encoding="utf-8")
        ped = {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "entrega", "nombre": "Ana", "direccion": "Calle 5", "zona": "Centro"}
        sv = Servicio(d, db=":memory:", cliente=ClienteFalso([herramienta("crear_pedido", ped), texto("¿Confirma?"),
                                                              herramienta("confirmar_pedido", {"pedido_id": 2}), texto("Confirmado")]))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        with unittest.mock.patch.dict(os.environ, {"PANEL_PIN": "482913", "REPARTIDORES": "Juan:271828", "RESTAURANTE_LAT": "7.96", "RESTAURANTE_LON": "-80.43"}):
            srv = web.crear_servidor(sv, 0, "s")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(sv.cerrar)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return Http(srv.server_address[1])

    def test_la_vista_previa_imita_la_api_real(self):
        """Mismo guion contra el motor y contra la vista previa: mismas claves en cada respuesta (API.md se apoya en esto)."""
        real, falsa = recorrido(self.motor_real()), recorrido(self.servir(KIT / "base"))
        for k in real:
            self.assertTrue(mezclar(forma(real[k]), forma(falsa[k])), (k, forma(real[k]), forma(falsa[k])))
        self.assertEqual(falsa["sin_sesion"][0], 401)

    def test_el_comando_arranca_y_responde(self):
        import subprocess
        import sys
        pr = subprocess.Popen([sys.executable, "-u", str(KIT / "vista.py"), str(KIT / "ejemplo-carta"), "0"], stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", cwd=tempfile.gettempdir(),
                              env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})  # desde cualquier carpeta
        self.addCleanup(pr.wait)
        self.addCleanup(pr.kill)
        puerto = int(re.search(r"localhost:(\d+)/", pr.stdout.readline() + pr.stdout.readline())[1])
        self.assertEqual(Http(puerto)("GET", "/")[0], 200)
        self.assertIsInstance(Http(puerto)("GET", "/menu")[1], list)

    def test_sirve_la_carpeta_con_tema_y_archivos(self):
        h = self.servir(KIT / "ejemplo-carta")
        st, html = h("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"<style>", html)
        self.assertNotIn(b"<!--TEMA-->", html)
        for ruta in re.findall(rb'(?:href|src)="(/web/[^"]+)"', html):
            self.assertEqual(h("GET", ruta.decode())[0], 200, ruta)
        self.assertEqual(h("GET", "/web/../vista.py")[0], 404)
        self.assertEqual(h("GET", "/estatico/jakarta-v1.woff2")[0], 200)
        for p in ("/panel", "/repartidor/"):  # el ejemplo no trae esas dos: la vista previa sirve las de base/
            self.assertEqual(h("GET", p)[0], 200)

    def test_la_vista_previa_es_igual_que_el_motor_al_armar(self):
        html = (KIT / "base" / "index.html").read_bytes()
        sv = type("SV", (), {"dir": Path(tempfile.gettempdir()) / "no-existe-xyz", "cfg": {"marca": "Mi Restaurante"}})()
        self.assertEqual(self.vista.armar(html, "Mi Restaurante"), paginas.sustituir(html, (web.ESTATICO / "tema.css").read_bytes(), b"Mi Restaurante"))
        self.assertEqual(paginas.ENLACE_TEMA, web.ENLACE_TEMA)
        self.assertEqual(paginas.MARCA, web.MARCA.encode())
        self.assertTrue(sv)

    def test_el_ejemplo_y_la_base_solo_usan_rutas_de_la_api(self):
        """Toda ruta que piden las páginas del kit existe en el motor (API.md)."""
        api = (KIT / "API.md").read_text(encoding="utf-8")
        for carpeta in ("base", "ejemplo-carta"):
            for f in (KIT / carpeta).rglob("*"):
                if f.suffix in (".html", ".js"):
                    for ruta in set(re.findall(r'''(?:fetch|api)\(\s*(?:"[A-Z]+",\s*)?[`"'](/[a-z_/]+)''', f.read_text(encoding="utf-8"))):
                        self.assertTrue(documentada(ruta.rstrip("/"), api), (f.name, ruta))

    def test_el_ejemplo_funciona_servido_por_el_motor(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS + "marca: La Fonda\n", encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        import shutil
        shutil.copytree(KIT / "ejemplo-carta", d / "web")
        sv = Servicio(d, db=":memory:", cliente=ClienteFalso([]))
        self.addCleanup(sv.cerrar)
        html = web.armar_pagina(sv, web.PAGINA)
        self.assertIn(b"La Fonda", html)
        self.assertNotIn((KIT / "base" / "index.html").read_bytes()[:200], html)
        for ruta in re.findall(rb'(?:href|src)="/web/([^"]+)"', html):
            self.assertIsNotNone(paginas.archivo(d / "web", ruta.decode()), ruta)

    def test_base_trae_las_tres_paginas_con_el_marcador(self):
        for f in ("index.html", "panel.html", "repartidor.html"):
            t = (KIT / "base" / f).read_text(encoding="utf-8")
            self.assertIn("<!--TEMA-->", t, f)
            self.assertGreater(t.count("<!-- ==="), 3, f)  # bloques comentados

    def test_documentos(self):
        for f in ("README.md", "API.md"):
            self.assertTrue((KIT / f).is_file(), f)
        api = (KIT / "API.md").read_text(encoding="utf-8")
        for ruta in ("/config", "/menu", "/entrar", "/chat", "/pedidos", "/avisos", "/ubicacion", "/efectivo", "/comprobante",
                     "/panel/entrar", "/panel/estado", "/panel/marcar", "/panel/asignar", "/repartidor/pedidos", "/repartidor/marcar", "/repartidor/posicion"):
            self.assertTrue(documentada(ruta, api), ruta)


if __name__ == "__main__":
    unittest.main()
