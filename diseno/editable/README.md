# Páginas a tu medida (kit de diseño)

Con este kit puedes cambiar **por completo** cómo se ven las páginas de un restaurante: la web donde piden los clientes, el panel del
restaurante y la app del repartidor. Otra distribución, otros textos, otras fotos, otra tipografía. Solo cambia para ese restaurante:
los demás siguen igual.

Lo que **no** cambia (lo pone el servidor, haga lo que haga la página): los precios, el impuesto y el envío, los pasos de un pedido,
los pagos y quién puede hacer qué. La página solo muestra y pide; el servidor decide.

## Qué hay aquí
| Carpeta o archivo | Para qué |
|---|---|
| `base/` | Copias de las 3 páginas de siempre (`index.html` web, `panel.html` panel, `repartidor.html` repartidor), con comentarios `=== … ===` que dicen qué es cada bloque. Punto de partida. |
| `ejemplo-carta/` | Un ejemplo distinto y funcionando: la web como una carta impresa con la comanda al lado. |
| `vista.py` y `vista-previa/` | Para ver tu página en tu ordenador con datos de mentira (platos, pedidos). |
| `API.md` | Lo técnico: qué le puede pedir la página al servidor. Para quien programe. |

## 1. Crea la carpeta del restaurante
1. Copia `base/` (o `ejemplo-carta/`) y ponle el nombre del restaurante, sin espacios ni tildes: `diseno/editable/la-fonda/`.
2. Deja solo las páginas que quieras cambiar. Si no pones `panel.html`, el panel sigue siendo el de siempre (igual con las otras dos).
3. Tus imágenes, estilos (`.css`), código (`.js`) y fuentes (`.woff2`) van dentro de esa carpeta (puedes hacer subcarpetas, p. ej. `img/`).

## 2. Qué puedes cambiar
- **Todo lo que se ve:** orden de las secciones, textos, colores, tamaños, fotos, iconos, animaciones.
- **Colores del restaurante:** usa `var(--primary)`, `var(--surface)`, `var(--on-surface)`… en tu CSS: así cambian solos si el restaurante cambia de tema.
- **El nombre:** donde escribas `DeliFood`, la página mostrará el nombre del restaurante.
- **Tus archivos** se enlazan siempre así: `/web/logo.png`, `/web/carta.css`, `/web/img/plato.webp` (con `/web/` delante).
- Solo se aceptan archivos `.html` (los 3 de arriba), `.css`, `.js`, `.png`, `.jpg`, `.webp`, `.svg` y `.woff2`. Nombres con letras, números, `-` y `_`.
- Tamaños máximos: cada página 500 KB, cada archivo 1 MB, todo junto 5 MB, hasta 60 archivos.

## 3. Qué NO tocar
- La línea `<!--TEMA-->` (dentro de `<head>`): ahí el servidor pone los colores. Si la borras, la página sale sin ellos.
- Las llamadas al servidor (`fetch(...)`, `api(...)`): sus rutas y datos están en `API.md`. Si cambias una, esa parte deja de funcionar.
- No cargues nada de otras webs (scripts, estilos, fuentes de Google, imágenes de otro sitio): el servidor lo bloquea. Si necesitas una fuente o imagen de fuera, dínoslo y la permitimos para ese restaurante.
- Los `.svg` no pueden llevar código (scripts) ni enlaces a otras webs: se rechazan al subir.
- No escribas datos privados en la página ni en sus archivos: son públicos.

## 4. Pruébala en tu ordenador
Necesitas Python (nada más). Desde la carpeta del proyecto:
```
python diseno/editable/vista.py diseno/editable/la-fonda
```
Abre en el navegador:
- Web de pedidos: <http://localhost:8090/>
- Panel: <http://localhost:8090/panel> (cualquier PIN de 6 números)
- Repartidor: <http://localhost:8090/repartidor/> (cualquier PIN de 6 números)

Los datos son de mentira (`vista-previa/datos.json`: puedes cambiar platos y pedidos para probar). Al guardar un archivo, la página se
recarga sola. El chat no es el asistente real: responde con textos de ejemplo («sí» confirma el pedido). Nada de lo que hagas aquí llega
al restaurante.

Para ver cómo queda con los colores de un tema concreto, pon su `tema.css` en tu carpeta.

## 5. Pásamela
Cuando te guste, dile al equipo: **«aplica lo de diseno/editable/la-fonda al cliente la-fonda»**. Se sube desde la consola del
operador (se revisa antes: el código de la página corre en el dominio del restaurante). Para volver a la página de siempre: «quita la
página propia de la-fonda».

---
**Para el operador:** subir = `POST /api/clientes/<slug>/pagina` (archivos o zip, ver MOTOR.md «Páginas propias»); queda en
`servicios/<slug>/web/`. Fuentes o imágenes de fuera: `web: {permitir: [fonts.googleapis.com, fonts.gstatic.com]}` en su `servicio.yaml`
(se lee al abrir el cliente: reinicia `alojar`).
