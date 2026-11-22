/* Urban Sazón: solo decoración (lluvia de comida, vapor, caja que se llena). No toca la API ni la lógica del pedido. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const quieto = matchMedia("(prefers-reduced-motion:reduce)").matches;
  const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" aria-hidden="true"';
  const ICO = {
    papas: '<rect x="13" y="6" width="5" height="20" rx="2" fill="#ffc21a" transform="rotate(-10 15 16)"/><rect x="20" y="3" width="5" height="22" rx="2" fill="#ffd54a"/><rect x="27" y="6" width="5" height="20" rx="2" fill="#ffc21a" transform="rotate(10 29 16)"/><rect x="33" y="10" width="5" height="16" rx="2" fill="#ffd54a" transform="rotate(22 35 18)"/><path d="M11 22h26l-3 22H14z" fill="#e63329"/><path d="M12 28h24l-.6 4H12.6z" fill="#ffc21a"/>',
    pollo: '<path d="M31 5c8 0 13 6 11 13-2 6-9 9-15 6l-8 8-5-5 8-8c-2-6 1-14 9-14z" fill="#e8872a"/><path d="M33 9c4 0 6 3 5 6" stroke="#ffc978" stroke-width="3" fill="none" stroke-linecap="round"/><circle cx="13" cy="36" r="4" fill="#fff3d6"/><circle cx="9" cy="40" r="4" fill="#fff3d6"/>',
    hamb: '<path d="M6 24a18 15 0 0 1 36 0z" fill="#e8872a"/><circle cx="16" cy="15" r="1.4" fill="#ffe7b3"/><circle cx="24" cy="12" r="1.4" fill="#ffe7b3"/><circle cx="31" cy="16" r="1.4" fill="#ffe7b3"/><path d="M5 26h38l-3 3-4-2-4 2-4-2-4 2-4-2-4 2-4-2-4 2z" fill="#7cc243"/><path d="M7 29h34l-2 3H9z" fill="#ffc21a"/><rect x="6" y="31" width="36" height="6" rx="3" fill="#5a2d17"/><path d="M6 38h36a4 4 0 0 1-4 6H10a4 4 0 0 1-4-6z" fill="#e8872a"/>',
    aro: '<circle cx="24" cy="24" r="17" fill="none" stroke="#e8872a" stroke-width="8"/><circle cx="24" cy="24" r="17" fill="none" stroke="#ffc978" stroke-width="2.5" stroke-dasharray="14 12" stroke-linecap="round"/><circle cx="24" cy="24" r="12.5" fill="none" stroke="#b85a14" stroke-width="1.5"/>',
    chile: '<path d="M30 8c1-3 4-4 7-3-1 3-3 5-5 5z" fill="#3a8f2e"/><path d="M28 10c10 2 14 12 10 22-3 8-12 12-24 8-3-1-3-4 0-5 8-2 12-6 12-14 0-5 0-8 2-11z" fill="#e02a1b"/><path d="M33 16c3 5 3 10 0 15" stroke="#ff7a6a" stroke-width="2.5" fill="none" stroke-linecap="round"/>',
    bebida: '<path d="M26 10l4-8 3 1-3 7z" fill="#ffc21a"/><path d="M13 15h22l-3 29H16z" fill="#ff5a1f"/><rect x="11" y="10" width="26" height="6" rx="3" fill="#fff"/><path d="M15 26h18l-.8 6H15.8z" fill="#fff" opacity=".9"/>'
  };
  const cajaSvg = () => `<svg class="u-caja" ${NS}><path d="M8 20h32l-2 22H10z" fill="#ff7a1a"/><path d="M8 20h32l-1 6H9z" fill="#e8590c"/><rect x="21" y="9" width="4" height="14" rx="2" fill="#ffc21a" transform="rotate(-8 23 16)"/><rect x="27" y="7" width="4" height="16" rx="2" fill="#ffd54a"/><circle cx="17" cy="17" r="5" fill="#e8872a"/><g class="tapa"><path d="M6 14h36v6H6z" fill="#ff9a3d"/><path d="M6 14h36v2H6z" fill="#ffc978"/></g></svg>`;
  const svg = n => `<svg ${NS}>${ICO[n]}</svg>`;
  const para = t => /alita|pollo|nugget|tender|broaster|wing/i.test(t) ? "pollo" : /papa|fries/i.test(t) ? "papas" : /hamb|burger|sandwich|perro|hot ?dog/i.test(t) ? "hamb" : /soda|jugo|limon|refresco|bebida|batido|t[eé]\b|caf[eé]/i.test(t) ? "bebida" : /combo/i.test(t) ? "papas" : "pollo";

  // Lluvia de comida: n iconos que caen dentro de `caja`
  function lluvia(caja, n, nombres) {
    if (quieto || !caja) return;
    const c = document.createElement("div"); c.className = "u-lluvia"; c.setAttribute("aria-hidden", "true");
    for (let i = 0; i < n; i++) {
      const s = document.createElement("span"); s.className = "u-cae";
      s.style.cssText = `--x:${Math.round(Math.random() * 94)}%;--s:${16 + Math.round(Math.random() * 16)}px;--t:${(5 + Math.random() * 5).toFixed(1)}s;--d:${(-Math.random() * 9).toFixed(1)}s;--r:${Math.round(Math.random() * 540 - 270)}deg`;
      s.innerHTML = svg(nombres[i % nombres.length]); c.append(s);
    }
    caja.prepend(c);
    const medir = () => c.style.setProperty("--alto", caja.clientHeight + 60 + "px");
    medir(); addEventListener("resize", medir);
  }

  // Cabecera: lluvia + cinta que corre
  const barra = $(".barra");
  if (barra) {
    lluvia(barra, 9, ["papas", "pollo", "hamb", "bebida"]);
    const cinta = document.createElement("div"); cinta.className = "u-cinta"; cinta.setAttribute("aria-hidden", "true");
    const fila = ["Alitas", "Papas", "Hamburguesas", "Combos", "Sazón de calle"].map(t => `<span>${t}</span>`).join("");
    cinta.innerHTML = `<div>${fila}${fila}${fila}${fila}</div>`; barra.append(cinta);
  }

  // Entrada: logo grande con vapor + lluvia suave
  const entrada = $("#entrada");
  if (entrada) {
    const h = document.createElement("div"); h.className = "u-hero";
    h.innerHTML = `<div class="u-logo-g"><svg class="u-vapor" viewBox="0 0 90 60" aria-hidden="true"><path d="M25 58c-10-12 10-16 0-28s8-18 0-28"/><path d="M45 58c-10-12 10-16 0-28s8-18 0-28"/><path d="M65 58c-10-12 10-16 0-28s8-18 0-28"/></svg><img src="/web/logo.png" alt="Urban Sazón" width="150" height="150"></div><h2>Pide fácil, come rico</h2><p>Alitas, papas y mucha sazón</p>`;
    entrada.prepend(h);
    lluvia(entrada, 8, ["papas", "pollo", "hamb"]);
  }

  // Platos sin foto: icono de comida según el nombre
  const lista = $("#lista-menu");
  if (lista) {
    const poner = () => lista.querySelectorAll(".plato:not([data-u])").forEach(p => {
      p.dataset.u = "1";
      if (p.querySelector(".foto")) return;
      const d = document.createElement("div"); d.className = "u-ico"; d.innerHTML = svg(para(p.querySelector("h3")?.textContent || "")); p.append(d);
    });
    new MutationObserver(poner).observe(lista, {childList: true}); poner();
  }

  // Carrito: caja de comida; al agregar, el plato vuela hasta ella y la caja rebota
  const carro = $("#carro");
  if (carro) carro.prepend(Object.assign(document.createElement("span"), {innerHTML: cajaSvg()}).firstChild);
  const golpe = () => { const c = $(".u-caja"); if (!c) return; c.classList.remove("golpe"); void c.getBoundingClientRect(); c.classList.add("golpe"); };
  lista?.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b || !(b.classList.contains("pedir") || /^Agregar uno/.test(b.getAttribute("aria-label") || ""))) return;
    const caja = $(".u-caja"); if (!caja || quieto) return golpe();
    const a = b.getBoundingClientRect(), z = caja.getBoundingClientRect();
    const v = document.createElement("div"); v.className = "u-vuelo"; v.innerHTML = svg(para(b.closest(".plato")?.querySelector("h3")?.textContent || "")); document.body.append(v);
    const dx = z.left + z.width / 2 - 20 - (a.left + a.width / 2 - 20), dy = z.top + z.height / 2 - 20 - (a.top + a.height / 2 - 20);
    const x0 = a.left + a.width / 2 - 20, y0 = a.top + a.height / 2 - 20;
    v.animate([
      {transform: `translate(${x0}px,${y0}px) scale(1) rotate(0)`},
      {transform: `translate(${x0 + dx * .5}px,${y0 + Math.min(dy, 0) - 70}px) scale(1.15) rotate(160deg)`, offset: .45},
      {transform: `translate(${x0 + dx}px,${y0 + dy}px) scale(.4) rotate(360deg)`, opacity: .9}
    ], {duration: 650, easing: "cubic-bezier(.5,0,.75,.4)"}).onfinish = () => { v.remove(); golpe(); };
  });
  // «Pedir»: caja de comida que se arma a pantalla completa (~1.8 s). Corre aparte del onclick de index.html.
  document.addEventListener("click", e => {
    if (!e.target.closest("#carro-enviar") || $("#carro")?.hidden || $(".u-armar")) return; // captura: el carro aún no se vació
    const o = document.createElement("div"); o.className = "u-armar"; o.setAttribute("role", "status"); o.setAttribute("aria-live", "polite");
    const lid = (c, d) => `<path class="${c}" d="${d}" fill="#ff9a3d"/>`;
    o.innerHTML = quieto ? `<p>¡Armando tu pedido!</p>` : `<div class="u-estuche"><div class="u-pop"><div class="u-flash"></div>
      <svg class="u-atras" viewBox="0 0 160 120" aria-hidden="true"><path d="M20 50h120l-10 62H30z" fill="#7a2f06"/></svg>
      ${["papas", "pollo", "hamb"].map(n => `<span class="u-it">${svg(n)}</span>`).join("")}
      <svg class="u-frente" viewBox="0 0 160 120" aria-hidden="true"><path d="M20 50h120l-10 62H30z" fill="#ff7a1a"/><path d="M20 50h120l-2 12H22z" fill="#e8590c"/><path class="u-rayo" d="M84 62 70 88h9l-4 20 20-30h-9l7-16z" fill="#ffd54a" stroke="#140c06" stroke-width="2" stroke-linejoin="round"/>
      ${lid("u-ti", "M16 43h66v9H16z")}${lid("u-td", "M78 43h66v9H78z")}</svg></div><p>¡Armando tu pedido!</p></div>`;
    document.body.append(o);
    setTimeout(() => o.remove(), quieto ? 800 : 1900);
  }, true);

  // ---- Ideas 1-5: hamburguesa, lluvia al confirmar, skeleton, total que cuenta, categorías que siguen el scroll ----
  // Capas de abajo arriba: pan, carne, queso, lechuga, tomate, pan con semillas
  const CAPAS = ['<path d="M7 38h34a3 3 0 0 1-3 5H10a3 3 0 0 1-3-5z" fill="#e8872a"/>',
    '<rect x="6" y="32" width="36" height="6.5" rx="3.2" fill="#5a2d17"/>',
    '<path d="M6 29h36l-3 5-5-3.5-5 3.5-5-3.5-5 3.5-5-3.5-3 3z" fill="#ffc21a"/>',
    '<path d="M5 25.5h38l-2 3.5-4-2.2-4 2.2-4-2.2-4 2.2-4-2.2-4 2.2-4-2.2-4 2.2z" fill="#7cc243"/>',
    '<rect x="8" y="21" width="32" height="5" rx="2.5" fill="#e63329"/>',
    '<path d="M6 21a18 15 0 0 1 36 0z" fill="#e8872a"/><circle cx="16" cy="15" r="1.4" fill="#ffe7b3"/><circle cx="24" cy="11.5" r="1.4" fill="#ffe7b3"/><circle cx="31" cy="15.5" r="1.4" fill="#ffe7b3"/><circle cx="21" cy="17.5" r="1.2" fill="#ffe7b3"/>'];
  const hamb = cls => `<svg class="${cls}" ${NS}>${CAPAS.map((c, i) => `<g class="c" style="--i:${i}">${c}</g>`).join("")}</svg>`;

  // 1a. Cabecera: la hamburguesa cae capa a capa junto al logo
  const logoB = $(".barra .logo");
  if (logoB) { const h = document.createElement("span"); h.className = "u-ham"; h.setAttribute("aria-hidden", "true"); h.innerHTML = hamb("u-ham-s"); logoB.after(h); }

  // 1b. Mi pedido: se completa según el paso actual (lee #pasos, no toca la lógica)
  const pasos = $("#pasos");
  if (pasos) {
    const pam = document.createElement("div"); pam.className = "u-pam"; pam.innerHTML = hamb("u-pam-s") + `<p></p>`; pasos.before(pam);
    const cs = pam.querySelectorAll(".c"), txt = pam.querySelector("p");
    pam.setAttribute("role", "img");
    const verPasos = () => {
      const li = [...pasos.children], n = li.length, i = li.findIndex(x => x.classList.contains("actual"));
      const k = !n ? 0 : i >= 0 ? Math.ceil(6 * (i + 1) / n) : li.every(x => x.classList.contains("hecho")) ? 6 : 0;
      cs.forEach((c, j) => c.classList.toggle("on", j < k));
      pam.setAttribute("aria-label", `Tu hamburguesa: ${k} de 6 capas`);
      txt.textContent = k >= 6 ? "¡Tu hamburguesa está completa!" : `Armando tu hamburguesa: ${k} de 6`;
    };
    new MutationObserver(verPasos).observe(pasos, {childList: true, subtree: true, attributes: true, attributeFilter: ["class"]}); verPasos();
  }

  // 2. Lluvia a pantalla completa al pasar a Confirmado / Pagado (una vez por pedido y estado; no al recargar)
  const visto = {}, rainDe = {Confirmado: 1, Pagado: 1};
  const llueve = () => {
    if (quieto || $(".u-llu")) return;
    const c = document.createElement("div"); c.className = "u-llu"; c.setAttribute("aria-hidden", "true");
    const ns = ["papas", "aro", "chile"];
    for (let i = 0; i < 36; i++) {
      const s = document.createElement("span");
      s.style.cssText = `--x:${Math.round(Math.random() * 96)}%;--s:${26 + Math.round(Math.random() * 20)}px;--d:${(Math.random() * .9).toFixed(2)}s;--r:${Math.round(Math.random() * 480 - 240)}deg`;
      s.innerHTML = svg(ns[i % 3]); c.append(s);
    }
    document.body.append(c); setTimeout(() => c.remove(), 3500);
  };
  const rs = $("#resumen");
  if (rs) new MutationObserver(() => {
    const id = $("#r-id")?.textContent, e = $("#r-estado")?.textContent; if (!id || !e) return;
    const antes = visto[id]; visto[id] = e;
    if (antes !== undefined && antes !== e && rainDe[e]) llueve();
  }).observe(rs, {childList: true, characterData: true, subtree: true});

  // 3. Skeleton con brillo mientras carga el menú (pintarMenu vacía la lista y lo quita)
  if (lista && !lista.children.length) {
    const sk = document.createElement("div"); sk.className = "u-sk"; sk.setAttribute("aria-hidden", "true");
    sk.innerHTML = '<i class="t"></i>' + '<div class="f"><div><i></i><i></i><i></i></div><b></b></div>'.repeat(4);
    lista.append(sk); setTimeout(() => sk.remove(), 10000);
  }

  // 4. Total que cuenta: #carro-total (real, para lectores) no se toca; una copia visible cuenta hasta el valor exacto
  const tot = $("#carro-total");
  if (tot) {
    const vis = document.createElement("span"); vis.className = "u-cuenta"; vis.setAttribute("aria-hidden", "true"); tot.after(vis); tot.classList.add("u-real");
    let ahora = 0, raf = 0, fuerza = 0;
    const leer = t => parseFloat((t.match(/\d+(?:\.\d+)?/) || [0])[0]);
    const poner = () => {
      const fin = tot.textContent, a = ahora, b = leer(fin); cancelAnimationFrame(raf); clearTimeout(fuerza);
      if (quieto || !isFinite(b) || a === b) { vis.textContent = fin; ahora = b; return; }
      const t0 = performance.now(), dur = 450;
      const paso = t => {
        const k = Math.min(1, (t - t0) / dur); ahora = a + (b - a) * (1 - Math.pow(1 - k, 3));
        if (k < 1) { vis.textContent = "B/. " + ahora.toFixed(2); raf = requestAnimationFrame(paso); } else { ahora = b; vis.textContent = fin; }
      };
      raf = requestAnimationFrame(paso); fuerza = setTimeout(() => { cancelAnimationFrame(raf); ahora = b; vis.textContent = fin; }, dur + 80); // pestaña oculta: rAF se pausa, el valor final no puede quedar a medias
      vis.classList.remove("sube"); void vis.offsetWidth; vis.classList.add("sube");
    };
    new MutationObserver(poner).observe(tot, {childList: true, characterData: true, subtree: true}); poner();
  }

  // 5. Categorías que siguen el scroll: chip con clase propia (no toca aria-pressed)
  const cats = $("#cats");
  if (lista && cats && "IntersectionObserver" in window) {
    let activa = "";
    const marca = () => {
      let chip = null;
      cats.querySelectorAll("button").forEach(b => { const on = !!activa && b.textContent.toLowerCase() === activa; b.classList.toggle("u-sigue", on); if (on) chip = b; });
      if (chip && cats.scrollWidth > cats.clientWidth) cats.scrollTo({left: Math.max(0, chip.offsetLeft - (cats.clientWidth - chip.offsetWidth) / 2), behavior: quieto ? "auto" : "smooth"});
    };
    // activa = último h2 cuyo borde superior ya pasó la línea (35% de la pantalla); el observer solo avisa de cruces
    const io = new IntersectionObserver(() => {
      const hs = [...lista.querySelectorAll("h2")], lim = innerHeight * .35; let h = null;
      for (const x of hs) if (x.getBoundingClientRect().top <= lim) h = x;
      const n = (h || hs[0])?.textContent.toLowerCase() || ""; if (n !== activa) { activa = n; marca(); }
    }, {rootMargin: "0px 0px -65% 0px"});
    const ver = () => { io.disconnect(); lista.querySelectorAll("h2").forEach(h => io.observe(h)); marca(); };
    new MutationObserver(ver).observe(lista, {childList: true});
    new MutationObserver(marca).observe(cats, {childList: true}); ver();
  }
})();
