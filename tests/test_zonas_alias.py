"""Corregimientos como alias de zona de entrega (sin red)."""
import unittest
from pathlib import Path

import yaml

from motor import herramientas

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "servicios" / "restaurante-pedidos" / "servicio.yaml").read_text(encoding="utf-8"))["pedidos"]


class Alias(unittest.TestCase):
    def test_corregimientos_de_chitre_cuestan_como_chitre(self):
        for z in ("Chitré", "La Arena", "la arena", "MONAGRILLO", "Llano Bonito", "San Juan Bautista", "Chitre cabecera"):
            self.assertEqual(herramientas._envio(CFG, z), 200, z)

    def test_la_villa(self):
        self.assertEqual(herramientas._envio(CFG, "La Villa"), 300)
        self.assertEqual(herramientas._envio(CFG, "La Villa de Los Santos"), 300)

    def test_zona_desconocida_pregunta_en_vez_de_escalar(self):
        with self.assertRaisesRegex(ValueError, "pregunta al cliente a qué zona"):
            herramientas._envio(CFG, "Las Tablas")

    def test_alias_de_zona_inexistente_se_ignora(self):
        cfg = {"envio": {"zonas": {"A": 1.0}, "alias": {"B": ["x"]}}}
        self.assertEqual(herramientas._envio(cfg, "A"), 100)
        with self.assertRaises(ValueError):
            herramientas._envio(cfg, "x")

    def test_instrucciones_no_exigen_apellido_ni_referencia(self):
        ins = yaml.safe_load((Path(__file__).resolve().parent.parent / "servicios" / "restaurante-pedidos" / "servicio.yaml").read_text(encoding="utf-8"))["instrucciones"]
        self.assertIn("basta el de pila", ins)
        self.assertIn("la referencia es opcional", ins)


if __name__ == "__main__":
    unittest.main()
