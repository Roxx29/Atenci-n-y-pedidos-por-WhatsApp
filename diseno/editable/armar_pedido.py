"""Genera PEDIR-DISENO.md: un solo archivo para subir a otro chat de IA junto con la página base que quieras rediseñar.
Uso (desde la carpeta del proyecto):  python diseno/editable/armar_pedido.py
Vuelve a ejecutarlo cuando cambie API.md o el README (así el archivo nunca queda viejo)."""
from pathlib import Path

aqui = Path(__file__).parent
intro = """# Pedido de diseño: páginas a medida para un restaurante

**Para la IA que lea esto:** eres diseñador/a y desarrollador/a front-end. Te adjunto una página base (HTML) de una app de pedidos para
restaurantes de Panamá. Tu trabajo es **rediseñarla por completo** (distribución, estilo, tipografía, animaciones, textos) para UN restaurante
concreto, sin romper su funcionamiento. El servidor ya existe y no se toca: tu página solo muestra y pide, y el servidor decide precios, pagos y reglas.

## Lo que el usuario te dirá (rellénalo si falta, pregunta una sola vez)
- Nombre y tipo de restaurante, y su ambiente (moderno, familiar, elegante, callejero…).
- Colores y tono. Si tiene logo, lo adjuntará como `logo.png`/`logo.svg`.
- Qué página rediseñar: la web de pedidos (`index.html`), el panel del restaurante (`panel.html`) o la app del repartidor (`repartidor.html`).
- Qué quiere distinto (ej.: menú en rejilla con fotos grandes, carta elegante, cabecera con foto, etc.).

## Cómo debes entregar el resultado
1. **Archivos completos listos para guardar** (nunca fragmentos ni «aquí va el resto»): `index.html` (o `panel.html` / `repartidor.html`), más los
   `.css`/`.js` que separes, cada uno en su bloque de código con su nombre de archivo encima.
2. Mantén **tal cual** la lógica de comunicación con el servidor del HTML base (las llamadas `fetch(...)`/`api(...)`, rutas, nombres de campos,
   sesión/cookies, sondeos). Puedes reorganizar el HTML y el CSS y reescribir cómo se pintan los datos, pero no cambies qué se pide ni cómo.
3. Conserva la línea `<!--TEMA-->` dentro de `<head>` (ahí el servidor inyecta los colores del restaurante) y el texto `DeliFood` donde aparece el
   nombre (el servidor lo cambia por el nombre real).
4. Usa los colores con variables CSS (`var(--primary)`, `var(--surface)`, `var(--on-surface)`, …) para que sigan funcionando con los temas.
5. Los archivos propios se enlazan con `/web/` delante (`/web/carta.css`, `/web/img/plato.webp`). Solo `.html`, `.css`, `.js`, `.png`, `.jpg`,
   `.webp`, `.svg`, `.woff2`; nombres con letras, números, `-` y `_`.
6. **Nada externo:** ni scripts, ni Google Fonts, ni CDN, ni imágenes de otras webs (el servidor lo bloquea). Fuentes e imágenes, dentro de la carpeta.
7. Móvil primero (360 px), accesible (contraste, foco, botones ≥44 px, `prefers-reduced-motion`), sin dependencias nuevas.
8. Al final, una lista corta: qué archivos entregas y qué cambiaste.

Debajo van las reglas del kit y el contrato completo de la API (lo que la página puede pedir al servidor).

---

"""
readme = (aqui / "README.md").read_text(encoding="utf-8")
api = (aqui / "API.md").read_text(encoding="utf-8")
(aqui / "PEDIR-DISENO.md").write_text(intro + "# Reglas del kit\n\n" + readme + "\n\n---\n\n" + api, encoding="utf-8")
print("Listo: diseno/editable/PEDIR-DISENO.md (" + str(len((aqui / "PEDIR-DISENO.md").read_bytes()) // 1024) + " KB)")
