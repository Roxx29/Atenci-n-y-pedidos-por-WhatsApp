# Ajustes profesionales — propuesta del diseñador (2026-11-04)

**Hoy:** Ajustes es un `<dialog>` modal (`panel_web.html`, `#dlg-aj`) con personas+PIN y menú en una sola lista; solo rol `dueno`. Nombre, horario, envío, ITBMS, Yappy viven en `servicio.yaml`; PINs en `.env`. La marca "Fonda El Chitreano" y el color brasa están fijos en CSS/HTML.

## (a) Estructura: UNA zona Ajustes, varias secciones (no varias apps)
Sacarla del modal a una **vista a pantalla completa** dentro del panel (ruta `#/ajustes/<sección>`), como Shopify/Square: columna izquierda de secciones, contenido a la derecha; en móvil/tablet la columna pasa a lista de entrada y cada sección es una pantalla con "‹ Ajustes".
1. **Mi restaurante** (identidad + contacto + horario) · 2. **Menú** · 3. **Equipo** (personas, PIN, repartidores) · 4. **Pedidos y entrega** (envío por zona, mínimo, ITBMS, caducidad) · 5. **Pagos** (Yappy, transferencia) · 6. **Cuenta** (cambiar PIN dueño/personal, cerrar sesión; futuro: ARCO/auditoría).
Menú es la más usada: acceso directo también desde la barra del panel. No separar en páginas HTML distintas: mismo shell, misma sesión, un solo `panel_web.html` (o parcial por sección si crece).

## (b) Campos y permisos
| Sección | Campos | Quién |
|---|---|---|
| Mi restaurante | logo (PNG/SVG/JPG ≤1 MB), nombre, lema, color de marca, teléfono, dirección, horario por día/turnos | dueño |
| Menú | plato: nombre, precio, categoría, descripción, foto, disponible/agotado, orden | dueño edita todo; **personal solo marca agotado/disponible** |
| Equipo | nombre, rol, PIN, activo; borrar | dueño |
| Pedidos y entrega | zonas+tarifa, alias, pedido mínimo, ITBMS %, caducidad, cierre cocina | dueño |
| Pagos | Yappy, banco/transferencia | dueño |
| Cuenta | cambiar PIN propio | cada rol |
Todo se guarda en BD (tabla clave→valor `ajustes`); `servicio.yaml` queda como valor por defecto/semilla.

## (c) Enfoque de diseño
- **Identidad con vista previa en vivo:** a la derecha del formulario, 3 miniaturas que se actualizan al teclear: cabecera web del cliente, cabecera del panel y burbuja del bot. Logo con arrastrar-y-soltar, recorte cuadrado, y **fallback = monograma** con iniciales sobre el color de marca si no hay logo.
- **Color de marca:** el dueño elige 1 color (con 6 muestras sugeridas + hex); el servidor deriva los tokens M3 (`primary`, `primary-container`, `primary-fixed`, versión oscura) y **valida contraste ≥4.5:1** de texto sobre primary; si falla, ajusta la luminosidad y avisa. Se aplica a web, panel y repartidor vía `<style>:root{…}` inyectado por el motor (sin tocar el CSS Fuego & Brasa; brasa = valor por defecto). Fuentes no se tocan.
- **Editor de menú:** categorías como secciones colapsables; reordenar con **arrastrar + botones ↑↓** (accesible por teclado); interruptor "Agotado" en la fila (acción más frecuente, un toque); foto con miniatura y "cambiar/quitar"; plato nuevo en panel lateral/hoja inferior, no formulario al pie.
- **Guardado:** autoguardado por campo con "Guardado ✓" o botón Guardar por sección (preferible para identidad: un "Publicar cambios" evita ver la marca a medias). Deshacer 8 s al borrar.
- **Estados vacíos:** menú sin platos → ilustración simple + "Añade tu primer plato" y botón; equipo vacío; sin logo → monograma + "Sube tu logo".
- **Móvil/tablet:** secciones en una columna, objetivos ≥48 px, hoja inferior para editar, probar 375 y 768 px (pendiente ya anotado en IDEAS).
- **Accesibilidad:** `nav` con `aria-current`, `label` real en cada campo, errores en `aria-live`, foco al abrir secciones, no solo color para estados (texto "Agotado"), arrastre con alternativa de teclado, `prefers-reduced-motion`.

## (d) Riesgos / NO hacer
- NO permitir color libre sin validar contraste ni logos SVG sin sanear (XSS): subir como imagen `<img>`, nunca inline; limitar tipo/tamaño y re-codificar.
- NO exponer PIN en claro tras guardarlo; NO dejar al personal ver Pagos/Equipo (el servidor responde 403, no solo ocultar en UI).
- NO tocar `.env` desde la UI (PIN dueño se cambia en Cuenta con hash en BD, con migración del `.env`).
- NO prometer temas por cliente en el chat de WhatsApp (el bot no tiene logo); solo textos/nombre.
- Cambiar horario/envío debe reflejarse en el bot y la web sin reiniciar (leer de BD, no de YAML cacheado) → coordinar con agentedeprogramacion.
- NO rediseñar Fuego & Brasa: solo parametrizar el color principal y el logo.

## (e) Fases
1. **Shell Ajustes** (vista completa + nav de secciones, mover lo actual: Equipo y Menú tal cual) + borrar personas (IDEAS 55).
2. **Mi restaurante:** tabla `ajustes`, logo+nombre+color con vista previa y tokens inyectados en web/panel/repartidor.
3. **Menú pro:** categorías, orden, agotado rápido (también para personal), estados vacíos.
4. **Pedidos y entrega + Pagos** desde BD (envío, ITBMS, mínimo, Yappy) con el motor leyendo de BD.
5. **Cuenta:** cambio de PIN; pulido móvil/AA, prueba en vivo a 375/768 px.
Dueños: disenador (UI/CSS/tokens, 1,2,3,5) · agentedeprogramacion (API, BD, permisos, 2,4) · comercial (textos y qué se ofrece como "personalizable" en el precio).
