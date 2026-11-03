# Web de pedidos — Fonda El Chitreano

Prototipo: `diseno/web-pedidos.html` (un solo archivo, CSS y JS en línea, sin CDN). Abierto con doble clic (`file://`) o sin servidor entra en **modo demo** con un bot de guion y avisos simulados.

## Decisiones
- **Móvil primero, modo "operar".** Tres vistas: entrada → Chat / Mi pedido (pestañas) + Menú (botón arriba; columna fija a la izquierda desde 900 px).
- **Mundo visual: fonda chitreana.** Papel crema, tinta café, rojo achiote (acción), amarillo maíz (resumen del pedido), verde hoja (pasos hechos). Títulos en serif del sistema (Iowan/Palatino/Georgia) porque no se permiten fuentes externas; texto en `system-ui`. Modo oscuro automático para pedidos de cena.
- **Menú → chat.** "Pedir" en el menú escribe la frase en el chat ("Quiero 1 sancocho de gallina"); el bot sigue siendo quien arma y confirma el pedido. Agotados tachados con "Agotado hoy".
- **Resumen destacado** fijo sobre la caja de texto: número, estado y total; se despliega para ver el detalle con ITBMS y envío.
- **Estado del pedido:** pasos verticales confirmado → pago reportado → pagado → en preparación → listo, con check, paso actual resaltado y texto (no solo color). Datos de pago con botón Copiar mientras está "confirmado".
- **Aviso de cambio** (pagado, en preparación, listo, entregado, cancelado): banner arriba (`role=status`), línea en el chat, punto en la pestaña "Mi pedido", título de la pestaña del navegador y vibración corta.
- **Comprobante de pago:** en "Mi pedido", la tarjeta de pago va arriba de los pasos mientras el pedido está confirmado; desde el chat, el botón "Enviar comprobante de pago" bajo el resumen lleva ahí. Elegir foto (galería o cámara, sin forzar cámara porque la captura de Yappy está en la galería) → miniatura + referencia opcional → Enviar (barra de progreso) → "Comprobante recibido · pendiente de verificación" con opción de enviar otra foto. La web reduce a JPEG ≤1600 px. Texto de legal-datos junto al botón.
- **Espera del bot:** a los 3 s el indicador añade "Es una demostración: la respuesta puede tardar unos 30 s."
- **Dinero:** todo total pasa por `dinero()` → siempre "B/. 0.00" (lee números o texto "B/. 7.00").
- **Privacidad:** casilla sin marcar, junto al botón, con el aviso completo de legal-datos (borrador) en un desplegable. Los `[corchetes]` los completa el restaurante.

## Contrato con la API (acordado con agentedeprogramacion)
- `GET /aviso` → `{version, texto}`: el texto se pinta en "Ver aviso de privacidad" (textContent); el bloque fijo es solo para el modo demo.
- `POST /entrar {nombre, telefono, acepta_privacidad, version_aviso}` → cookie firmada; 400 → `{error}` bajo el teléfono; si el aviso cambió, recarga el aviso, desmarca la casilla y pide aceptarlo otra vez.
- `POST /chat {texto}` → `{respuesta, pedido?}`; 401 → vuelve a la entrada.
- Cada 5 s: `GET /pedidos` (pinta el más reciente) y `GET /avisos` → `{mensajes}` (burbujas del bot).
- `POST /comprobante?pedido=<id>&referencia=<≤40>` (cookie), cuerpo = JPEG crudo → `{ok, pedido}`; 400/404/409/413/415 → `{error}`; 401 → entrada. CSP: `img-src 'self' blob: data:`.
- Estado `caducado` se pinta "Caducó, pide de nuevo".
- `GET /menu` (números en dólares); si falla, usa el JSON en línea `#datos-menu`.
- Dinero acepta número o texto "B/. 7.00". Texto del usuario y del servidor siempre con `textContent`.

## Accesibilidad
Etiquetas visibles, errores junto al campo y foco al primero, objetivos ≥ 44 px, `role=log` en el chat, pestañas con flechas, foco visible en amarillo maíz, `prefers-reduced-motion`.
