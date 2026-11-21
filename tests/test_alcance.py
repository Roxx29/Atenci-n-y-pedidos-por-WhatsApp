"""Alcance del bot (fuera de tema) y teléfono del restaurante. Ejecutar: python -m unittest tests.test_alcance"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from motor import nucleo
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso, herramienta, texto

FUERA = herramienta("fuera_de_tema", {"motivo": "receta"})


class TestAlcance(unittest.TestCase):
    def sv(self, respuestas, extra=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS + extra, encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        sv = Servicio(d, db=":memory:", cliente=ClienteFalso(respuestas))
        self.addCleanup(sv.cerrar)
        self.hora = dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        sv.ahora = lambda: self.hora
        return sv

    def strike(self, sv, cid="A"):
        sv._cliente.respuestas += [FUERA, texto("Solo ayudo con pedidos")]
        return sv.responder(cid, "dame una receta")

    def test_registra_strike_sin_editar_el_yaml_y_el_prompt_tiene_la_regla(self):
        sv = self.sv([])
        self.assertIn("fuera_de_tema", [t["name"] for t in sv.tools])
        self.assertEqual(self.strike(sv), "Solo ayudo con pedidos")
        self.assertEqual([(r["telefono"], r["motivo"]) for r in sv.listar("fuera_tema")], [("A", "receta")])
        self.assertIn("Alcance:", nucleo.PROMPT)
        self.assertNotIn("WhatsApp", nucleo.PROMPT)

    def test_tercer_strike_bloquea_sin_modelo_y_se_libera_a_la_hora(self):
        sv = self.sv([], "telefono: 994-1234\n")
        for _ in range(3):
            self.strike(sv)
        n = len(sv._cliente.llamadas)
        self.assertEqual(sv.responder("A", "otra receta"), "Solo puedo ayudarle con pedidos y dudas de Fonda. Para otra cosa llame al 994-1234.")
        self.assertEqual(len(sv._cliente.llamadas), n)
        self.hora += dt.timedelta(hours=1, minutes=1)
        sv._cliente.respuestas = [texto("hola")]
        self.assertEqual(sv.responder("A", "hola"), "hola")

    def test_otro_cliente_no_se_afecta_y_sin_telefono_no_hay_frase(self):
        sv = self.sv([])
        for _ in range(3):
            self.strike(sv)
        self.assertEqual(sv.responder("A", "x"), "Solo puedo ayudarle con pedidos y dudas de Fonda.")
        sv._cliente.respuestas = [texto("hola")]
        self.assertEqual(sv.responder("B", "hola"), "hola")

    def test_fuera_tema_max_configurable_y_nota_de_ultimo_aviso(self):
        sv = self.sv([], "fuera_tema_max: 1\n")
        self.strike(sv)
        self.assertIn("Solo puedo", sv.responder("A", "x"))
        nota = herramientas_nota(sv)
        self.assertIn("Último aviso", nota)


def herramientas_nota(sv):
    from motor import herramientas
    return herramientas.ejecutar(sv, "fuera_de_tema", {}, "B")["nota"]


class TestTelefono(unittest.TestCase):
    def prompt(self, extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS + extra, encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        sv = Servicio(d, db=":memory:", cliente=ClienteFalso([]))
        self.addCleanup(sv.cerrar)
        return sv._prompt

    def test_la_regla_del_telefono_solo_si_hay_telefono(self):
        self.assertNotIn("Teléfono del restaurante", self.prompt(""))
        p = self.prompt("telefono: 994-1234\n")
        self.assertIn("Teléfono del restaurante: 994-1234", p)
        self.assertIn("queja", p)


if __name__ == "__main__":
    unittest.main()
