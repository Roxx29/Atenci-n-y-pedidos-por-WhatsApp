# Propuesta: interacción y acabado premium

Solo investigación, sin implementar. Se revisó `motor/web_index.html` (731 líneas), `motor/panel_web.html` (748) y `motor/repartidor.html` (341), más `diseno/*.md`.
Marca actual (no cambia): papel crema, tinta café, achiote `#A8321F`, maíz `#E9B949`, hoja `#2F6B3A`.

**Regla de oro:** todo es mejora progresiva. Un celular barato sin soporte ve la página de hoy; uno moderno ve la versión viva. Nada de librerías: solo CSS/JS nativo y menos de 5 KB de JS nuevo por página.

Soporte de las técnicas (a nov. 2026):
- Seguras en cualquier móvil actual: `@starting-style`, `:has()`, container queries, `<dialog>`, popover, `color-mix()`, `text-wrap`, `content-visibility`.
- Mejora progresiva (envolver en `@supports` o comprobar con `if (document.startViewTransition)`): View Transitions de mismo documento y animaciones guiadas por scroll (`animation-timeline`).
- Solo Chrome/Android: `field-sizing:content` y `navigator.vibrate`. En iOS no hay `vibrate`.

---

## 1. Diagnóstico corto

### Web de pedidos (`web_index.html`)
- **Menú sin imágenes ni jerarquía** (`.plato`, l.140–146): lista plana de texto con un botón "Pedir" que solo escribe en el chat. No hay cantidad, carrito ni retroalimentación. Es lo que más se ve barato.
- **Todo aparece de golpe.** Burbujas (l.488), pestañas, avisos y pasos entran sin transición. El indicador "está escribiendo…" (l.498) es texto fijo, sin puntos animados.
- **Cambio de pestañas** Chat / Mi pedido / Menú con `hidden`: sin transición ni continuidad espacial.
- **Estado del pedido** (`ol.pasos`): correcto pero estático. El paso actual no "respira" y al avanzar no hay celebración.
- **Tipografía serif del sistema** (`Iowan/Palatino/Georgia`): en un Android barato (el cliente típico) no existe ninguna de las tres y cae a `serif` genérico. La marca se ve distinta en cada teléfono.
- **Sin estados de carga**: el menú y los pedidos aparecen cuando llegan, sin esqueleto. Con 3G se ve un hueco.
- Usa `innerHTML` en l.470 y l.545. Hoy el contenido es estático y está bien, pero conviene pasarlo a nodos al tocar esas líneas.

### Panel del restaurante (`panel_web.html`)
- **El tablero se repinta entero** (`replaceChildren`, l.418) cada sondeo de 3 s. Las tarjetas "saltan": se pierden scroll interno, animaciones y foco. Y un pedido que cambia de columna simplemente desaparece y reaparece.
- **Sin visión global**: no hay un solo número de resumen (pedidos de hoy, ventas, tiempo medio). El dueño tiene que contar columnas.
- **Urgencia solo como texto** ("2 min", chip rojo): no se percibe de lejos en un local con ruido y luz.
- **Sin modo oscuro** (0 reglas `prefers-color-scheme`), aunque la web de pedidos sí lo tiene y el panel se usa de noche.
- **Estados vacíos** "Nada por aquí": sin ilustración ni guía. Sin esqueleto en la primera carga.
- Solo sonido (WebAudio) y `document.title` como alerta; si la pestaña está en segundo plano o la pantalla se apaga, el aviso se pierde.

### App del repartidor (`repartidor.html`)
- **Botón grande "Entregado" de un toque**: fácil de pulsar por error con una mano en moto, y sin confirmación ni gesto.
- **Sin modo oscuro** para repartir de noche (0 reglas), con un fondo crema que deslumbra.
- **Pantalla que se apaga** durante la entrega y la lista se refresca cada 6 s sin dar señal visible.
- **Sin señal de conexión**: aunque hay service worker, el repartidor no sabe si está en línea o viendo datos viejos.
- **Lista y detalle sin transición**: al tocar un pedido hay un corte seco. Vibración solo en el aviso (l.321), no en las acciones.

---

## 2. Mejoras priorizadas

Esfuerzo: S ≤ 1 h · M ≈ media jornada · L ≥ 1 jornada. Impacto: ★ bajo … ★★★ alto.

