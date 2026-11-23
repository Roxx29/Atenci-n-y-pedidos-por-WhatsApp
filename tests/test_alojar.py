"""Varios clientes en un proceso (`python -m motor alojar`): se elige por Host y nada se cruza. Ejecutar: python -m unittest tests.test_alojar"""
import contextlib
import http.client
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
from pathlib import Path

from motor import __main__ as cli
from motor import clientes, nucleo, panel_web, web
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso

PIN_A, PIN_B, PIN_GLOBAL = "482913", "739155", "555666"
ENV = {"WEB_SECRET": "maestro", "PANEL_PIN": PIN_GLOBAL, "PANEL_PIN_A": PIN_A, "PANEL_PIN_B": PIN_B,
       "REPARTIDORES_A": "Juan:271828", "REPARTIDORES_B": "Juan:314159",
       "RESTAURANTE_LAT": "1.0", "RESTAURANTE_LON": "1.0", "RESTAURANTE_LAT_A": "7.96", "RESTAURANTE_LON_A": "-80.43"}
FUERA = {"X-Forwarded-For": "8.8.8.8"}  # lo que Caddy añade a lo que reenvía de internet


class Base(unittest.TestCase):
    env = ENV

    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, self.env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        for slug in "abc":
            (self.d / slug).mkdir()
            (self.d / slug / "servicio.yaml").write_text(SERVICIO_PEDIDOS + f'marca: Marca-{slug}\nweb: {{aviso_version: "v1", aviso: "aviso.md"}}\n', encoding="utf-8")
            (self.d / slug / "menu.yaml").write_text(MENU, encoding="utf-8")
            (self.d / slug / "aviso.md").write_text("Aviso", encoding="utf-8")
        (self.d / "clientes.yaml").write_text("clientes:\n  a: {carpeta: a, dominio: a.com}\n  b: {carpeta: b, dominio: b.com}\n"
                                              "  c: {carpeta: c, dominio: c.com, activo: false}\n", encoding="utf-8")
        self.arrancar()

    def arrancar(self):
        self.srv = srv = web.crear_alojamiento(clientes.cargar(self.d / "clientes.yaml"), 0, ruta=self.d / "clientes.yaml")
        self.puerto = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [sv.cerrar() for sv in srv.servicios.values()])  # corre después de cerrar el servidor; incluye los cargados en caliente
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))

    def req(self, host, metodo, ruta, cuerpo=None, cookie=None, extra=None, con=None):
        c = con or http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        h = {"Content-Type": "application/json", **(extra or {})}
        if host:
            h["Host"] = host
        if cookie:
            h["Cookie"] = cookie
        c.putrequest(metodo, ruta, skip_host=True)
        data = json.dumps(cuerpo).encode() if cuerpo is not None else b""
        for k, v in {**h, "Content-Length": str(len(data))}.items():
            c.putheader(k, v)
        c.endheaders(data)
        r = c.getresponse()
        raw = r.read()
        if not con:
            c.close()
        return r.status, raw, (r.getheader("Set-Cookie") or "").split(";")[0]

    def entrar(self, host, tel="50760001111"):
        return self.req(host, "POST", "/entrar", {"nombre": "Ana", "telefono": tel, "consentimiento": True, "version_aviso": "v1"})

    def marca(self, host, **k):
        st, raw, _ = self.req(host, "GET", "/config", **k)
        return json.loads(raw).get("marca") if st == 200 else st


