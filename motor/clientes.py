"""Registro de clientes alojados en un mismo proceso (`python -m motor alojar`). Cada cliente = una carpeta de servicio + un dominio.

clientes.yaml (no se sube al repo; ver clientes.ejemplo.yaml):
  clientes:
    delifood: {carpeta: servicios/restaurante-pedidos, dominio: delifood.com, activo: true}

Las variables del .env de cada cliente llevan su slug como sufijo (PANEL_PIN_DELIFOOD, REPARTIDORES_LAS_TABLAS...): ver `entorno`.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SLUG = re.compile(r"[a-z0-9-]+")
DOMINIO = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
# Lo que en `motor web` es global del proceso y aquí es de cada cliente. Sin sufijo no se lee nunca: un cliente no hereda las del proceso.
ENV_POR_CLIENTE = ("PANEL_PIN", "PANEL_PIN_HASH", "PANEL_PIN_PERSONAL", "PANEL_PIN_PERSONAL_HASH", "REPARTIDORES", "REPARTIDOR_PIN",
                   "RESTAURANTE_LAT", "RESTAURANTE_LON", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
                   # La única con valor por defecto global: PANEL_PUBLICO_<SLUG>=1/0 abre o cierra el panel de ESE cliente fuera de la LAN;
                   # sin ella vale la PANEL_PUBLICO del proceso (ver web.py `_panel`).
                   "PANEL_PUBLICO")
# "WHATSAPP_PHONE_ID" no está: WhatsApp multi-cliente (F3) enrutará por phone_number_id.


@dataclass(frozen=True)
class Cliente:
    slug: str
    carpeta: Path
    dominio: str
    activo: bool


class _SinRepetidos(yaml.SafeLoader):
    """yaml.safe_load se queda con la última clave repetida sin avisar: aquí un slug repetido es un error."""

    def construct_mapping(self, node, deep=False):
        m = super().construct_mapping(node, deep)
        if len(m) != len(node.value):
            raise ValueError("clientes.yaml: hay una clave repetida (¿slug repetido?)")
        return m


def _env(nombre, slug):
    return f"{nombre}_{slug.upper().replace('-', '_')}"


def entorno(slug):
    """Variables de ese cliente, ya sin sufijo: {"PANEL_PIN": os.environ["PANEL_PIN_<SLUG>"], ...}. Solo las suyas."""
    return {n: os.environ[_env(n, slug)] for n in ENV_POR_CLIENTE if os.environ.get(_env(n, slug))}


def _fuera(carpeta, cfg):
    """Primer archivo del servicio que cae fuera de su carpeta (ruta con `..`, absoluta o enlace simbólico), o None."""
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    refs = [str(r) for r in (cfg.get("menu"), cfg.get("conocimiento"), web.get("aviso")) if r]
    for f in [carpeta / r for r in refs] + list(carpeta.iterdir()):
        if not f.resolve().is_relative_to(carpeta):
            return f
    return None


def cargar(ruta="clientes.yaml"):
    """Lee y valida clientes.yaml. ValueError con el motivo si algo permitiría mezclar clientes; OSError si no existe."""
    ruta = Path(ruta)
    d = yaml.load(ruta.read_text(encoding="utf-8"), Loader=_SinRepetidos)  # noqa: S506 — subclase de SafeLoader
    if not (isinstance(d, dict) and isinstance(d.get("clientes"), dict)):
        raise ValueError("clientes.yaml debe tener `clientes:` con un cliente por slug")
    out, nombres_env = [], set(ENV_POR_CLIENTE)
    for slug, c in d["clientes"].items():
        if not (isinstance(slug, str) and SLUG.fullmatch(slug)):
            raise ValueError(f"slug inválido {slug!r}: solo minúsculas, números y guiones")
        if not isinstance(c, dict) or set(c) - {"carpeta", "dominio", "activo"}:
            raise ValueError(f"cliente {slug}: solo admite carpeta, dominio y activo (sobra {sorted(set(c) - {'carpeta', 'dominio', 'activo'}) if isinstance(c, dict) else c!r})")
        dominio = str(c.get("dominio") or "").lower()
        if not DOMINIO.fullmatch(dominio) or dominio.endswith(".localhost"):
            raise ValueError(f"cliente {slug}: dominio inválido {dominio!r} (sin https://, puerto ni ruta; .localhost está reservado)")
        if dominio in {x.dominio for x in out}:
            raise ValueError(f"cliente {slug}: el dominio {dominio} ya es de otro cliente")
        activo = c.get("activo", True)
        if not isinstance(activo, bool):
            raise ValueError(f"cliente {slug}: activo debe ser true o false")
        carpeta = (ruta.parent / str(c.get("carpeta") or "")).resolve()
        if not (c.get("carpeta") and (carpeta / "servicio.yaml").is_file()):
            raise ValueError(f"cliente {slug}: la carpeta {c.get('carpeta')!r} no existe o no tiene servicio.yaml")
        if any(carpeta == x.carpeta or carpeta.is_relative_to(x.carpeta) or x.carpeta.is_relative_to(carpeta) for x in out):
            raise ValueError(f"cliente {slug}: su carpeta es la de otro cliente o está dentro de ella (compartirían datos)")
        cfg = yaml.safe_load((carpeta / "servicio.yaml").read_text(encoding="utf-8"))
        if f := _fuera(carpeta, cfg if isinstance(cfg, dict) else {}):
            raise ValueError(f"cliente {slug}: {f.name} queda fuera de su carpeta")
        suyas = {_env(n, slug) for n in ENV_POR_CLIENTE}
        if suyas & nombres_env:  # p. ej. slug «personal»: PANEL_PIN_PERSONAL ya significa otra cosa
            raise ValueError(f"cliente {slug}: sus variables del .env se pisarían con las de otro ({', '.join(sorted(suyas & nombres_env))}); elija otro slug")
        nombres_env |= suyas
        out.append(Cliente(slug, carpeta, dominio, activo))
    return out