### 2.1 Web de pedidos
| # | Qué | Por qué | Técnica | Esf. | Imp. |
|---|---|---|---|---|---|
| W1 | **Entradas suaves** de burbujas, avisos y tarjetas | Sensación de app viva | `@starting-style` + `transition` en `.burbuja`, `.aviso`; `animation: entra .25s` con `translateY(6px)`+`opacity` | S | ★★★ |
| W2 | **Puntos animados** de "escribiendo" | El bot tarda hasta 30 s: debe parecer que trabaja | 3 `<i>` con `@keyframes` escalonado; el texto de la espera sigue en `.sr` | S | ★★ |
| W3 | **Transición entre pestañas** | Continuidad espacial | `document.startViewTransition(() => pestana(x))` con fallback; `::view-transition-old/new(root){animation-duration:.18s}` | S | ★★ |
| W4 | **Esqueleto** del menú y de Mi pedido | En 3G se ve un hueco blanco | `.esq{background:linear-gradient(90deg,var(--carril) 25%,var(--papel) 50%,var(--carril) 75%);background-size:200%;animation:brillo 1.2s infinite}` | S | ★★ |
| W5 | **Menú con foto opcional** y "mini-carrito" | Es lo que decide el pedido | Campo `foto` opcional por plato (WebP 160×160, ≤ 12 KB, `loading="lazy"`, `aspect-ratio`, fondo con el color medio). Sin foto: inicial del plato sobre `--maiz-suave` | M | ★★★ |
| W6 | **Stepper − 1 + en cada plato**; al pulsar, barra "Tu pedido: 3 platos · B/. 18.50 · Enviar al chat" | Hoy "Pedir" es opaco | Estado local en un `Map`; "Enviar" escribe la frase en el chat como ahora (el bot sigue confirmando). El resumen se pinta con `popover` o una barra `position:sticky` | M | ★★★ |
| W7 | **Pasos del pedido vivos** | Es el momento de más ansiedad del cliente | Paso actual con `@keyframes pulso` (anillo); al cambiar de estado, el check se "dibuja" (`stroke-dashoffset`) y la línea se llena (`transform:scaleY`) | S | ★★ |
| W8 | **Fuente de marca propia** | Evita el serif genérico de Android | Una sola fuente display variable, subconjunto latino (`woff2` ≤ 35 KB, propia, `font-display:swap`, `<link rel=preload>`); texto sigue en `system-ui` | M | ★★★ |
| W9 | **Hoja de resumen con `::details-content`** | Hoy el desplegable "corta" | `details::details-content{transition:block-size .25s,content-visibility .25s allow-discrete}` + `interpolate-size:allow-keywords`; fallback: `grid-template-rows:0fr→1fr` | S | ★ |
| W10 | **Textarea que crece sola** y botón enviar con rebote | Pulido del chat | `textarea{field-sizing:content}` (en otros, el JS actual); `.enviar:active{transform:scale(.92)}` | S | ★ |
| W11 | **Háptica** al enviar, confirmar y cambio de estado | Refuerza el "se hizo" | `navigator.vibrate?.(10)` en acciones, `[30,40,30]` en cambio de estado. En iOS no hay: usar `<input type="checkbox" switch>` en la casilla de privacidad (Safari 18 vibra) | S | ★ |
| W12 | **Listas largas**: chat y menú | Rendimiento en gama baja | `content-visibility:auto; contain-intrinsic-size:auto 64px` en `.plato` y `.burbuja` | S | ★ |

