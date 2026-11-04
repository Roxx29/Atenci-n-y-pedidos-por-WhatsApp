# Ajustes profesionales — enfoque comercial (comercial, 2026-11-04)

**Hoy:** Ajustes es un `<dialog>` modal (solo dueño) con 2 bloques: Personas/PIN y Menú (+fotos). Nombre, horario, envío, ITBMS, Yappy viven en `servicio.yaml`/`.env`: el dueño no puede tocarlos ni verlos. En una demo de venta eso es lo primero que preguntan ("¿y si cambio el precio del envío?").

## (a) Estructura
**Una página `/panel/ajustes` (no modal), con menú lateral/pestañas, secciones cortas.** Separar en páginas aparte abruma; un solo lugar = "aquí configuro todo". Orden por frecuencia de uso:
1. **Menú** (lo más usado: precio, disponible hoy, foto) — primera pestaña.
2. **Mi restaurante** (logo, nombre, contacto, colores).
3. **Pedidos y entrega** (horario, zonas/tarifa, ITBMS, mínimo).
4. **Pagos** (Yappy, transferencia).
5. **Equipo** (personal, repartidores, PIN).
Cuenta/plan/idioma: fuera (ver d). En móvil: lista de secciones → detalle.

## (b) Campos y permisos
| Campo | Quién |
|---|---|
| Logo, nombre, colores (1 color de acento de 6-8 predefinidos), teléfono, dirección, Instagram | dueño |
| Horario por día + turnos, "cerrar hoy / pausar pedidos" | dueño; **personal: solo pausar/reabrir** |
| Zonas y tarifa de envío, ITBMS %, pedido mínimo | dueño |
| Yappy, cuenta bancaria | dueño |
| Platos: precio, foto, categoría | dueño; **personal: solo "disponible hoy"** |
| Personas, roles, PIN | dueño |
Cambios sensibles (Yappy, cuenta, PIN) → pedir PIN del dueño otra vez y guardar en auditoría.

## (c) Enfoque comercial
- **Confianza:** el cliente final ve logo + nombre + datos de pago correctos; si el dueño ve su logo en panel, web y mensajes, siente que "es suyo". Vista previa en vivo ("así lo ve tu cliente") junto al formulario.
- **Valor en demo (mostrar en 2 min):** cambiar logo+nombre → se actualiza la web y el panel al instante; subir foto a un plato; "pausar pedidos" ; cambiar tarifa de envío y ver el total cambiar en el chat. Estos 4 cierran la venta.
- **Onboarding (primer arranque, asistente de 4 pasos, saltable):** 1 Nombre+logo → 2 Horario → 3 Menú (subir fotos/plantilla, mínimo 3 platos) → 4 Yappy/envío. Barra "Tu restaurante está 70 % listo". Al terminar: enlace de la web y QR para compartir.
- **Qué dejar fuera:** plantillas del bot/prompt, modelo de IA, esfuerzo, `caduca_min`, `cierre_cocina_min`, `herramientas`, integraciones. Valores técnicos quedan en YAML (soporte los toca); en el panel solo lenguaje del dueño.
- **Multi-restaurante en demo:** no vender selector todavía. Mostrar **un restaurante = una instalación** y una segunda demo ya cargada (otra marca: p. ej. "Café del Parque") para enseñar que el logo/colores/menú cambian sin código. Argumento: "cada restaurante tiene su propio servidor y datos" (también pro-Ley 81). Selector multi-local solo cuando un cliente tenga 2+ sucursales.

## (d) Riesgos / NO hacer
- No guardar configuración en dos sitios: elegir una fuente (BD `config`) y que `servicio.yaml` sea solo el valor por defecto; el motor lee de la BD. Si no, panel y bot se contradicen (precio/horario distintos).
- No permitir SVG/archivos arbitrarios como logo sin sanear: solo PNG/JPG/WebP, ≤1 MB, redimensionar.
- No editar tarifas/ITBMS con pedidos abiertos sin avisar: aplica a pedidos nuevos.
- No exponer PINs ni .env en pantalla; no dar al personal acceso a pagos.
- No colores libres (rompen contraste/accesibilidad): paleta curada.
- No inventar plan/facturación aún.

## (e) Fases
1. **F1 (demo):** página Ajustes con secciones; Mi restaurante (logo, nombre, acento, contacto) en BD y usado por web/panel/repartidor. Menú y Equipo migran tal cual.
2. **F2:** Pedidos y entrega + Pagos + pausar pedidos; el motor lee de BD. Auditoría de cambios.
3. **F3:** asistente de primer arranque + vista previa en vivo.
4. **F4 (nube):** "Duplicar restaurante" demo con otra marca; multi-sucursal solo bajo demanda.
Quién: disenador (UI/vista previa), agentedeprogramacion (tabla config, API, permisos), legal-datos (revisar datos de pago/auditoría).