class TestHost(Base):
    def test_cada_dominio_sirve_a_su_cliente(self):
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.assertEqual(self.marca("b.com"), "Marca-b")

    def test_puerto_mayusculas_y_slug_localhost(self):
        for h in ("a.com:8080", "A.COM", "a.localhost", f"a.localhost:{self.puerto}", "a.com."):
            self.assertEqual(self.marca(h), "Marca-a", h)

    def test_host_desconocido_e_inactivo_dan_el_mismo_404(self):
        vistos = {self.req(h, m, r, c)[:2] for h in ("otro.com", "c.com", "c.localhost", None, "a.com.evil.com", "127.0.0.1", "xa.com")
                  for m, r, c in (("GET", "/", None), ("GET", "/config", None), ("POST", "/entrar", {"x": 1}), ("GET", "/panel", None))}
        self.assertEqual(len(vistos), 1, vistos)
        st, raw = vistos.pop()
        self.assertEqual(st, 404)
        self.assertNotIn(b"a.com", raw)

    def test_una_conexion_reutilizada_despacha_cada_peticion(self):
        """Caddy reutiliza conexiones entre dominios: el cliente lo decide cada petición, no la conexión."""
        con = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        self.addCleanup(con.close)
        self.assertEqual([self.marca(h, con=con) for h in ("a.com", "b.com", "a.com")], ["Marca-a", "Marca-b", "Marca-a"])

    def test_salud_comprueba_todas_las_bd(self):
        self.assertEqual(self.req("lo-que-sea", "GET", "/salud")[0], 200)
        self.srv.servicios["b"].cerrar()
        self.assertEqual(self.req("lo-que-sea", "GET", "/salud")[0], 503)

    def test_salud_desde_fuera_es_barato_y_no_toca_las_bd(self):
        """Lo que llega por Caddy (X-Forwarded-For) no puede forzar un SELECT por cliente ni saber si una BD está caída."""
        self.srv.servicios["b"].cerrar()
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.req("a.com", "GET", "/salud", extra=FUERA)[:2], (200, b'{"ok": true}'))
            self.assertEqual(self.req("a.com", "GET", "/salud")[0], 503)

    def test_proxy_delante_sin_PROXY_1_es_error_de_configuracion(self):
        """Caddy local sin PROXY=1: todos tendrían la IP 127.0.0.1 (panel abierto, bloqueo y límites compartidos). Mejor no servir."""
        for ruta in ("/config", "/panel", "/salud"):
            st, raw, _ = self.req("a.com", "GET", ruta, extra=FUERA)
            self.assertEqual(st, 500, ruta)
            self.assertIn("PROXY=1", json.loads(raw)["error"])
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.marca("a.com", extra=FUERA), "Marca-a")
        with unittest.mock.patch.object(web, "LOCALES", ()):  # no viene de un proxy de esta máquina: la cabecera la escribe cualquiera
            self.assertEqual(self.marca("a.com", extra=FUERA), "Marca-a")

    def test_escucha_solo_en_esta_maquina_por_defecto(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")


class TestConProxy(Base):
    env = {**ENV, "PROXY": "1", "PANEL_PUBLICO": "1", "PANEL_PUBLICO_B": "0", "ALOJAR_HOST": "0.0.0.0"}

    def test_slug_localhost_no_se_registra_tras_un_proxy(self):
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.assertEqual(self.marca("a.localhost"), 404)

    def test_panel_publico_por_cliente_con_la_global_por_defecto(self):
        self.assertEqual(self.req("a.com", "GET", "/panel", extra=FUERA)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/panel", extra=FUERA)[0], 404)

    def test_alojar_host(self):
        self.assertEqual(self.srv.server_address[0], "0.0.0.0")


class TestRecarga(Base):
    def escribir(self, texto):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text(texto, encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))  # que el mtime cambie aunque el reloj del disco sea grueso

    def clientes(self, a="true", c="false"):
        self.escribir(f"clientes:\n  a: {{carpeta: a, dominio: a.com, activo: {a}}}\n  b: {{carpeta: b, dominio: b.com}}\n"
                      f"  c: {{carpeta: c, dominio: c.com, activo: {c}}}\n")

    def ask(self, dominio):
        return self.req("127.0.0.1", "GET", f"/_ask?domain={dominio}")[0]

    def test_pausar_y_reactivar_sin_reiniciar(self):
        sv = self.srv.servicios["a"]
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.clientes(a="false")
        self.assertEqual([self.marca("a.com"), self.marca("a.localhost"), self.ask("a.com")], [404, 404, 404])
        self.assertEqual(self.marca("b.com"), "Marca-b")
        self.assertEqual(self.req("x", "GET", "/salud")[0], 200)
        self.clientes()
        self.assertEqual([self.marca("a.com"), self.ask("a.com")], ["Marca-a", 200])
        self.assertIs(self.srv.servicios["a"], sv)  # misma instancia: no se reabre la BD ni se pierden sesiones

    def test_activar_un_cliente_en_caliente(self):
        self.clientes(c="true")
        self.assertEqual([self.marca("c.com"), self.ask("c.com")], ["Marca-c", 200])

    def test_clientes_yaml_roto_en_caliente_conserva_la_lista(self):
        for roto in ("clientes: [", "clientes:\n  a: {carpeta: a, dominio: b.com}\n  b: {carpeta: b, dominio: b.com}\n", None):
            if roto is None:
                (self.d / "clientes.yaml").unlink()
            else:
                self.escribir(roto)
            self.assertEqual([self.marca("a.com"), self.marca("b.com"), self.marca("c.com")], ["Marca-a", "Marca-b", 404], roto)


class TestModulos(Base):
    """Módulo efectivo = lo que diga el operador en clientes.yaml; si no dice nada, el servicio.yaml del cliente."""

    def arrancar(self):
        with (self.d / "a" / "servicio.yaml").open("a", encoding="utf-8") as f:
            f.write("modulos: [estadisticas]\n")  # a lo contrató; b no
        super().arrancar()

    def escribir(self, a="", b=""):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text(f"clientes:\n  a: {{carpeta: a, dominio: a.com{a}}}\n  b: {{carpeta: b, dominio: b.com{b}}}\n", encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))

    def ver(self, host, ck):
        """(estado de la ruta de estadísticas, módulos que ve el panel) con la sesión ya abierta."""
        st, raw, _ = self.req(host, "GET", "/panel/estado", cookie=ck)
        self.assertEqual(st, 200)  # la sesión sigue viva tras cada recarga
        return self.req(host, "POST", "/panel/dueno/estadisticas", {}, cookie=ck)[0], json.loads(raw)["modulos"]

    def test_override_del_operador_en_caliente_sin_cerrar_sesiones(self):
        cka, ckb = (self.req(h, "POST", "/panel/entrar", {"pin": p})[2] for h, p in (("a.com", PIN_A), ("b.com", PIN_B)))
        con, sin = (200, ["estadisticas"]), (404, [])
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])  # sin override: servicio.yaml
        self.escribir(a=", modulos: []", b=", modulos: [estadisticas]")
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [sin, con])  # el operador manda
        self.escribir()
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])  # quitado: vuelve el servicio.yaml
        self.escribir(b=", modulos: [inventado]")  # inválido: sigue la lista anterior
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])

    def test_el_dueno_no_puede_activarlo_desde_el_panel(self):
        ck = self.req("b.com", "POST", "/panel/entrar", {"pin": PIN_B})[2]
        self.req("b.com", "POST", "/panel/dueno/ajustes", {"modulos": ["estadisticas"], "modulos_operador": ["estadisticas"], "marca": "B"}, cookie=ck)
        self.assertEqual(self.ver("b.com", ck), (404, []))
        self.srv.servicios["b"].poner_ajuste("modulos", ["estadisticas"])  # ni aunque llegara a la tabla ajustes (la que escribe el panel)
        self.assertEqual(self.ver("b.com", ck), (404, []))
        self.escribir(b=", modulos: []")
        self.assertEqual(self.ver("b.com", ck), (404, []))


