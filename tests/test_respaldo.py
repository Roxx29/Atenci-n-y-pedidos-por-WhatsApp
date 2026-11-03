import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.entorno import cargar_env
from motor.nucleo import Servicio
from tests.test_motor import DEMO, Bloque, ClienteFalso, NS, texto

ENV = {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "LLM_MODEL": "deepseek-v4-flash",
       "LLM_RESPALDO_BASE_URL": "http://127.0.0.1:11435", "LLM_RESPALDO_MODEL": "qwen3:8b"}


class Error(Exception):
    def __init__(self, code):
        super().__init__("x")
        self.status_code = code


class APIConnectionError(Exception):
    pass


class Roto(ClienteFalso):
    """Principal que siempre lanza `exc`."""

    def __init__(self, exc):
        super().__init__([])
        self.exc, self.n = exc, 0
        self.beta = NS(messages=NS(create=self._falla))
        self.messages = NS(create=self._falla)

    def _falla(self, **kw):
        self.n += 1
        raise self.exc


class Respaldo(unittest.TestCase):
    def sv(self, exc, resp=(texto("hola"), texto("otra")), env=ENV):
        self.p = Roto(exc)
        self.r = ClienteFalso(resp)
        sv = Servicio(DEMO, db=":memory:", cliente=self.p, cliente_respaldo=self.r)
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        return sv

    def responder(self, sv, t="hi"):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            r = sv.responder("A", t)
        return r, o.getvalue()

    def test_errores_de_proveedor_activan_respaldo(self):
        for exc in (Error(402), Error(401), Error(429), Error(500), Error(503), ConnectionError(), TimeoutError(), APIConnectionError()):
            with self.subTest(exc=repr(exc)):
                sv = self.sv(exc)
                r, salida = self.responder(sv)
                self.assertEqual(r, "hola")
                self.assertEqual(self.r.simple["model"], "qwen3:8b")
                self.assertEqual(self.r.simple["thinking"], {"type": "disabled"})
                self.assertEqual(sorted(self.r.simple), ["max_tokens", "messages", "model", "system", "thinking", "tools"])
                self.assertNotIn("sk-", salida)

    def test_un_aviso_y_principal_en_pausa_300s(self):
        sv = self.sv(Error(402))
        t = [1000.0]
        sv._reloj = lambda: t[0]
        self.responder(sv)
        self.responder(sv, "otra vez")
        self.assertEqual(self.p.n, 1)
        self.assertEqual(sv.listar("aviso"), [{"id": 1, "texto": "IA principal sin saldo o caída: usando respaldo local", "telefono": None, "pedido_id": None}])
        t[0] += 301
        self.r.respuestas.append(texto("x"))
        self.responder(sv)
        self.assertEqual(self.p.n, 2)  # reintentó el principal
        self.assertEqual(len(sv.listar("aviso")), 1)  # sin aviso repetido mientras siga caído

    def test_error_nuestro_no_activa_respaldo(self):
        for c in (400, 404, 422):
            sv = self.sv(Error(c))
            with self.assertRaises(Error):
                sv.responder("A", "hi")
            self.assertEqual(self.r.llamadas, [])

    def test_sin_respaldo_se_propaga(self):
        sv = self.sv(Error(402), env={"LLM_RESPALDO_BASE_URL": "", "LLM_RESPALDO_MODEL": ""})
        with self.assertRaises(Error) as c:
            sv.responder("A", "hi")
        self.assertEqual(c.exception.status_code, 402)

    def test_thinking_del_respaldo_no_entra_al_historial(self):
        sv = self.sv(Error(402), resp=[NS(stop_reason="tool_use", content=[Bloque(type="thinking", thinking="m", signature="s"), Bloque(type="tool_use", id="t1", name="buscar", input={"tipo": "pedido"})]), texto("ok")],
                     env={**ENV, "ANTHROPIC_BASE_URL": "https://api.anthropic.com"})
        self.assertEqual(self.responder(sv)[0], "ok")
        self.assertEqual([b["type"] for b in self.r.llamadas[1][1]["content"]], ["tool_use"])

    def test_respaldo_tambien_falla(self):
        sv = self.sv(Error(402))
        self.r.messages = NS(create=mock.Mock(side_effect=Error(500)))
        with self.assertRaises(Error) as c, contextlib.redirect_stdout(io.StringIO()):
            sv.responder("A", "hi")
        self.assertEqual(c.exception.status_code, 500)


class CargarEnv(unittest.TestCase):
    def cargar(self, contenido, previo=None):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, previo or {}):
            for k in ("T_A", "T_B", "T_C", "T_D", "T_E"):
                os.environ.pop(k, None) if k not in (previo or {}) else None
            ruta = Path(d) / ".env"
            ruta.write_text(contenido, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as o:
                cargar_env(ruta)
            return {k: os.environ.get(k) for k in ("T_A", "T_B", "T_C", "T_D", "T_E")}, o.getvalue()

    def test_formatos(self):
        v, salida = self.cargar("# c\n\nT_A='uno'\nT_B = \"dos\" \nexport T_C=tres\nmala linea\n=x\nT_D=a=b\n", {"T_E": "propio"})
        self.assertEqual(v, {"T_A": "uno", "T_B": "dos", "T_C": "tres", "T_D": "a=b", "T_E": "propio"})
        self.assertEqual(salida, "")

    def test_no_pisa(self):
        v, _ = self.cargar("T_A=nuevo\n", {"T_A": "viejo"})
        self.assertEqual(v["T_A"], "viejo")

    def test_inexistente(self):
        cargar_env(Path(tempfile.gettempdir()) / "no-existe" / ".env")


if __name__ == "__main__":
    unittest.main()
