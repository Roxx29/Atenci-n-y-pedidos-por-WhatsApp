"""Panel del restaurante (tkinter): pedidos activos y avisos casi en tiempo real. Solo stdlib.

  python -m motor panel [carpeta]
"""
import os
import sys
import time
from pathlib import Path

from motor import herramientas
from motor.nucleo import Servicio

TERMINADOS = {"entregado", "cancelado", "caducado"}
SONDEO_MS = 3000
ACCIONES = [("Pagado", "pagado"), ("En preparación", "en_preparacion"), ("Listo", "listo"), ("Entregado", "entregado"), ("Pago no válido", "pago_rechazado")]


def activos(sv):
    """Pedidos que no están entregados, cancelados ni caducados, del más antiguo al más nuevo."""
    return sorted((p for p in sv.listar("pedido") if p["estado"] not in TERMINADOS), key=lambda p: p["id"])


def avisos_nuevos(sv, desde_id):
    """(avisos con id > desde_id, nuevo desde_id)."""
    nuevos = [a for a in sv.listar("aviso") if a["id"] > desde_id]
    return nuevos, max([desde_id] + [a["id"] for a in nuevos])


def avisos_pendientes(sv):
    """Avisos de pedidos que esperan acción del restaurante (confirmado o pago_reportado), del más nuevo al más viejo."""
    espera = {p["id"] for p in sv.listar("pedido") if p["estado"] in {"confirmado", "pago_reportado"}}
    return [a for a in reversed(sv.listar("aviso")) if a.get("pedido_id") in espera]


def ruta_comprobante(sv, pedido):
    """Ruta del comprobante del pedido, solo si el nombre es [0-9a-f]{32}.jpg|png y queda dentro de comprobantes/; si no, None."""
    n = pedido.get("comprobante")
    if not (isinstance(n, str) and herramientas.NOMBRE_COMPROBANTE.fullmatch(n)):
        return None
    carpeta = (sv.dir / "comprobantes").resolve()
    f = (carpeta / n).resolve()
    return f if f.parent == carpeta and f.is_file() else None


def elegir_carpeta(argv, exe_dir):
    """Carpeta del servicio: argumento, o la del .exe si tiene servicio.yaml, o None (preguntar)."""
    if argv:
        return Path(argv[0])
    if (Path(exe_dir) / "servicio.yaml").exists():
        return Path(exe_dir)
    return None


def main(argv):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Pedidos del restaurante")
    carpeta = elegir_carpeta(argv, Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent)
    if carpeta is None:
        root.withdraw()
        carpeta = filedialog.askdirectory(title="Carpeta del servicio (con servicio.yaml)")
        root.deiconify()
    try:
        if not carpeta:
            raise ValueError("No se eligió ninguna carpeta.")
        sv = Servicio(carpeta)
    except Exception as e:
        messagebox.showerror("Panel de pedidos", f"No se pudo abrir el servicio: {e}")
        return

    cols = ("id", "estado", "cliente", "modalidad", "total", "pago", "lineas")
    tabla = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse", height=14)
    for c, w in zip(cols, (50, 110, 130, 90, 80, 150, 380)):
        tabla.heading(c, text=c.capitalize())
        tabla.column(c, width=w)
    tabla.tag_configure("pago_reportado", background="#fff3a0")
    tabla.tag_configure("confirmado", background="#cfe4ff")
    tabla.tag_configure("nuevo", background="#ffb3b3")
    tabla.pack(fill="both", expand=True)
    avisos = tk.Listbox(root, height=6)
    avisos.pack(fill="x")

    def marcar(estado):
        sel = tabla.selection()
        if not sel:
            return
        try:
            herramientas.marcar_pedido(sv, int(sel[0]), estado)
        except ValueError as e:
            messagebox.showerror("No se pudo marcar", str(e))
        refrescar(sonar=False)

    def ver_comprobante():
        sel = tabla.selection()
        ruta = next((ruta_comprobante(sv, p) for p in sv.listar("pedido") if sel and p["id"] == int(sel[0])), None)
        if ruta:
            os.startfile(ruta)
        else:
            messagebox.showinfo("Comprobante", "Sin comprobante")

    barra = ttk.Frame(root)
    barra.pack(fill="x")
    for texto, estado in ACCIONES:
        ttk.Button(barra, text=texto, command=lambda e=estado: marcar(e)).pack(side="left", padx=4, pady=4)
    ttk.Button(barra, text="Ver comprobante", command=ver_comprobante).pack(side="left", padx=4, pady=4)

    estado = {"aviso": max([a["id"] for a in sv.listar("aviso")] + [0]), "vistos": {p["id"] for p in activos(sv)}, "purga": time.time()}
    herramientas.purgar_comprobantes(sv)

    def refrescar(sonar=True):
        try:
            herramientas.caducar_pedidos(sv)
            if time.time() - estado["purga"] > 3600:
                estado["purga"] = time.time()
                herramientas.purgar_comprobantes(sv)
            peds = activos(sv)
            nuevos, estado["aviso"] = avisos_nuevos(sv, estado["aviso"])
        except Exception as e:  # base bloqueada un momento: reintentar en el próximo ciclo
            print(f"[panel] {e}")
            peds, nuevos = None, []
        if peds is not None:
            ids = {p["id"] for p in peds}
            pedidos_nuevos = ids - estado["vistos"]
            sel = tabla.selection()
            tabla.delete(*tabla.get_children())
            for p in peds:
                tags = (p["estado"],) + (("nuevo",) if p["id"] in pedidos_nuevos else ())
                pago = f"{p.get('metodo', '')} {p['referencia']}".strip() if p.get("referencia") else ""
                pago += " (comprobante)" if p.get("comprobante") else ""
                lineas = ", ".join(f"{l['cantidad']} x {l['nombre']}" for l in p["lineas"])
                tabla.insert("", "end", iid=str(p["id"]), tags=tags, values=(p["id"], p["estado"], p["nombre"], p["modalidad"], herramientas.dinero(p["total_c"]), pago, lineas))
            if sel and tabla.exists(sel[0]):
                tabla.selection_set(sel[0])
            estado["vistos"] |= ids
            nuevos_pedidos = bool(pedidos_nuevos)
        else:
            nuevos_pedidos = False
        for a in nuevos:
            avisos.insert(0, a["texto"])
        if sonar and (nuevos or nuevos_pedidos):
            import winsound
            winsound.MessageBeep()
            root.deiconify()
            root.lift()
        root.after(SONDEO_MS, refrescar)

    for a in avisos_pendientes(sv):
        avisos.insert("end", a["texto"])
    refrescar(sonar=False)  # al arrancar no suena por lo viejo
    root.mainloop()
    sv.cerrar()


if __name__ == "__main__":
    main(sys.argv[1:])