class TestBloqueo(unittest.TestCase):
    def test_ipv6_cuenta_por_64(self):
        """Un atacante con un /64 (lo que da cualquier proveedor) tiene 2^64 direcciones: cuentan como una."""
        b = panel_web.Bloqueo()
        for i in range(5):
            b.fallo(f"2001:db8:1:2::{i + 1}")
        self.assertTrue(b.bloqueada("2001:db8:1:2:ffff::9"))
        self.assertFalse(b.bloqueada("2001:db8:1:3::1"))
        b.acierto("2001:db8:1:3::1")

    def test_muchos_fallos_repartidos_solo_retrasan(self):
        b = panel_web.Bloqueo()
        for i in range(panel_web.Bloqueo.TOPE - 1):
            b.fallo(f"8.8.{i}.1")
        self.assertEqual(b.retraso(), 0)
        b.fallo("9.9.9.9")
        self.assertGreater(b.retraso(), 0)
        self.assertFalse(b.bloqueada("7.7.7.7"))  # el dueño, desde otra IP, entra (más despacio)
        with unittest.mock.patch.object(panel_web.time, "time", return_value=panel_web.time.time() + panel_web.Bloqueo.VENTANA_S + 1):
            self.assertEqual(b.retraso(), 0)


class TestBloqueoGlobal(unittest.TestCase):
    def test_tope_por_hora_entre_todas_las_ip_y_un_aviso(self):
        b, t0 = panel_web.Bloqueo(), panel_web.time.time()
        avisos = [b.fallo(f"10.0.{i}.1") for i in range(panel_web.Bloqueo.TOPE_HORA + 3)]
        self.assertEqual(avisos.count(True), 1)  # un solo aviso por episodio
        self.assertTrue(b.bloqueada("7.7.7.7"))  # nadie entra hasta que bajen
        with unittest.mock.patch.object(panel_web.time, "time", return_value=t0 + panel_web.Bloqueo.HORA_S + 1):
            self.assertFalse(b.bloqueada("7.7.7.7"))
            self.assertEqual([b.fallo(f"10.1.{i}.1") for i in range(panel_web.Bloqueo.TOPE_HORA)].count(True), 1)  # otro episodio, otro aviso

    def test_los_diccionarios_no_crecen_sin_fin(self):
        b = panel_web.Bloqueo()
        with unittest.mock.patch.object(panel_web.Bloqueo, "TOPE_HORA", 10**9):
            for i in range(3000):
                b.fallo(f"10.{i // 250}.{i % 250}.1")
        self.assertLessEqual(len(b.fallos) + len(b.hasta), panel_web.Bloqueo.MAX_CLAVES + 1)


