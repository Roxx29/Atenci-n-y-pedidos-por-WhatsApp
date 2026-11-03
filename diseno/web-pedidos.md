# Web de pedidos — Fonda El Chitreano

Prototipo: `diseno/web-pedidos.html` (un solo archivo, CSS y JS en línea, sin CDN). Abierto con doble clic (`file://`) o sin servidor entra en **modo demo** con un bot de guion y avisos simulados.

## Decisiones
- **Móvil primero, modo "operar".** Tres vistas: entrada → Chat / Mi pedido (pestañas) + Menú (botón arriba; columna fija a la izquierda desde 900 px).
- **Mundo visual: fonda chitreana.** Papel crema, tinta café, rojo achiote (acción), amarillo maíz (resumen del pedido), verde hoja (pasos hechos). Títulos en serif del sistema (Iowan/Palatino/Georgia) porque no se permiten fuentes externas; texto en `system-ui`. Modo oscuro automático para pedidos de cena.
- **Menú → chat.** "Pedir" en el menú escribe la frase en el chat ("Quiero 1 sancocho de gallina"); el bot sigue siendo quien arma y confirma el pedido. Agotados tachados con "Agotado hoy".
- **Resumen destacado** fijo sobre la caja de texto: número, estado y total; se despliega para ver el detalle con ITBMS y envío.
- **Estado del pedido:** pasos verticales confirmado → pago reportado → pagado → en preparación → listo, con check, paso actual resaltado y texto (no solo color). Datos de pago con botón Copiar mientras está "confirmado".
- **Aviso de cambio** (pagado, en preparación, listo, entregado, cancelado): banner arriba (`role=status`), línea en el chat, punto en la pestaña "Mi pedido", título de la pestaña del navegador y vibración corta.
- **Privacidad:** casilla sin marcar, junto al botón, con el aviso completo de legal-datos (borrador) en un desplegable. Los `[corchetes]` los completa el restaurante.

## Contrato con la API (acordado con agentedeprogramacion)
- `POST /entrar {nombre, telefono, acepta_privacidad}` → cookie firmada; 400 → `{error}` se muestra bajo el teléfono.
- `POST /chat {texto}` → `{respuesta, pedido?}`; 401 → vuelve a la entrada.
- Cada 5 s: `GET /pedidos` (pinta el más reciente) y `GET /avisos` → `{mensajes}` (burbujas del bot).
- `GET /menu` (números en dólares); si falla, usa el JSON en línea `#datos-menu`.
- Dinero acepta número o texto "B/. 7.00". Texto del usuario y del servidor siempre con `textContent`.

## Accesibilidad
Etiquetas visibles, errores junto al campo y foco al primero, objetivos ≥ 44 px, `role=log` en el chat, pestañas con flechas, foco visible en amarillo maíz, `prefers-reduced-motion`.