### 2.2 Panel del restaurante
| # | Qué | Por qué | Técnica | Esf. | Imp. |
|---|---|---|---|---|---|
| P1 | **Repintado con identidad**: no destruir el DOM; una tarjeta que cambia de columna *vuela* | Quita los saltos y se entiende qué pasó | Reutilizar nodos por `data-id`, y envolver el cambio en `document.startViewTransition()` con `view-transition-name: p-<id>` en cada tarjeta. Fallback: repintado actual | M | ★★★ |
| P2 | **Franja de resumen** arriba: pedidos hoy · en cocina · ventas B/. · tiempo medio | El dueño ve el día de un vistazo | Se calcula en el cliente con lo que ya llega en `/panel/estado`; números con `font-variant-numeric:tabular-nums` y conteo animado de 300 ms | M | ★★★ |
| P3 | **Urgencia visible**: barra fina bajo la cabecera de la tarjeta que se llena hasta el límite (5/25/15 min) y pasa de maíz a achiote | Se lee de lejos, sin leer números | `--p: calc(min(1, min/limite))` en la tarjeta; `background:linear-gradient(90deg,var(--maiz) calc(var(--p)*100%),transparent 0)`; tarjeta tarde: borde achiote + pulso lento (reduced-motion: sin pulso) | S | ★★★ |
| P4 | **Modo oscuro** | Se usa de noche; menos cansancio | Mismos tokens con `light-dark()` / `@media (prefers-color-scheme:dark)`; los de la web de pedidos ya sirven de base. Mantener contraste AA | M | ★★ |
| P5 | **Primera carga con esqueleto** y estados vacíos con SVG | Evita el tablero en blanco y aporta marca | 6 columnas `.esq`; SVG inline de una olla/bolsa en `.vacia` con texto guía ("Cuando llegue un pedido, suena y aparece aquí") | S | ★★ |
| P6 | **Pantalla siempre encendida** y **globo en el icono** | El panel vive en una tablet fija; que no se duerma y muestre pendientes | `navigator.wakeLock.request('screen')` (re-pedir en `visibilitychange`); `navigator.setAppBadge?.(n)` con los Nuevos | S | ★★★ |
| P7 | **Notificación del sistema** cuando la pestaña está oculta | Hoy solo suena si la pestaña vive | `Notification` + el service worker existente; pedir permiso tras el primer toque en "Entrar" | M | ★★ |
| P8 | **Teclado** para PC: `N` salta al primer Nuevo, `A` abre Avisos, `Esc` cierra, flechas entre tarjetas | Atender sin ratón | Un `keydown` global con `aria-keyshortcuts` | S | ★ |
| P9 | **Tarjetas adaptables** a la columna | En tablet vertical y teléfono la misma tarjeta se ve apretada | `container-type:inline-size` en `.col`; `@container (max-width:240px){.t-pie{flex-wrap:wrap}}`. Se suma al CSS móvil ya hecho (≤700 px) | M | ★★ |
| P10 | **Microinteracciones de acción**: botón con estado "enviando…" → check en `--hoja`; toast con deshacer 5 s (solo donde la API lo permita) | Confianza al tocar | `.btn[aria-busy=true]` con spinner CSS; ya existe `disabled` | S | ★★ |

Pendiente aparte (necesita programación): arrastrar tarjetas entre columnas. No se propone: el servidor solo permite las acciones de `acciones[]`.

### 2.3 App del repartidor
| # | Qué | Por qué | Técnica | Esf. | Imp. |
|---|---|---|---|---|---|
| R1 | **Deslizar para confirmar** "Salí" / "Entregado" | Evita errores con una mano en moto y se siente premium | `<input type="range">` estilizado o `pointerdown/move/up` sobre un carril; al soltar > 85 % dispara la acción; si no, rebota. Alternativa accesible: el botón actual sigue disponible con doble pulsación | M | ★★★ |
| R2 | **Pantalla encendida** mientras hay entrega en camino | Que no se apague en la ruta | `navigator.wakeLock` solo con pedidos `en_camino` | S | ★★★ |
| R3 | **Modo oscuro** (y alto contraste exterior) | Nocturno y sol directo | `prefers-color-scheme:dark` + fondo `#1A1310`; texto 18 px+ se mantiene | S | ★★★ |
| R4 | **Transición lista → detalle** | Quita el corte seco | `startViewTransition` + `view-transition-name:p-<id>` en el número del pedido | S | ★★ |
| R5 | **Píldora de conexión** "En línea · hace 4 s" / "Sin señal: datos de las 3:42" | Confianza en datos viejos | `navigator.onLine` + hora del último sondeo; se pinta en la barra | S | ★★★ |
| R6 | **Háptica en acciones** y en pedido nuevo | Feedback sin mirar | `navigator.vibrate?.([200,100,200])` ya existe para avisos; añadir `15` al pulsar y `[40,60,40]` al confirmar | S | ★★ |
| R7 | **Entrada escalonada** de la lista y "tirar para refrescar" | Vida y control sobre los 6 s del sondeo | `animation-delay:calc(var(--i)*40ms)`; `overscroll-behavior:contain` + botón "Actualizar" con giro | S | ★★ |
| R8 | **Globo en el icono** con pedidos asignados | PWA instalada | `navigator.setAppBadge?.(n)` | S | ★ |