class TestAislamiento(Base):
    def test_cookie_de_cliente_de_a_no_vale_en_b(self):
        st, _, ck = self.entrar("a.com")
        self.assertEqual(st, 200)
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)
        self.assertEqual(self.req("b.localhost", "GET", "/pedidos", cookie=ck)[0], 401)

    def test_los_datos_van_a_la_bd_de_cada_carpeta(self):
        self.entrar("a.com")
        a, b = self.srv.servicios["a"], self.srv.servicios["b"]
        self.assertEqual(len(a.listar("consentimiento")), 1)
        self.assertEqual(b.listar("consentimiento"), [])
        self.assertTrue((self.d / "a" / "datos.sqlite").is_file() and (self.d / "b" / "datos.sqlite").is_file())
        self.assertNotIn("c", self.srv.servicios)  # el inactivo ni se abre
        self.assertFalse((self.d / "c" / "datos.sqlite").exists())

    def login(self, host, pin, ruta="/panel"):
        st, _, ck = self.req(host, "POST", ruta + "/entrar", {"pin": pin})
        return st, ck

    def test_pin_de_a_no_abre_b_ni_el_global_abre_ninguno(self):
        self.assertEqual(self.login("a.com", PIN_A)[0], 200)
        self.assertEqual(self.login("b.com", PIN_A)[0], 401)
        self.assertEqual(self.login("b.com", PIN_B)[0], 200)
        for h in ("a.com", "b.com"):
            self.assertEqual(self.login(h, PIN_GLOBAL)[0], 401)

    def test_cookie_del_panel_de_a_no_vale_en_b(self):
        _, ck = self.login("a.com", PIN_A)
        self.assertEqual(self.req("a.com", "GET", "/panel/estado", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/panel/estado", cookie=ck)[0], 401)

    def test_repartidor_del_mismo_nombre_no_cruza(self):
        st, ck = self.login("a.com", "271828", "/repartidor")
        self.assertEqual(st, 200)
        self.assertEqual(self.login("b.com", "271828", "/repartidor")[0], 401)
        self.assertEqual(self.req("a.com", "GET", "/repartidor/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/repartidor/pedidos", cookie=ck)[0], 401)

    def test_limites_por_cliente(self):
        for _ in range(10):
            self.req("a.com", "POST", "/entrar", {})
        self.assertEqual(self.req("a.com", "POST", "/entrar", {})[0], 429)
        self.assertEqual(self.req("b.com", "POST", "/entrar", {})[0], 400)

    def test_bloqueo_de_pin_por_cliente(self):
        for _ in range(5):
            self.login("a.com", "000111")
        self.assertEqual(self.login("a.com", PIN_A)[0], 429)
        self.assertEqual(self.login("b.com", PIN_B)[0], 200)

    def test_tope_global_pausa_el_login_y_avisa_al_dueno_una_vez(self):
        with unittest.mock.patch.object(panel_web.Bloqueo, "TOPE_HORA", 3):
            for _ in range(3):
                self.assertEqual(self.login("a.com", "000111")[0], 401)
            self.assertEqual(self.login("a.com", PIN_A)[0], 429)  # ni con el PIN bueno, hasta que pase la hora
            self.assertEqual(self.login("a.com", PIN_A)[0], 429)
            self.assertEqual(self.login("b.com", PIN_B)[0], 200)  # otro cliente no se entera
        avisos = [x for x in self.srv.servicios["a"].listar("aviso") if "PIN" in x["texto"]]
        self.assertEqual(len(avisos), 1)

    def test_el_retraso_se_aplica_antes_de_comprobar_el_pin(self):
        with unittest.mock.patch.object(panel_web.Bloqueo, "retraso", return_value=0.3), unittest.mock.patch.object(web.time, "sleep") as dormir:
            self.assertEqual(self.login("a.com", PIN_A)[0], 200)
            self.assertEqual(self.login("a.com", "271828", "/repartidor")[0], 200)
        self.assertEqual([c.args for c in dormir.call_args_list], [(0.3,), (0.3,)])

    def test_ubicacion_del_restaurante_por_cliente(self):
        for host, pin, esperado in (("a.com", PIN_A, {"lat": 7.96, "lon": -80.43}), ("b.com", PIN_B, None)):
            _, ck = self.login(host, pin)
            rest = json.loads(self.req(host, "GET", "/panel/repartidores_mapa", cookie=ck)[1])["restaurante"]
            self.assertEqual(rest and {k: rest[k] for k in ("lat", "lon")}, esperado)


class TestSecreto(Base):
    env = {}  # sin WEB_SECRET: manda el .web_secret de cada carpeta

    def test_web_secret_copiado_con_la_carpeta_no_cruza_cookies(self):
        """«Nuevo cliente» copia una carpeta: aunque arrastre el .web_secret, el secreto de cada cliente sale del slug."""
        (self.d / "b" / ".web_secret").write_text((self.d / "a" / ".web_secret").read_text())
        self.srv.shutdown(), self.srv.server_close()
        for sv in self.srv.servicios.values():
            sv.cerrar()
        self.arrancar()
        _, _, ck = self.entrar("a.com")
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)

    def test_sin_pin_propio_no_hay_panel(self):
        self.assertEqual(self.req("a.com", "GET", "/panel")[0], 404)


class TestAsk(Base):
    def ask(self, dominio, **k):
        return self.req("127.0.0.1", "GET", f"/_ask?domain={dominio}", **k)[0]

    def test_solo_dominios_de_clientes_activos(self):
        self.assertEqual([self.ask(d) for d in ("a.com", "B.com", "c.com", "otro.com", "a.localhost", "")], [200, 200, 404, 404, 404, 404])

    def test_no_responde_a_peticiones_que_llegan_por_el_proxy(self):
        """Tras Caddy todo llega desde 127.0.0.1: lo de fuera se distingue por X-Forwarded-For."""
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.ask("a.com", extra=FUERA), 404)
            self.assertEqual(self.req("a.com", "GET", "/_ask?domain=a.com", extra=FUERA)[0], 404)

    def test_no_responde_a_otra_maquina(self):
        with unittest.mock.patch.object(web, "LOCALES", ()):
            self.assertEqual(self.ask("a.com"), 404)

    def test_con_proxy_1_responde_al_proxy_de_la_red_privada(self):
        """Caddy en otro contenedor no llega desde 127.0.0.1 sino desde la red interna (docker-compose pone PROXY=1)."""
        with unittest.mock.patch.object(web, "LOCALES", ()), unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.ask("a.com"), 200)
            self.assertEqual(self.ask("a.com", extra={"X-Forwarded-For": "8.8.8.8"}), 404)


