# Páginas más interactivas y premium (web de pedidos, panel, PWA repartidor)

Fecha: 2026-11-03. Encargo del capitán para agentediseno-a8.
Leyenda: **[NV]** = no verificado en fuente. `*` = cifra de memoria o fuente secundaria.
Aviso: los referentes salieron de blogs, casos de estudio y portafolios. "Se siente premium" es interpretación, no dato.

## 1. Referentes

### Pedidos (cliente)
| Referente | Qué hace bien | Fuente |
|---|---|---|
| Sweetgreen | Pocas pestañas, home con "repetir pedido", casi sin iconos (foto e ilustración mandan). Muestra lo elegido al personalizar y avisa cuando sube el precio. | [UX Collective](https://uxdesign.cc/the-ux-of-sweetgreen-68696b16a66), [Gin Lane](https://surfthenet.ginlane.com/work/sweetgreen/) |
| Chipotle | Elegir ingredientes paso a paso, como la línea de ensamblado. Rewards con barra de progreso al siguiente premio. Deloitte reporta pedidos móviles +50% (cifra de la agencia). | [Deloitte](https://www.deloittedigital.com/ce/en/work/chipotle.html), [CX Dive](https://www.customerexperiencedive.com/news/chipotles-loyalty-relaunch-customer-behavior/817862/) |
| Domino's | Pizza Tracker: progreso por etapas que reduce la incertidumbre (fuentes secundarias). | [Medium](https://medium.com/@The.Nikki.Chronicles/dominos-pizza-tracker-turning-frowns-into-funds-with-ux-aad760b75c1a) |
| Uber Eats | Consistencia por sistema de diseño (Base) en todos los productos. Evaluado por Baymard. | [Baymard](https://baymard.com/ux-benchmark/case-studies/uber-eats), [Uber](https://www.uber.com/us/en/blog/design-system-at-scale/) |
| Wolt | Limpio, simple, intuitivo (reseñas). | [Dribbble Wolt](https://wolt.dribbble.com) |
| Glovo | Marca fuerte y flujo corto; el menú inicial confunde por tantos servicios (reseña de 2020, una persona). | [Medium](https://gezwastell.medium.com/glovo-vs-deliveroo-e08969c19f78) |

Sin fuente: Rappi, PedidosYa, Toast Online Ordering **[NV]**.
Dato útil: en app móvil se percibe pedir más rápido; el menú se ve mejor en escritorio ([MeasuringU](https://measuringu.com/?p=508)).

### Cocina (KDS)
- **Toast KDS:** enruta por estación y expeditor; temporizador de ticket con color de alerta configurable; borde de color por tipo de pedido; ticket en verde cuando todo está listo; RUSH con bump bar. [Toast](https://pos.toasttab.com/hardware/kitchen-display-system), [doc bump bar](https://doc.toasttab.com/doc/platformguide/platformKitchenUsingBumpBars.html)
- **Square KDS:** minutos configurables para alerta amarilla y roja; cabecera verde→amarillo→rojo (guía de terceros); modificadores con color propio. [Square](https://squareup.com/help/us/en/article/7944-get-started-with-square-kds-android), [Fresh](https://www.fresh.technology/blog/a-guide-to-kitchen-display-systems-for-square)
- Fresh KDS: sin fuente útil **[NV]**.

### Repartidor
- **DoorDash Dasher:** rediseño ago-2025 para ser más rápido y simple (home, barra lateral, notificaciones). Un caso de estudio critica el mapa saturado de banners y propone control de mapa y probar modo oscuro. [DoorDash](https://dasher.doordash.com/en-us/blog/new-doordash-dasher-app), [Medium](https://medium.com/@a_kill_/doordash-visions-redesigning-the-dasher-apps-homescreen-f40d1b09ba8c)
- **Uber Driver:** botón grande "Go" para conectarse; confirmar deslizando en el flujo del viaje (un foro reporta fallos). [Uber](https://www.uber.com/us/en/deliver/driver-app/)
- Concepto CarPlay: se mira el teléfono ~9 veces por entrega, así que poco y en grande. [Portafolio](https://www.elizagonzales.com/projects/doordash-x-carplay)
- Rappi Repartidor: sin fuente **[NV]**.

### Ideas aplicables
Repetir pedido en el home; barra de progreso/etapas del pedido; temporizador con color verde→amarillo→rojo en el panel; borde de color por tipo (local/llevar/domicilio); botones grandes en repartidor.

## 2. Técnicas web sin build

Verificado en caniuse: Vibration, View Transitions multipágina, scroll-driven (esta con fuente secundaria). El resto `*`.

| Técnica | Global | Android Chrome / iOS Safari | Fallback |
|---|---|---|---|
| View Transitions (mismo documento) | ~90%* | Sí / 18+ | `if(!document.startViewTransition)` y cambiar sin transición |
| View Transitions multipágina | 88.8% | Sí / 18.2+ | Se ignora |
| Scroll-driven animations | ~82-85%* | Sí / 26+ | `@supports (animation-timeline: scroll())` |
| `@starting-style` | ~90%* | Sí / 17.5+ | Sin animación de entrada |
| popover | ~93%* | Sí / 17+ | `<dialog>` |
| `<dialog>` | ~97%* | Sí / 15.4+ | No hace falta |
| Container queries | ~93%* | Sí / 16+ | Media queries |
| `:has()` | ~93%* | Sí / 15.4+ | Mejora opcional |
| Vibration API | 77.2% | Sí / **No** | `navigator.vibrate?.()`; en iOS sonido o aviso visual |
| Web Share | ~90%* | Sí / Sí | Copiar enlace o `wa.me` |
| Screen Wake Lock | ~88%* | Sí / 16.4+ | Re-pedir en `visibilitychange` |
| Web Push en PWA iOS | n/d | Sí / 16.4+ solo con PWA instalada | WhatsApp o sonido en página abierta |
| color-mix / oklch | ~92%* | Sí / 16.2+ | Hex previo |
| scroll-snap, prefers-reduced-motion | ~97%* | Sí / Sí | — |
| dvh | ~93%* | Sí / 15.4+ | `height:100vh; height:100dvh;` |
| Badging API | ~75%* | Parcial / solo PWA instalada | Contador en el título |

Para el repartidor en iPhone: Vibration no existe, usar sonido y aviso visual. Wake Lock, Badging y Push exigen PWA instalada.
Fuentes: [Vibration](https://caniuse.com/vibration), [View Transitions MPA](https://caniuse.com/cross-document-view-transitions), [scroll-driven](https://caniuse.com/wf-scroll-driven-animations). Verificar el resto en caniuse antes de depender de ellos.

## 3. Librerías por CDN (min+gzip)

| Librería | Tamaño | Licencia | Veredicto |
|---|---|---|---|
| Alpine.js | ~16.7 kB | MIT | Solo si el estado crece (carrito, modales) |
| htmx | ~14 kB* | BSD-2 | Solo si el servidor devuelve HTML; si no, `fetch` |
| petite-vue | ~7.1 kB | MIT | Congelado desde 2022; mejor Alpine |
| canvas-confetti | ~4 kB* | ISC | Opcional, "pedido completado" |
| Motion (`animate` mini) | 2.3 kB (proveedor) | MIT | Evitable con View Transitions y `@starting-style` |
| SortableJS | ~15 kB* | MIT | Solo si la cola de cocina necesita arrastrar |
| GSAP Flip | ~25 kB* | Propia (hoy gratis) | Evitable con View Transitions |

**Recomendación:** base sin librerías (CSS nativo + `fetch`). Fuentes: [sprae](https://sprae.js.org/compare), [Motion](https://motion.dev/docs/react-reduce-bundle-size).

## 4. Rendimiento en móviles baratos y 3G (Panamá)

**Contexto:** mediana móvil de bajada ~32 Mbps (Ookla vía [DataReportal](https://datareportal.com/reports/digital-2026-panama)); es mediana, en zonas rurales o saturadas baja. LatAm Q1 2026: Samsung 37%, Xiaomi 17%, Motorola 14%, gama baja y entrada ~70% ([PhoneArena](https://www.phonearena.com/news/latin-american-smartphone-market-report-q1-2026-samsung-untouchable-apple-irrelevant_id180565)). RAM por modelo: no hay dato; asumir 2-4 GB y CPU lenta **[NV]**.

**Evitar → alternativa**
- `backdrop-filter`/blur → fondo sólido o rgba.
- Sombras grandes (blur >10px) o animadas → sombra pequeña o borde; animar opacidad de un pseudo-elemento.
- Animar width/height/top/left/margin → solo `transform` y `opacity`.
- Fotos pesadas → WebP/AVIF a ~2x del tamaño mostrado.
- Fuentes web → `system-ui`; si hace falta, WOFF2 de un peso.
- Frameworks y JS grande → vanilla.
- Listas largas → paginar o `content-visibility: auto` + `contain-intrinsic-size`.

**Presupuesto** (referencia Moto G4, 3G lenta): interactiva en <5 s ([web.dev](https://web.dev/articles/performance-budgets-101)); JS <170 KB según web.dev ([Russell](https://infrequently.org/2017/10/can-you-afford-it-real-world-web-performance-budgets) da 130-170 KB en ruta crítica). Recomendación propia: JS+CSS <100 KB gzip, imagen héroe ≤60 KB, imágenes de primera pantalla ≤150 KB. Core Web Vitals "bueno": LCP ≤2,5 s, INP ≤200 ms, CLS ≤0,1 (de memoria, [web.dev](https://web.dev/articles/vitals)).

**Tácticas:** service worker (precache del shell, stale-while-revalidate para el menú, caché-primero para imágenes); `width/height` fijos y `loading="lazy"` fuera de pantalla, `fetchpriority="high"` en la imagen LCP; `font-display: swap`; `will-change` solo mientras se anima; `prefers-reduced-motion`; `navigator.connection?.saveData` (solo Chromium); cabeceras de caché largas con `?v=`; probar en Lighthouse móvil con CPU 4x y 3G lenta.

## Huecos
Rappi, PedidosYa, Fresh KDS y Toast Online Ordering sin fuente. Falta revisar Mobbin/Awwwards/Dribbble por app y la animación de "deslizar para confirmar". Varias cifras de soporte son de memoria. Apuntado en `IDEAS.md`.
