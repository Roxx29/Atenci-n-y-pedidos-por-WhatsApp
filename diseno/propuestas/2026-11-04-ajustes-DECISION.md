# Ajustes profesionales — DECISIÓN (ronda 2, disenador, 2026-11-04)
Fusiona las 3 propuestas. **⚠ = discrepaban**. Base: la de programación (datos/API) + UX del disenador + demo/onboarding de comercial.

## 1. Estructura final
Los tres coinciden: **una vista Ajustes a pantalla completa (no modal), no varias páginas**. Lateral en escritorio; en móvil lista → detalle con "‹ Ajustes". Un HTML/JS/API, ruta interna `#/ajustes/<sección>`.
Secciones, en este orden: **1 Menú · 2 Mi restaurante · 3 Pedidos y entrega · 4 Pagos · 5 Equipo · 6 Cuenta**.
- ⚠ Orden: yo ponía Mi restaurante primero, comercial Menú primero (uso diario: precio, agotado). **Decidido: Menú primero** (frecuencia de uso); la identidad se cubre en el asistente de primer arranque.
- ⚠ Cuenta: comercial la sacaba; programación/yo la mantenemos. **Decidido: se queda**, solo "cambiar mi PIN" (cada rol).

## 2. Campos y permisos (servidor responde 403, no solo oculta en UI)
| Sección | Campos | Dueño | Personal |
|---|---|---|---|
| Menú | nombre, precio, categoría, descripción, foto, orden, disponible | todo | **solo disponible/agotado** |
| Mi restaurante | logo, nombre, `negocio` (frase del bot), acento, teléfono, dirección, Instagram, lat/lon | sí | no |
| Pedidos y entrega | horario (días+turnos), **pausar pedidos**, zonas+tarifa+alias, ITBMS %, mínimo, caducidad, cierre cocina | sí | **solo pausar/reabrir** |
| Pagos | Yappy, transferencia | sí | no |
| Equipo | personas, rol, PIN, activo, borrar | sí | no |
- ⚠ `caduca_min`/`cierre_cocina_min`: comercial los dejaba en YAML; programación los listaba. **Decidido: editables pero bajo "Avanzado" plegado** (el soporte los suele tocar). Fuera del panel siempre: `modelo`, `herramientas`, `instrucciones`, `.env`.
- ⚠ "Pausar pedidos": solo comercial lo pedía. **Adoptado** (clave `pausa`, útil en demo y para el personal; el bot y la web responden "cerrado temporalmente").
- ⚠ Cambios sensibles: comercial pedía re-PIN + auditoría; programación auditoría + aviso en bandeja. **Decidido: ambos** para Pagos y PIN (re-pedir PIN del dueño, `auditar`, aviso "Yappy cambiado por X").

## 3. Modelo de datos (adopta programación)
- Tabla `ajustes(clave PK, valor JSON, at, por)`; `servicio.yaml` = semilla/por defecto; `sv.cfg` = YAML ∪ filas (merge profundo, **lista blanca**), sin escribir el YAML. Prompt armado por turno (nombre/horario sin reiniciar).
- Logo: BLOB **WebP RGBA** (96/192 px alto, ≤20 KB, conserva transparencia), `GET /marca/logo.webp?v=hash`, favicon del mismo BLOB. ⚠ Tamaño de subida: comercial/yo 1 MB, programación 3 MB → **3 MB de entrada** (reutiliza `procesar_foto`), salida ≤20 KB. SVG: **rechazado (415)** — los 3 coincidimos.
- `GET /config` público sin datos sensibles (`nombre, color, logo, telefono, horario, abierto_ahora, pausa`); nada de transferencia/PIN.
- **Un solo color de acento** `#rrggbb` con contraste WCAG ≥4.5 calculado en servidor. ⚠ Comercial: paleta curada 6-8; programación: hex libre validado/rechazado; yo: muestras + hex con auto-ajuste. **Decidido: 8 muestras curadas + hex libre; el servidor rechaza lo que no cumple y propone el tono válido más cercano** (no cambia en silencio). Sin CSS libre ni temas. Derivados M3 (container/fixed/oscuro) los calcula el servidor; Fuego & Brasa = valor por defecto.
- Validadores por clave (horario HH:MM inicio<fin sin cruzar medianoche, zonas ≥0, ITBMS 0-15, Yappy regex). Precios/envío/ITBMS no alteran pedidos ya creados.

## 4. Demo de venta y onboarding (comercial)
Demo de 2 min, debe funcionar en F1-F2: cambiar logo+nombre → web y panel cambian al instante; foto de plato; pausar pedidos; cambiar tarifa de envío y ver el total en el chat. Segunda demo "Café del Parque" con otra marca (sin selector multi-local).
Onboarding: asistente 4 pasos saltable (Nombre+logo → Horario → Menú ≥3 platos → Yappy/envío), barra "70 % listo", al final enlace + QR. ⚠ Comercial lo ponía F3 con vista previa; **se mantiene en F4, pero la vista previa en vivo entra ya en F1** (barata y es el "wow" de la demo).

## 5. Fases finales
| # | Fase | Quién | Tamaño |
|---|---|---|---|
| 1 | Datos: tabla `ajustes`, merge `cfg`+lista blanca, prompt por turno, `procesar_imagen`, logo, `/config`, auditoría | arquitecto (diseño merge/prompt) + programador; tester | M (~250 L + ~15 pruebas) |
| 2 | Shell Ajustes (vista completa, nav, Menú+Equipo movidos tal cual, borrar personas) + **Mi restaurante** con vista previa; web/panel/repartidor usan marca y acento | disenador (UI/CSS/tokens) + programador (JS/API) | M-L |
| 3 | Pedidos y entrega + Pagos + pausar pedidos + re-PIN/aviso; personal → 403 salvo disponible/pausa | programador; tester; revisor (seguridad) | M (~200 L + ~20 pruebas) |
| 4 | Menú pro: categorías, arrastre+teclado, agotado rápido, estados vacíos, Cuenta (cambiar PIN) | disenador + programador | M |
| 5 | Asistente primer arranque + QR; pulido móvil/AA (375/768 px) | disenador + programador-rapido | S-M |
| 6 | Revisión legal de datos de pago/auditoría/`/config` público (puede ir en paralelo desde F3) | legal-datos | S |
| 7 | Opcional: exportar/importar ajustes JSON para clonar restaurante ("Duplicar demo") | programador | S |
Revisor cierra cada fase antes de integrar en master.