class TestTurso(unittest.TestCase):
    def test_un_cliente_sin_credenciales_propias_no_usa_las_del_proceso(self):
        """Con las TURSO_* globales, los 20 clientes escribirían en la misma BD."""
        with unittest.mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://global", "TURSO_AUTH_TOKEN": "t", "TURSO_SOLO_LOCAL": "1"}):
            con, nube = nucleo.conectar(":memory:", nube=True, env={})
        con.close()
        self.assertFalse(nube)

    def test_alojar_abre_cada_servicio_con_su_entorno(self):
        visto = {}

        def falso(carpeta, env=None, **k):
            visto[Path(carpeta).name] = env
            return Servicio(carpeta, db=":memory:", cliente=ClienteFalso(()))

        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(web, "Servicio", falso), \
                unittest.mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://global", "TURSO_DATABASE_URL_A": "libsql://a"}, clear=True):
            (Path(tmp) / "a").mkdir()
            (Path(tmp) / "a" / "servicio.yaml").write_text(SERVICIO_PEDIDOS.replace("menu: menu.yaml\n", ""), encoding="utf-8")
            srv = web.crear_alojamiento([clientes.Cliente("a", Path(tmp) / "a", "a.com", True)], 0)
            srv.server_close()
            srv.servicios["a"].cerrar()
        self.assertEqual(visto, {"a": {"TURSO_DATABASE_URL": "libsql://a"}})


