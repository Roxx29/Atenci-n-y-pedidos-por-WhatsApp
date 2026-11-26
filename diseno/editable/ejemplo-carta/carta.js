/* Lógica de la carta. Solo usa la API del motor (../API.md). Todo texto del servidor se pinta con textContent (nunca innerHTML). */
"use strict";
const $ = s => document.querySelector(s);
const st = {cfg: {}, menu: [], comanda: new Map(), dentro: false, pedido: null, aviso: null, sondeo: 0};
const ESTADOS = {por_confirmar: "por confirmar", confirmado: "confirmado · falta el pago", pago_reportado: "revisando tu pago", pagado: "pagado",
  en_preparacion: "en cocina", listo: "listo", en_camino: "en camino", entregado: "entregado", cancelado: "cancelado", caducado: "caducado"};
const num = s => Number((String(s).match(/\d+(?:\.\d+)?/) || [0])[0]); // "B/. 5.00" -> 5
const dinero = n => "B/. " + Number(n).toFixed(2);

async function api(metodo, ruta, cuerpo) {
  const r = await fetch(ruta, {method: metodo, headers: {"Content-Type": "application/json"}, body: cuerpo ? JSON.stringify(cuerpo) : undefined});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(d.error || r.status), {status: r.status});
  return d;
}

/* ---------- Portada ---------- */
async function cargarConfig() {
  st.cfg = await api("GET", "/config");
  const h = st.cfg.horario || {};
  $("#estado").textContent = st.cfg.cerrado ? "Cerrado ahora" : `Abierto · ${h.desde || ""}–${h.hasta || ""}`;
  $("#estado").classList.toggle("cerrado", !!st.cfg.cerrado);
  $("#anuncio").hidden = !st.cfg.anuncio; $("#anuncio").textContent = st.cfg.anuncio || "";
  const zonas = Object.keys(st.cfg.envio || {}).filter(Boolean);
  $("#zona").replaceChildren(...zonas.map(z => new Option(`${z} (+${dinero(st.cfg.envio[z])})`, z)));
  pintarPago();
}

/* ---------- Carta ---------- */
async function cargarMenu() {
  st.menu = await api("GET", "/menu");
  const grupos = new Map();
  for (const m of st.menu) { const c = m.categoria || "Carta"; if (!grupos.has(c)) grupos.set(c, []); grupos.get(c).push(m); }
  $("#carta").replaceChildren(...[...grupos].map(([cat, platos]) => {
    const s = document.createElement("section"); s.className = "categoria";
    const h = document.createElement("h2"); h.textContent = cat; s.append(h);
    for (const m of platos) {
      const f = document.createElement("div"); f.className = "plato" + (m.disponible ? "" : " agotado");
      const n = document.createElement("span"); n.className = "nombre"; n.textContent = m.nombre + (m.disponible ? "" : " (agotado)");
      const p = document.createElement("span"); p.className = "puntos";
      const pr = document.createElement("span"); pr.className = "precio"; pr.textContent = dinero(m.precio);
      const b = document.createElement("button"); b.type = "button"; b.textContent = "+"; b.setAttribute("aria-label", "Añadir " + m.nombre);
      b.onclick = () => cambiar(m.id, 1);
      f.append(n, p, pr, b); s.append(f);
    }
    return s;
  }));
}

