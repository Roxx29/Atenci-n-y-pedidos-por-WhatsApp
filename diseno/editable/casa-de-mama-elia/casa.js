/* Casa de Mamá Elia: solo adornos (parallax, aparición al desplazar, vuelo al carrito, olla de «Pedir»). No toca la API ni los datos. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const quieto = matchMedia("(prefers-reduced-motion:reduce)").matches;
  const menu = $("#menu"), hero = $("#hero"), lista = $("#lista-menu"), barraCarro = $("#carro");
  const ESPERA = ms => new Promise(ok => setTimeout(ok, ms));

  // Botones del hero
  $("#ir-menu").onclick = () => lista.scrollIntoView({behavior: quieto ? "auto" : "smooth", block: "start"});

  /* ===== Agregar plato: vuela al carrito, el carrito rebota y la casita cae como sello ===== */
  let volando = 0;
  const centro = r => ({x: r.left + r.width / 2, y: r.top + r.height / 2});
  const sello = card => {
    const s = document.createElement("i"), o = document.createElement("i");
    s.className = "sello" + (quieto ? " suave" : ""); o.className = "onda"; s.setAttribute("aria-hidden", "true"); o.setAttribute("aria-hidden", "true");
    s.onanimationend = () => s.remove(); o.onanimationend = () => o.remove();
    card.append(s); if (!quieto) card.append(o);
  };
  const rebota = () => { barraCarro.classList.remove("bump"); void barraCarro.offsetWidth; barraCarro.classList.add("bump"); };
  async function volar(card) { if (!quieto) rebota(); sello(card); } // sin vuelo al carrito: solo sello y rebote
  // En captura: antes de que el botón cambie la tarjeta se anota de dónde sale el plato
  lista.addEventListener("click", e => {
    const b = e.target.closest(".pedir,.paso button"); if (!b) return;
    const card = b.closest(".plato"), paso = card.querySelector(".paso");
    if (b.textContent.trim() === "−") { requestAnimationFrame(() => paso && !paso.hidden && pop(paso)); return; }
    const foto = card.querySelector(".foto"), r = (foto || card).getBoundingClientRect();
    const origen = foto ? r : {left: r.left + 16, top: r.top + r.height / 2 - 28, width: 56, height: 56};
    volando++;
    requestAnimationFrame(() => { // ya corrió el onclick del plato: el carrito existe y tiene el total nuevo
      paso && !paso.hidden && pop(paso);
      volar(card).finally(() => { volando--; });
    });
  }, true);
  const pop = p => { p.classList.remove("pop"); void p.offsetWidth; p.classList.add("pop"); };

  /* ===== Pedir: olla humeante, los ingredientes vuelan a ella y se desvanece mostrando el chat ===== */
  const ING = ["g-hoja", "g-aji", "g-maiz", "g-cilantro", "g-patacon"];
  let activo = false;
  const origenes = () => { // de los platos del carrito; si no hay, de los ingredientes del hero; si no, de los bordes
    const vw = innerWidth, vh = innerHeight, dentro = r => r.width && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    const flota = [...document.querySelectorAll(".flota")].map(f => ({r: f.getBoundingClientRect()})).filter(o => dentro(o.r));
    if (flota.length >= 3) return flota;
    return [[-30, .2], [1.04, .3], [.15, -.05], [.85, 1.05], [-30, .75]].map(([x, y]) => ({r: new DOMRect(x > 1 || x < 0 ? (x < 0 ? x : vw + 30) : x * vw, y * vh, 34, 34)}));
  };
  async function pedir(orig) {
    activo = true;
    await new Promise(requestAnimationFrame); // el clic ya cambió de pestaña
    const panel = ["#chat", "#entrada", "#estado"].map($).find(p => p && !p.hidden), pr = panel ? panel.getBoundingClientRect() : {left: 0, top: 0, width: innerWidth, height: innerHeight};
    const px = Math.max(90, Math.min(innerWidth - 90, pr.left + pr.width / 2)), py = Math.max(150, Math.min(innerHeight - 120, innerHeight * .45));
    const velo = document.createElement("div"); velo.className = "velo"; velo.setAttribute("aria-hidden", "true");
    velo.style.setProperty("--px", px + "px"); velo.style.setProperty("--py", py + "px");
    const pote = document.createElement("div"); pote.className = "pote"; pote.style.setProperty("--px", px + "px"); pote.style.setProperty("--py", py + "px");
    const olla = hero.querySelector(".olla").cloneNode(true); olla.removeAttribute("style");
    const caldo = document.createElementNS("http://www.w3.org/2000/svg", "path"); caldo.setAttribute("class", "caldo"); caldo.setAttribute("d", "M13 37h74v19a20 20 0 0 1-20 20H33A20 20 0 0 1 13 56z");
    olla.querySelector(".cuerpo").after(caldo); pote.append(olla); velo.append(pote); document.body.append(velo);
    const fin = () => velo.remove();
    if (quieto) { await velo.animate({opacity: [0, 1, 1, 0], offset: [0, .25, .7, 1]}, {duration: 500}).finished.catch(() => {}); fin(); activo = false; return; }
    velo.animate({opacity: [0, 1, 1, 0], offset: [0, .1, .78, 1]}, {duration: 1400, fill: "forwards"}).finished.then(fin, fin);
    pote.animate([{transform: "scale(.4)"}, {transform: "scale(1.06)", offset: .14}, {transform: "scale(1)", offset: .3}, {transform: "scale(1.16,.9)", offset: .66}, {transform: "scale(.96,1.06)", offset: .8}, {transform: "scale(1)"}], {duration: 1400, easing: "ease-out"});
    caldo.animate({clipPath: ["inset(100% 0 0 0)", "inset(8% 0 0 0)"]}, {duration: 900, delay: 250, fill: "forwards", easing: "ease-in"});
    const vuelos = ING.map((id, i) => {
      const o = orig[i % orig.length], a = centro(o.r), dx = px - a.x, dy = py - 20 - a.y;
      let el; if (o.foto && i < orig.length) { el = o.foto.cloneNode(); el.className = "vuela"; el.style.width = el.style.height = "40px"; el.removeAttribute("loading"); }
      else { el = document.createElementNS("http://www.w3.org/2000/svg", "svg"); el.setAttribute("class", "ing-v"); const u = document.createElementNS("http://www.w3.org/2000/svg", "use"); u.setAttribute("href", "#" + id); el.append(u); }
      el.setAttribute("aria-hidden", "true"); el.style.left = a.x - 17 + "px"; el.style.top = a.y - 17 + "px"; velo.append(el);
      return el.animate([
        {transform: "translate(0,0) scale(1) rotate(0)", opacity: 0},
        {transform: `translate(${dx * .4}px,${dy * .4 - 70}px) scale(1.25) rotate(120deg)`, opacity: 1, offset: .4},
        {transform: `translate(${dx}px,${dy}px) scale(.45) rotate(300deg)`, opacity: .9}
      ], {duration: 600, delay: 120 + i * 60, easing: "cubic-bezier(.5,0,.3,1)", fill: "both"}).finished.then(() => { el.style.visibility = "hidden"; });
    });
    await Promise.all(vuelos).catch(() => {});
    await ESPERA(620); activo = false; // el overlay termina solo a los 1.4 s; no bloquea nada (pointer-events:none)
  }
  document.addEventListener("click", e => {
    const t = e.target.closest("#tab-chat,#ir-chat,#carro-enviar"); if (!t || activo) return;
    if (t.id === "tab-chat" && t.getAttribute("aria-selected") === "true") return; // ya está en el chat
    if (t.id === "carro-enviar" && barraCarro.hidden) return;
    pedir(origenes());
  }, true);

  // Cabecera plegable en móvil: #app[data-vista] = "menu" u "otra" según la pestaña activa (el CSS pliega la barra)
  const app = $("#app"), tabM = $("#tab-menu");
  const vista = () => { app.dataset.vista = tabM.getAttribute("aria-selected") === "true" ? "menu" : "otra"; };
  new MutationObserver(vista).observe(tabM, {attributes: true, attributeFilter: ["aria-selected"]}); vista();

  if (quieto) return; // sin movimiento: todo lo de abajo (parallax, aparición) se omite

  // Parallax suave: el desplazamiento del menú y el puntero mueven los ingredientes
  let raf = 0;
  const frame = () => { raf = 0; hero.style.setProperty("--sy", Math.min(menu.scrollTop, 600) + "px"); };
  menu.addEventListener("scroll", () => { raf = raf || requestAnimationFrame(frame); }, {passive: true});
  hero.addEventListener("pointermove", e => {
    if (e.pointerType !== "mouse") return;
    const r = hero.getBoundingClientRect();
    hero.style.setProperty("--mx", ((e.clientX - r.left) / r.width - .5).toFixed(3));
    hero.style.setProperty("--my", ((e.clientY - r.top) / r.height - .5).toFixed(3));
  }, {passive: true});

  // Aparición al desplazar: títulos y platos (se repintan al filtrar, por eso se observa la lista)
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    io.unobserve(e.target); e.target.classList.add("en");
    setTimeout(() => e.target.classList.remove("rv", "en"), 1400); // luego el hover manda
  }), {threshold: .1, rootMargin: "0px 0px -4% 0px"});
  const preparar = () => {
    const sin = lista.classList.contains("quieto"); let n = 0;
    lista.querySelectorAll(".plato:not(.rv),h2:not(.rv)").forEach(el => {
      if (el.dataset.visto) return; el.dataset.visto = "1";
      if (sin) return; // escribiendo en el buscador: sin entrada escalonada
      el.style.setProperty("--i", n++ % 6); el.classList.add("rv"); io.observe(el);
    });
  };
  new MutationObserver(preparar).observe(lista, {childList: true, subtree: true});
  preparar();

  // El carrito "salta" al cambiar el total, salvo cuando acaba de volar un plato hacia él (rebota al llegar)
  new MutationObserver(() => { if (!volando) rebota(); }).observe($("#carro-total"), {childList: true, characterData: true, subtree: true});
})();