class TestComandos(unittest.TestCase):
    def setUp(self):
        p = unittest.mock.patch.object(cli, "cargar_env")  # el .env real no debe colarse en os.environ de las demás pruebas
        p.start()
        self.addCleanup(p.stop)

    def test_alojar_sin_clientes_yaml_da_error_claro(self):
        with self.assertRaises(SystemExit) as e:
            cli.main(["alojar", "no-existe-clientes.yaml"])
        self.assertIn("no-existe-clientes.yaml", str(e.exception))

    def test_alojar_pasa_archivo_y_puerto(self):
        with unittest.mock.patch.object(web, "alojar") as alojar:
            cli.main(["alojar"])
            cli.main(["alojar", "9001"])
            cli.main(["alojar", "otros.yaml", "9002"])
        self.assertEqual([c.args for c in alojar.call_args_list], [("clientes.yaml", 8080), ("clientes.yaml", 9001), ("otros.yaml", 9002)])

    def test_operador_sin_modulo_da_error_claro(self):
        with unittest.mock.patch.dict(sys.modules, {"motor.operador": None}), self.assertRaises(SystemExit) as e:
            cli.main(["operador"])
        self.assertIn("motor/operador.py", str(e.exception))

    def test_operador_llama_a_servir(self):
        llamadas = []
        falso = types.ModuleType("motor.operador")
        falso.servir = lambda ruta, puerto: llamadas.append((ruta, puerto))
        with unittest.mock.patch.dict(sys.modules, {"motor.operador": falso}):
            cli.main(["operador", "otros.yaml", "9100"])
        self.assertEqual(llamadas, [("otros.yaml", 9100)])


