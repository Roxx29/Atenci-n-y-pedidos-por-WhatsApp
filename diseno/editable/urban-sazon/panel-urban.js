/* Urban Sazón: alerta visual de pedido nuevo para el panel. Solo observa el DOM (MutationObserver); no toca la API ni el sondeo.
   Sin audio. Solo transform/opacity; con prefers-reduced-motion, solo fundido. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const quieto = matchMedia("(prefers-reduced-motion:reduce)");
  const tablero = $("#tablero"), btn = $("#btn-nuevos");
  if (!tablero || !btn) return;

  const destello = document.body.appendChild(Object.assign(document.createElement("div"), {id:"u-destello"}));
  destello.setAttribute("aria-hidden", "true");
  const flash = () => destello.animate([{opacity:0}, {opacity:1, offset:.25}, {opacity:0}], {duration:quieto.matches ? 500 : 1000, easing:"ease-out"});

  const CICLO = 1600; // = duración del anillo en panel-urban.css
  let vistas = new Set();
  function tarjetas() {
    const ahora = new Set();
    let hayNueva = false;
    for (const t of tablero.querySelectorAll(".tarjeta.nuevo")) {
      ahora.add(t.id);
      // el tablero se reconstruye cada 3 s: se alinea la fase del anillo con el reloj para que no se reinicie
      t.style.setProperty("--u-delay", -(performance.now() % CICLO) + "ms");
      if (vistas.has(t.id)) continue;
      hayNueva = true; // entra con rebote (o solo fundido)
      t.animate(quieto.matches ? [{opacity:0}, {opacity:1}]
        : [{opacity:0, transform:"translateY(-28px) scale(.94)"}, {opacity:1, transform:"translateY(5px) scale(1.03)", offset:.55}, {opacity:1, transform:"translateY(-3px) scale(.99)", offset:.78}, {opacity:1, transform:"none"}],
        {duration:quieto.matches ? 250 : 650, easing:"cubic-bezier(.23,1,.32,1)"});
    }
    vistas = ahora; // al atenderse (deja de ser .nuevo) sale del conjunto: si vuelve a entrar, repite la alerta
    if (hayNueva) flash();
  }
  const alerta = () => document.body.classList.toggle("u-alerta", !btn.hidden);

  new MutationObserver(tarjetas).observe(tablero, {childList:true});
  new MutationObserver(alerta).observe(btn, {attributes:true, attributeFilter:["hidden"]});

  // el contador de avisos da un saltito al cambiar
  const na = $("#n-avisos");
  if (na) new MutationObserver(() => { if (!na.hidden && !quieto.matches) na.animate([{transform:"scale(1)"}, {transform:"scale(1.4)"}, {transform:"scale(1)"}], {duration:380, easing:"cubic-bezier(.23,1,.32,1)"}); }).observe(na, {childList:true, characterData:true, subtree:true});

  alerta(); tarjetas();
})();