---

## 3. Dirección visual premium

**Idea:** *fonda chitreana, pero cuidada*: papel, tinta y especias; calma y calidez, no brillo.

- **Tipografía.** Display: una serif con carácter propia y subconjunto (W8), solo para títulos, números de pedido y totales. Texto: `system-ui`. Escala 1.25 (14 · 16 · 20 · 25 · 31). `text-wrap:balance` en títulos y `pretty` en párrafos. Números siempre `tabular-nums`.
- **Color.** Mantener los 4 tokens. El achiote solo para la acción principal; el maíz solo para "atención/nuevo"; la hoja solo para "hecho". Superficies en capas: `--papel` → `--carril` → `--superficie`. Mezclas con `color-mix(in oklab, …)` en lugar de colores nuevos. Modo oscuro en las 3 páginas, con achiote más claro (ya definido) y sin negro puro.
- **Espaciado y forma.** Escala de 4 px (4·8·12·16·24·32). Radios: 12 px tarjetas, 999 px botones y chips (se mantiene). Sombra en un solo nivel; los bordes cálidos (`--linea`) hacen el trabajo.
- **Movimiento.** Tres duraciones: 120 ms (toque), 220 ms (entrada/salida), 360 ms (cambio de estado importante). Curva `cubic-bezier(.16,1,.3,1)` al entrar, `ease-in` más corta al salir. Solo `transform` y `opacity`. Con `prefers-reduced-motion` desaparecen desplazamientos y pulsos y quedan cambios de color y de opacidad.
- **Microinteracciones.** Pulsar = `scale(.97)`; enviar = rebote del botón; paso hecho = check dibujado; pedido nuevo en el panel = anillo maíz (ya existe) y contador que sube; entregado = check verde que se llena.
- **Carga.** Esqueletos del mismo tamaño que el contenido (sin saltos de layout). Nunca spinner a pantalla completa. Botones con `aria-busy`.
- **Vacío.** Una línea de marca + una acción o una guía; SVG de una sola tinta (`currentColor`), 80 px.
- **Háptica.** Android: `vibrate` 10–15 ms al tocar, 30–60 ms al confirmar, patrón largo para avisos. iOS: solo el truco de `switch`. Siempre opcional, nunca la única señal.
- **Rendimiento (3G / gama baja).** Sin librerías. Presupuesto: HTML + CSS + JS ≤ 60 KB comprimido por página; fuente ≤ 35 KB; imágenes ≤ 12 KB, WebP, `lazy`. `content-visibility` en listas. Nada de `backdrop-filter` ni sombras grandes animadas.
- **Accesibilidad.** Contraste AA, objetivos ≥ 48 px, foco visible maíz (ya existe), `aria-live` sin cambios, y toda animación con alternativa estática.

---

## 4. Top 5 quick wins (≈ 1 jornada en total)

1. **W1 + W2 + W7**: entradas suaves, puntos de "escribiendo" y pasos que pulsan (web de pedidos). CSS casi puro.
2. **P3**: barra de urgencia en las tarjetas del panel; se lee de lejos y apenas lleva código.
3. **P6 + R2**: pantalla encendida (`wakeLock`) y globo en el icono. Mejora real en el local y en la ruta con pocas líneas.
4. **R3 + P4**: modo oscuro en el repartidor y el panel, reutilizando los tokens que ya tiene la web de pedidos.
5. **R5**: píldora de conexión en el repartidor, para que nadie se fíe de datos viejos.

Después, lo de mayor impacto y mayor esfuerzo: **W5/W6** (menú con fotos y mini-carrito), **P1** (tarjetas que vuelan entre columnas) y **R1** (deslizar para confirmar).

## 5. Orden sugerido y dependencias
1. Quick wins 1–5 (sin tocar la API).
2. Fuente propia (W8): requiere que `web.py` y `panel_web.py` sirvan un archivo estático nuevo.
3. Fotos de platos (W5): requiere campo `foto` en `/menu` y subida desde "Ajustes" (programación).
4. P1 + R4 (View Transitions): refactor del repintado del panel (programación revisa).

Conviene medir antes y después con Lighthouse móvil (perfil "Slow 4G", CPU ×4) y revisar a 375 px.