/* ---------- Comanda ---------- */
function cambiar(id, d) {
  const q = (st.comanda.get(id) || 0) + d;
  q > 0 ? st.comanda.set(id, q) : st.comanda.delete(id);
  pintarComanda();
}
function pintarComanda() {
  const lis = [...st.comanda].map(([id, q]) => {
    const m = st.menu.find(x => x.id === id), li = document.createElement("li");
    const t = document.createElement("span"); t.textContent = `${q} × ${m.nombre}`;
    const menos = document.createElement("button"); menos.type = "button"; menos.textContent = "−"; menos.setAttribute("aria-label", "Quitar uno"); menos.onclick = () => cambiar(id, -1);
    const mas = document.createElement("button"); mas.type = "button"; mas.textContent = "+"; mas.setAttribute("aria-label", "Uno más"); mas.onclick = () => cambiar(id, 1);
    li.append(t, menos, mas); return li;
  });
  if (!lis.length) { const li = document.createElement("li"); li.className = "vacia"; li.textContent = "Toca «+» en un plato."; lis.push(li); }
  $("#lineas").replaceChildren(...lis);
  const sub = [...st.comanda].reduce((s, [id, q]) => s + q * st.menu.find(x => x.id === id).precio, 0);
  $("#aprox").hidden = !sub; $("#aprox").textContent = `Aprox. ${dinero(sub)}${$("#pedir").modo.value === "entrega" ? " + envío" : ""}. El total exacto lo da el restaurante.`;
  $("#n-comanda").textContent = [...st.comanda.values()].reduce((a, b) => a + b, 0);
}
$("#pedir").addEventListener("change", e => { if (e.target.name === "modo") { $("#entrega").hidden = e.target.value !== "entrega"; pintarComanda(); } });
$("#ver-comanda").onclick = () => $("#comanda").classList.toggle("abierta");

/* ---------- Pedir: entrar (una vez) y mandar la comanda al asistente ---------- */
$("#pedir").addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = ev.target, err = $("#error");
  if (!st.comanda.size) return (err.textContent = "Elige al menos un plato.");
  if (f.modo.value === "entrega" && !f.direccion.value.trim()) return (err.textContent = "Escribe la dirección de entrega.");
  err.textContent = "";
  $("#enviar").disabled = true;
  try {
    if (!st.dentro) {
      const tel = f.telefono.value.replace(/\D/g, "").replace(/^507/, "");
      if (!f.nombre.value.trim()) throw new Error("Escribe tu nombre.");
      if (!/^6\d{7}$/.test(tel)) throw new Error("Escribe un celular de Panamá de 8 dígitos (empieza por 6).");
      if (!f.privacidad.checked) throw new Error("Necesitamos tu permiso para tomar el pedido.");
      st.aviso = st.aviso || await api("GET", "/aviso");
      await api("POST", "/entrar", {nombre: f.nombre.value.trim(), telefono: "507" + tel, acepta_privacidad: true, version_aviso: st.aviso.version});
      st.dentro = true; $("#quien").hidden = true;
      cargarConfig(); // ahora trae los datos de pago
      clearInterval(st.sondeo); st.sondeo = setInterval(sondear, 5000);
    }
    const platos = [...st.comanda].map(([id, q]) => `${q} ${st.menu.find(x => x.id === id).nombre}`).join("; ");
    const como = f.modo.value === "entrega" ? `Para entrega en la zona ${f.zona.value}, dirección: ${f.direccion.value.trim()}.` : "Paso a recoger.";
    await decir(`Quiero ${platos}. ${como}`);
    st.comanda.clear(); pintarComanda();
  } catch (e) {
    err.textContent = e.status === 400 && /aviso/i.test(e.message) ? "El aviso de privacidad cambió: léelo y vuelve a marcarlo." : e.message;
  } finally { $("#enviar").disabled = false; }
});

/* ---------- Conversación con el asistente ---------- */
function burbuja(texto, yo) { const p = document.createElement("p"); if (yo) p.className = "yo"; p.textContent = texto; $("#mensajes").append(p); p.scrollIntoView({block: "nearest"}); }
async function decir(texto) {
  $("#charla").hidden = false; burbuja(texto, true);
  try {
    const r = await api("POST", "/chat", {texto});
    burbuja(r.respuesta.replace(/\*\*?|_/g, ""));
    if (r.pedido) ponerPedido(r.pedido);
  } catch (e) {
    if (e.status === 401) { st.dentro = false; $("#quien").hidden = false; burbuja("Tu sesión venció: vuelve a poner tus datos."); }
    else burbuja("No pudimos enviar el mensaje. Inténtalo otra vez.");
  }
}
$("#responder").addEventListener("submit", ev => { ev.preventDefault(); const t = ev.target.texto.value.trim(); if (t) { ev.target.texto.value = ""; decir(t); } });