if __name__ == "__main__":
    unittest.main()


class Rota:
    """Conexión que falla como una BD reemplazada por debajo (lo que se veía en el VPS)."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def close(self): pass
    def execute(self, *a): raise sqlite3.DatabaseError("file is not a database")


class TestBdCambiada(Base):
    """Bug de producción: `alojar` seguía con la conexión al archivo viejo de un cliente → `file is not a database` en cada petición → 502."""
    escribir, clientes = TestRecarga.escribir, TestRecarga.clientes

    def bd(self, marca):
        f = self.d / f"nueva-{marca}.sqlite"
        sv = Servicio(self.d / "a", db=str(f))
        sv.poner_ajuste("marca", marca)
        sv.cerrar()
        return f

    def liberar(self, slug):
        if os.name == "nt":  # Windows no deja reemplazar ni mover un archivo abierto (en Linux sí: ahí aparece el bug); se cierra a mano
            sv = self.srv.servicios[slug]
            with sv._db_lock:
                sv.db.close()

    def romper(self, sv):
        with sv._db_lock:  # cerrar con otra consulta en curso tumba el proceso
            sv.db.close()
            sv.db = Rota()

    def test_bd_reemplazada_se_reabre_antes_de_atender(self):
        viejo, b = self.srv.servicios["a"], self.srv.servicios["b"]
        self.assertEqual(self.marca("a.com"), "Marca-a")
        nueva = self.bd("Nueva")
        self.liberar("a")
        os.replace(nueva, self.d / "a" / "datos.sqlite")
        with unittest.mock.patch.object(web, "bd_rota", wraps=web.bd_rota) as rota:
            self.assertEqual(self.marca("a.com"), "Nueva")
            self.assertEqual(self.marca("b.com"), "Marca-b")
        rota.assert_not_called()  # se vio por el archivo, antes de atender: sin fallo ni reintento
        self.assertIsNot(self.srv.servicios["a"], viejo)
        self.assertIs(self.srv.servicios["b"], b)  # el otro cliente ni se entera

    def test_carpeta_recreada(self):
        self.srv.servicios["a"]._q("INSERT INTO ajustes VALUES ('marca', '\"Vieja\"')")
        self.assertEqual(self.marca("a.com"), "Vieja")
        self.liberar("a")
        shutil.rmtree(self.d / "a")
        shutil.copytree(self.d / "c", self.d / "a", ignore=shutil.ignore_patterns("datos.sqlite*"))
        self.assertEqual(self.marca("a.com"), "Marca-c")

    def test_archivar_suelta_la_bd_y_restaurar_la_reabre(self):
        viejo = self.srv.servicios["a"]
        self.escribir("clientes:\n  a: {carpeta: a, dominio: a.com, activo: false, archivado: 2026-11-17}\n  b: {carpeta: b, dominio: b.com}\n")
        self.assertEqual([self.marca("a.com"), self.marca("b.com")], [404, "Marca-b"])
        self.assertNotIn("a", self.srv.servicios)
        with self.assertRaises(sqlite3.ProgrammingError):  # conexión cerrada: sus archivos quedan libres
            viejo._q("SELECT 1")
        os.rename(self.d / "a", self.d / "a2")  # en Windows falla si sigue abierta
        os.rename(self.d / "a2", self.d / "a")
        self.clientes()
        self.assertEqual(self.marca("a.com"), "Marca-a")

    def test_eliminar_y_recrear_el_mismo_slug(self):
        self.srv.servicios["a"]._q("INSERT INTO ajustes VALUES ('marca', '\"Vieja\"')")
        self.escribir("clientes:\n  b: {carpeta: b, dominio: b.com}\n")
        self.assertEqual(self.marca("b.com"), "Marca-b")
        shutil.rmtree(self.d / "a")  # en Windows falla si alojar no la soltó
        shutil.copytree(self.d / "c", self.d / "a", ignore=shutil.ignore_patterns("datos.sqlite*"))
        self.clientes()
        self.assertEqual(self.marca("a.com"), "Marca-c")

    def test_reintenta_una_vez_reabriendo_solo_ese_cliente(self):
        viejo, b = self.srv.servicios["a"], self.srv.servicios["b"]
        self.romper(viejo)
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.assertIsNot(self.srv.servicios["a"], viejo)
        self.assertIs(self.srv.servicios["b"], b)

    def test_post_se_reintenta_con_su_cuerpo(self):
        self.romper(self.srv.servicios["a"])
        self.assertEqual(self.entrar("a.com")[0], 200)

    def test_503_si_sigue_rota_y_se_recupera_al_arreglarla(self):
        sv = self.srv.servicios["a"]
        self.romper(sv)
        f = self.d / "a" / "datos.sqlite"
        f.write_bytes(b"esto no es una base de datos" * 200)
        for x in ("-wal", "-shm"):
            Path(str(f) + x).unlink(missing_ok=True)
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            st, raw, _ = self.req("a.com", "GET", "/config")
        self.assertEqual(st, 503)
        self.assertIn("error", json.loads(raw))
        self.assertIn("[alojar] BD de a no disponible: DatabaseError", salida.getvalue())
        self.assertEqual(self.marca("b.com"), "Marca-b")
        f.unlink()
        self.assertEqual(self.marca("a.com"), "Marca-a")  # se reabre sola al volver a pedir

    def test_concurrencia_mientras_se_reemplaza(self):
        estados, fallos = [], []

        def pedir():
            try:
                for _ in range(12):
                    estados.append(self.req("a.com", "GET", "/config")[0])
            except Exception as e:
                fallos.append(e)

        hilos = [threading.Thread(target=pedir) for _ in range(4)]
        for h in hilos:
            h.start()
        for marca in ("X", "Y", "Z"):
            nueva, viejo = self.bd(marca), self.srv.servicios["a"]
            if os.name == "nt":
                self.romper(viejo)  # Windows: no se puede reemplazar abierto; se rompe la conexión en vivo
            else:
                os.replace(nueva, self.d / "a" / "datos.sqlite")
            for _ in range(200):
                if self.srv.servicios.get("a") not in (viejo, None):
                    break
                time.sleep(0.01)
            time.sleep(0.05)
        for h in hilos:
            h.join(30)
        self.assertFalse(any(h.is_alive() for h in hilos), "bloqueo")
        self.assertEqual(fallos, [])
        self.assertEqual(set(estados), {200})


class TestError500(Base):
    def test_excepcion_no_controlada_da_500_json(self):
        for ruta, que in (("/config", "config_publica"), ("/", "armar_pagina")):
            with unittest.mock.patch.object(web, que, side_effect=RuntimeError("x")), contextlib.redirect_stderr(io.StringIO()):
                st, raw, _ = self.req("a.com", "GET", ruta)
            self.assertEqual((st, json.loads(raw)["error"]), (500, "Error interno"), ruta)

    def test_motor_web_tambien(self):
        srv = web.crear_servidor(self.srv.servicios["b"], 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        with unittest.mock.patch.object(web, "config_publica", side_effect=RuntimeError("x")), contextlib.redirect_stderr(io.StringIO()):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", "/config")
            self.assertEqual(c.getresponse().status, 500)
            c.close()