/* ---------- Tu pedido y el pago ---------- */
function ponerPedido(p) {
  st.pedido = p; $("#ticket").hidden = false;
  $("#t-num").textContent = "#" + p.id; $("#t-estado").textContent = ESTADOS[p.estado] || p.estado;
  const tabla = $("#t-items"), fila = (a, b, cl) => { const tr = tabla.insertRow(); if (cl) tr.className = cl; tr.insertCell().textContent = a; tr.insertCell().textContent = b; };
  tabla.replaceChildren();
  p.items.forEach(i => fila(`${i.cantidad} × ${i.nombre}`, dinero(num(i.precio) * i.cantidad)));
  if (num(p.envio)) fila("Envío", p.envio);
  fila("Total", p.total, "total");
  const espera = p.estado === "confirmado";
  $("#b-confirmar").hidden = p.estado !== "por_confirmar";
  $("#b-efectivo").hidden = !(espera && p.modalidad === "entrega" && st.cfg.efectivo);
  $("#b-comprobante").hidden = !(espera || p.estado === "pago_reportado");
  $("#b-ubicacion").hidden = !(p.modalidad === "entrega" && !p.ubicacion && !["entregado", "cancelado", "caducado"].includes(p.estado));
  $("#t-repartidor").hidden = p.estado !== "en_camino";
  $("#t-repartidor").textContent = p.mensaje || (p.repartidor ? `${p.repartidor} va en camino${p.eta_min ? `: llega en ~${p.eta_min} min` : ""}.` : "Va en camino.");
  pintarPago();
}
function pintarPago() {
  const pago = st.cfg.pago || {}, ver = st.pedido && st.pedido.estado === "confirmado" && (pago.yappy || pago.transferencia);
  $("#t-pago").hidden = !ver;
  $("#t-pago").textContent = ver ? ["Paga y envía el comprobante:", pago.yappy && "Yappy: " + pago.yappy, pago.transferencia && "Transferencia: " + pago.transferencia].filter(Boolean).join("\n") : "";
}
async function sondear() {
  try {
    const [{pedidos}, {mensajes}] = await Promise.all([api("GET", "/pedidos"), api("GET", "/avisos")]);
    mensajes.forEach(m => burbuja(m));
    if (pedidos.length) ponerPedido(pedidos[0]);
  } catch (e) { if (e.status === 401) clearInterval(st.sondeo); }
}
$("#b-confirmar").onclick = () => decir("Sí, confirmo el pedido.");
$("#b-efectivo").onclick = async () => {
  try { ponerPedido((await api("POST", "/efectivo", {pedido: st.pedido.id})).pedido); } catch (e) { burbuja(e.message); }
};
$("#b-comprobante").querySelector("input").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const r = await fetch(`/comprobante?pedido=${st.pedido.id}&referencia=`, {method: "POST", headers: {"Content-Type": f.type}, body: f});
  const d = await r.json().catch(() => ({}));
  r.ok ? ponerPedido(d.pedido) : burbuja(d.error || "No se pudo enviar el comprobante.");
  e.target.value = "";
};
$("#b-ubicacion").onclick = () => navigator.geolocation.getCurrentPosition(async pos => {
  try { await api("POST", "/ubicacion", {lat: pos.coords.latitude, lon: pos.coords.longitude, precision: Math.round(pos.coords.accuracy || 0)}); sondear(); }
  catch (e) { burbuja(e.message); }
}, () => burbuja("No pudimos leer tu ubicación. Puedes escribir la dirección al restaurante."));

/* ---------- Arranque ---------- */
cargarConfig().catch(() => { $("#estado").textContent = "Sin conexión con el restaurante"; });
cargarMenu().then(pintarComanda).catch(() => {});
api("GET", "/aviso").then(a => { st.aviso = a; $("#aviso-texto").textContent = a.texto; }).catch(() => {});
api("GET", "/pedidos").then(({pedidos}) => { // ¿ya había entrado (cookie viva)? sigue donde iba
  st.dentro = true; $("#quien").hidden = true; cargarConfig().catch(() => {});
  st.sondeo = setInterval(sondear, 5000); if (pedidos.length) ponerPedido(pedidos[0]);
}).catch(() => {}); // 401: aún no ha entrado
setInterval(() => cargarConfig().catch(() => {}), 60000); // si el restaurante cierra o cambia el anuncio, se ve sin recargar
