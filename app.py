import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from datetime import datetime, date, timedelta
import urllib.parse

# ==========================================================
# CONFIGURACIÓN GENERAL DE LA APP
# ==========================================================
st.set_page_config(
    page_title="CrediYa",
    page_icon="💸",
    layout="centered",
    initial_sidebar_state="collapsed",
)

DB_NAME = "prestamos.db"
FREQ_DAYS = {"Diario": 1, "Semanal": 7, "Quincenal": 15, "Mensual": 30}

# CSS para optimizar la experiencia táctil en celulares
st.markdown(
    """
    <style>
        .block-container {padding-top: 1.2rem; padding-bottom: 3rem;}
        div.stButton > button, .stLinkButton > a {
            width: 100%;
            border-radius: 10px;
            font-weight: 600;
            padding: 0.6rem 0.5rem;
        }
        div[data-testid="stMetric"] {
            background-color: #f6f7f9;
            border-radius: 12px;
            padding: 10px 8px;
            border: 1px solid #eaecef;
        }
        div[data-baseweb="tab-list"] {
            gap: 2px;
        }
        button[data-baseweb="tab"] {
            font-size: 13px;
            padding: 8px 6px;
        }
        h1, h2, h3 {margin-bottom: 0.3rem;}
        .semaforo-badge {
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            color: white;
            display: inline-block;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==========================================================
# BASE DE DATOS
# ==========================================================
def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS socios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            socio TEXT NOT NULL,
            monto REAL NOT NULL,
            fecha TEXT NOT NULL,
            nota TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            whatsapp TEXT NOT NULL,
            fecha_alta TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS prestamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            monto REAL NOT NULL,
            interes_pct REAL NOT NULL,
            frecuencia TEXT NOT NULL,
            fecha_inicio TEXT NOT NULL,
            proximo_cobro TEXT NOT NULL,
            estado TEXT DEFAULT 'Activo'
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestamo_id INTEGER NOT NULL,
            monto REAL NOT NULL,
            tipo TEXT NOT NULL,
            fecha TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )"""
    )
    conn.commit()
    conn.close()


def get_config(clave, default=""):
    conn = get_connection()
    row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_config(clave, valor):
    conn = get_connection()
    conn.execute(
        """INSERT INTO config (clave, valor) VALUES (?, ?)
           ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor""",
        (clave, valor),
    )
    conn.commit()
    conn.close()


def load_data():
    conn = get_connection()
    clientes = pd.read_sql("SELECT * FROM clientes", conn)
    prestamos = pd.read_sql("SELECT * FROM prestamos", conn)
    pagos = pd.read_sql("SELECT * FROM pagos", conn)
    socios = pd.read_sql("SELECT * FROM socios", conn)
    conn.close()
    return clientes, prestamos, pagos, socios


def actualizar_estados_pagados(prestamos, pagos):
    """Marca como 'Pagado' los préstamos cuyo capital ya fue totalmente recuperado."""
    conn = get_connection()
    for _, p in prestamos.iterrows():
        capital_pagado = pagos[(pagos.prestamo_id == p.id) & (pagos.tipo == "Capital")].monto.sum()
        saldo = p.monto - capital_pagado
        if saldo <= 0.01 and p.estado != "Pagado":
            conn.execute("UPDATE prestamos SET estado=? WHERE id=?", ("Pagado", p.id))
    conn.commit()
    conn.close()


# ==========================================================
# UTILIDADES
# ==========================================================
def limpiar_numero(numero: str) -> str:
    return "".join(ch for ch in numero if ch.isdigit())


def link_whatsapp(numero: str, mensaje: str) -> str:
    numero_limpio = limpiar_numero(numero)
    texto = urllib.parse.quote(mensaje)
    return f"https://wa.me/{numero_limpio}?text={texto}"


def construir_mensaje(tipo, cliente, monto, interes_pct, fecha, saldo_pendiente):
    return (
        f"📌 *CrediYa - Comprobante*\n\n"
        f"Tipo: {tipo}\n"
        f"Cliente: {cliente}\n"
        f"Monto: ${monto:,.2f}\n"
        f"Interés: {interes_pct}%\n"
        f"Fecha: {fecha}\n"
        f"Saldo pendiente: ${saldo_pendiente:,.2f}\n\n"
        f"Gracias por confiar en CrediYa 💸"
    )


def enriquecer_prestamos(clientes, prestamos, pagos):
    """Devuelve el df de préstamos con saldo, cliente y semáforo calculados."""
    if prestamos.empty:
        return prestamos.assign(
            cliente_nombre=[], cliente_whatsapp=[], capital_pagado=[],
            interes_pagado=[], saldo_capital=[], semaforo=[]
        )

    df = prestamos.merge(
        clientes.rename(columns={"id": "cliente_id", "nombre": "cliente_nombre", "whatsapp": "cliente_whatsapp"}),
        on="cliente_id",
        how="left",
    )

    capital_pagado_list = []
    interes_pagado_list = []
    for pid in df["id"]:
        cap = pagos[(pagos.prestamo_id == pid) & (pagos.tipo == "Capital")].monto.sum()
        inte = pagos[(pagos.prestamo_id == pid) & (pagos.tipo == "Interés")].monto.sum()
        capital_pagado_list.append(cap)
        interes_pagado_list.append(inte)

    df["capital_pagado"] = capital_pagado_list
    df["interes_pagado"] = interes_pagado_list
    df["saldo_capital"] = df["monto"] - df["capital_pagado"]

    hoy = date.today()

    def semaforo(row):
        if row["estado"] == "Pagado":
            return "✅ Pagado"
        try:
            prox = datetime.strptime(row["proximo_cobro"], "%Y-%m-%d").date()
        except Exception:
            return "🟢 Al día"
        if prox < hoy:
            return "🔴 Moroso"
        elif prox <= hoy + timedelta(days=2):
            return "🟡 Próximo a vencer"
        else:
            return "🟢 Al día"

    df["semaforo"] = df.apply(semaforo, axis=1)
    return df


# ==========================================================
# INICIALIZACIÓN
# ==========================================================
init_db()
clientes_df, prestamos_df, pagos_df, socios_df = load_data()
actualizar_estados_pagados(prestamos_df, pagos_df)
clientes_df, prestamos_df, pagos_df, socios_df = load_data()  # recargar tras update
prestamos_full = enriquecer_prestamos(clientes_df, prestamos_df, pagos_df)

ADMIN_WHATSAPP = get_config("admin_whatsapp", "")

st.title("💸 CrediYa")
st.caption("Gestión de préstamos personales · Solange & Cristian")

tab_dash, tab_clientes, tab_prestamos, tab_cobros, tab_socios, tab_config = st.tabs(
    ["📊 Inicio", "👥 Clientes", "💵 Préstamos", "💰 Cobros", "🤝 Socios", "⚙️ Config"]
)

# ==========================================================
# TAB 1 - DASHBOARD
# ==========================================================
with tab_dash:
    total_aportado = socios_df["monto"].sum() if not socios_df.empty else 0.0
    total_otorgado = prestamos_df["monto"].sum() if not prestamos_df.empty else 0.0
    total_capital_recuperado = pagos_df[pagos_df.tipo == "Capital"]["monto"].sum() if not pagos_df.empty else 0.0
    total_interes_cobrado = pagos_df[pagos_df.tipo == "Interés"]["monto"].sum() if not pagos_df.empty else 0.0

    capital_en_calle = total_otorgado - total_capital_recuperado
    capital_caja = total_aportado - total_otorgado + total_capital_recuperado + total_interes_cobrado

    if not prestamos_full.empty:
        activos = prestamos_full[prestamos_full.estado == "Activo"]
        ganancia_proyectada = (activos["saldo_capital"] * activos["interes_pct"] / 100).sum()
    else:
        ganancia_proyectada = 0.0

    aporte_cristian = socios_df[socios_df.socio == "Cristian"]["monto"].sum() if not socios_df.empty else 0.0
    aporte_solange = socios_df[socios_df.socio == "Solange"]["monto"].sum() if not socios_df.empty else 0.0

    c1, c2 = st.columns(2)
    c1.metric("💼 Capital Aportado", f"${total_aportado:,.0f}")
    c2.metric("🚶 Prestado en Calle", f"${capital_en_calle:,.0f}")
    c3, c4 = st.columns(2)
    c3.metric("🏦 Disponible en Caja", f"${capital_caja:,.0f}")
    c4.metric("📈 Ganancia Proyectada", f"${ganancia_proyectada:,.0f}")
    st.metric("✅ Ganancia Ya Cobrada (Intereses)", f"${total_interes_cobrado:,.0f}")

    st.divider()
    st.subheader("Distribución de Capital")
    if (aporte_cristian + aporte_solange + total_interes_cobrado) > 0:
        fig = px.pie(
            values=[aporte_cristian, aporte_solange, total_interes_cobrado],
            names=["Cristian", "Solange", "Ganancias CrediYa"],
            hole=0.55,
            color_discrete_sequence=["#3B82F6", "#EC4899", "#10B981"],
        )
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Todavía no hay datos suficientes para mostrar el gráfico.")

    st.divider()
    st.subheader("📋 Próximos Cobros")
    if prestamos_full.empty:
        st.info("No hay préstamos registrados todavía.")
    else:
        pendientes = prestamos_full[prestamos_full.estado == "Activo"].sort_values("proximo_cobro")
        if pendientes.empty:
            st.success("No hay cobros pendientes. 🎉")
        for _, row in pendientes.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['cliente_nombre']}**  —  {row['semaforo']}")
                st.caption(
                    f"Saldo capital: ${row['saldo_capital']:,.0f} · "
                    f"Interés: {row['interes_pct']}% · Próx. cobro: {row['proximo_cobro']}"
                )
                msg = construir_mensaje(
                    "Recordatorio de Cobro",
                    row["cliente_nombre"],
                    row["saldo_capital"],
                    row["interes_pct"],
                    row["proximo_cobro"],
                    row["saldo_capital"],
                )
                bcol1, bcol2 = st.columns(2)
                with bcol1:
                    st.link_button("📲 Enviar al Cliente", link_whatsapp(row["cliente_whatsapp"], msg))
                with bcol2:
                    if ADMIN_WHATSAPP:
                        st.link_button("📤 Enviarme Copia", link_whatsapp(ADMIN_WHATSAPP, msg))
                    else:
                        st.caption("Configurá tu WhatsApp en ⚙️ Config")

# ==========================================================
# TAB 2 - CLIENTES
# ==========================================================
with tab_clientes:
    st.subheader("👥 Nuevo Cliente")
    with st.form("form_cliente", clear_on_submit=True):
        nombre = st.text_input("Nombre completo")
        whatsapp_cliente = st.text_input("WhatsApp (con código de país y área, ej: 5491122334455)")
        enviar_cliente = st.form_submit_button("Guardar Cliente")

        if enviar_cliente:
            if nombre.strip() and whatsapp_cliente.strip():
                conn = get_connection()
                conn.execute(
                    "INSERT INTO clientes (nombre, whatsapp, fecha_alta) VALUES (?, ?, ?)",
                    (nombre.strip(), limpiar_numero(whatsapp_cliente), date.today().isoformat()),
                )
                conn.commit()
                conn.close()
                st.success(f"Cliente '{nombre}' guardado ✅")
                st.rerun()
            else:
                st.error("Completá nombre y WhatsApp.")

    st.divider()
    st.subheader("📇 Clientes Registrados")
    if clientes_df.empty:
        st.info("Todavía no hay clientes cargados.")
    else:
        st.dataframe(
            clientes_df[["nombre", "whatsapp", "fecha_alta"]],
            use_container_width=True,
            hide_index=True,
        )

# ==========================================================
# TAB 3 - PRÉSTAMOS
# ==========================================================
with tab_prestamos:
    st.subheader("💵 Nuevo Préstamo")
    if clientes_df.empty:
        st.warning("Primero registrá al menos un cliente en la pestaña 👥 Clientes.")
    else:
        with st.form("form_prestamo", clear_on_submit=True):
            cliente_sel = st.selectbox("Cliente", clientes_df["nombre"].tolist())
            monto_prestamo = st.number_input("Monto del préstamo ($)", min_value=0.0, step=1000.0)
            interes_pct = st.number_input("Interés por período (%)", min_value=0.0, step=1.0)
            frecuencia = st.selectbox("Frecuencia de cobro", list(FREQ_DAYS.keys()))
            fecha_inicio = st.date_input("Fecha de inicio", value=date.today())
            enviar_prestamo = st.form_submit_button("Registrar Préstamo")

            if enviar_prestamo:
                if monto_prestamo > 0:
                    cliente_id = int(clientes_df[clientes_df.nombre == cliente_sel]["id"].iloc[0])
                    cliente_wpp = clientes_df[clientes_df.nombre == cliente_sel]["whatsapp"].iloc[0]
                    proximo = fecha_inicio + timedelta(days=FREQ_DAYS[frecuencia])
                    conn = get_connection()
                    conn.execute(
                        """INSERT INTO prestamos
                           (cliente_id, monto, interes_pct, frecuencia, fecha_inicio, proximo_cobro, estado)
                           VALUES (?, ?, ?, ?, ?, ?, 'Activo')""",
                        (
                            cliente_id,
                            monto_prestamo,
                            interes_pct,
                            frecuencia,
                            fecha_inicio.isoformat(),
                            proximo.isoformat(),
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Préstamo registrado ✅")

                    msg = construir_mensaje(
                        "Nuevo Préstamo Otorgado",
                        cliente_sel,
                        monto_prestamo,
                        interes_pct,
                        fecha_inicio.isoformat(),
                        monto_prestamo,
                    )
                    colw1, colw2 = st.columns(2)
                    with colw1:
                        st.link_button("📲 Enviar al Cliente", link_whatsapp(cliente_wpp, msg))
                    with colw2:
                        if ADMIN_WHATSAPP:
                            st.link_button("📤 Enviarme Copia", link_whatsapp(ADMIN_WHATSAPP, msg))
                else:
                    st.error("El monto debe ser mayor a 0.")

    st.divider()
    st.subheader("📑 Préstamos Activos")
    if prestamos_full.empty or prestamos_full[prestamos_full.estado == "Activo"].empty:
        st.info("No hay préstamos activos.")
    else:
        activos_show = prestamos_full[prestamos_full.estado == "Activo"][
            ["cliente_nombre", "monto", "saldo_capital", "interes_pct", "frecuencia", "proximo_cobro", "semaforo"]
        ].rename(
            columns={
                "cliente_nombre": "Cliente",
                "monto": "Monto",
                "saldo_capital": "Saldo",
                "interes_pct": "Interés %",
                "frecuencia": "Frecuencia",
                "proximo_cobro": "Próx. Cobro",
                "semaforo": "Estado",
            }
        )
        st.dataframe(activos_show, use_container_width=True, hide_index=True)

    with st.expander("📜 Ver historial de préstamos pagados"):
        pagados = prestamos_full[prestamos_full.estado == "Pagado"]
        if pagados.empty:
            st.caption("Todavía no hay préstamos totalmente cancelados.")
        else:
            st.dataframe(
                pagados[["cliente_nombre", "monto", "interes_pagado", "fecha_inicio"]].rename(
                    columns={
                        "cliente_nombre": "Cliente",
                        "monto": "Monto",
                        "interes_pagado": "Interés Cobrado",
                        "fecha_inicio": "Fecha Inicio",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

# ==========================================================
# TAB 4 - COBROS / PAGOS PARCIALES
# ==========================================================
with tab_cobros:
    st.subheader("💰 Registrar Cobro / Pago")
    activos_df = prestamos_full[prestamos_full.estado == "Activo"] if not prestamos_full.empty else prestamos_full

    if activos_df.empty:
        st.info("No hay préstamos activos para cobrar.")
    else:
        opciones = {
            f"{row['cliente_nombre']} — Saldo ${row['saldo_capital']:,.0f}": row["id"]
            for _, row in activos_df.iterrows()
        }
        seleccion = st.selectbox("Préstamo", list(opciones.keys()))
        prestamo_id = opciones[seleccion]
        fila = activos_df[activos_df.id == prestamo_id].iloc[0]

        tipo_pago = st.radio("Tipo de pago", ["Interés", "Capital"], horizontal=True)
        monto_pago = st.number_input("Monto recibido ($)", min_value=0.0, step=500.0)
        fecha_pago = st.date_input("Fecha del pago", value=date.today())

        if st.button("✅ Registrar Cobro", use_container_width=True):
            if monto_pago > 0:
                conn = get_connection()
                conn.execute(
                    "INSERT INTO pagos (prestamo_id, monto, tipo, fecha) VALUES (?, ?, ?, ?)",
                    (int(prestamo_id), monto_pago, tipo_pago, fecha_pago.isoformat()),
                )

                # Si es pago de interés, se corre la fecha de próximo cobro
                if tipo_pago == "Interés":
                    nuevo_prox = fecha_pago + timedelta(days=FREQ_DAYS[fila["frecuencia"]])
                    conn.execute(
                        "UPDATE prestamos SET proximo_cobro=? WHERE id=?",
                        (nuevo_prox.isoformat(), int(prestamo_id)),
                    )

                # Si el abono de capital cancela el préstamo, se marca como Pagado
                nuevo_saldo = fila["saldo_capital"] - (monto_pago if tipo_pago == "Capital" else 0)
                if tipo_pago == "Capital" and nuevo_saldo <= 0.01:
                    conn.execute("UPDATE prestamos SET estado='Pagado' WHERE id=?", (int(prestamo_id),))

                conn.commit()
                conn.close()

                st.success("Cobro registrado ✅")

                saldo_mostrado = max(nuevo_saldo, 0) if tipo_pago == "Capital" else fila["saldo_capital"]
                msg = construir_mensaje(
                    f"Pago Recibido ({tipo_pago})",
                    fila["cliente_nombre"],
                    monto_pago,
                    fila["interes_pct"],
                    fecha_pago.isoformat(),
                    saldo_mostrado,
                )
                colp1, colp2 = st.columns(2)
                with colp1:
                    st.link_button("📲 Enviar al Cliente", link_whatsapp(fila["cliente_whatsapp"], msg))
                with colp2:
                    if ADMIN_WHATSAPP:
                        st.link_button("📤 Enviarme Copia", link_whatsapp(ADMIN_WHATSAPP, msg))
            else:
                st.error("El monto debe ser mayor a 0.")

    st.divider()
    st.subheader("🕘 Historial de Cobros")
    if pagos_df.empty:
        st.caption("Todavía no se registraron cobros.")
    else:
        hist = pagos_df.merge(
            prestamos_df[["id", "cliente_id"]], left_on="prestamo_id", right_on="id", suffixes=("", "_p")
        ).merge(clientes_df[["id", "nombre"]], left_on="cliente_id", right_on="id", suffixes=("", "_c"))
        hist = hist[["fecha", "nombre", "tipo", "monto"]].rename(
            columns={"fecha": "Fecha", "nombre": "Cliente", "tipo": "Tipo", "monto": "Monto"}
        ).sort_values("Fecha", ascending=False)
        st.dataframe(hist, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📤 Reenviar Comprobante al Administrador")
    if not pagos_df.empty and ADMIN_WHATSAPP:
        hist_options = {
            f"#{r.id} · {r.fecha} · {r.tipo} · ${r.monto:,.0f}": r.id for r in pagos_df.itertuples()
        }
        pago_sel = st.selectbox("Elegí un cobro para reenviar", list(hist_options.keys()), key="reenvio")
        pago_id = hist_options[pago_sel]
        pago_row = pagos_df[pagos_df.id == pago_id].iloc[0]
        prestamo_rel = prestamos_full[prestamos_full.id == pago_row["prestamo_id"]]
        if not prestamo_rel.empty:
            prestamo_rel = prestamo_rel.iloc[0]
            msg_reenvio = construir_mensaje(
                f"Reenvío de Comprobante ({pago_row['tipo']})",
                prestamo_rel["cliente_nombre"],
                pago_row["monto"],
                prestamo_rel["interes_pct"],
                pago_row["fecha"],
                prestamo_rel["saldo_capital"],
            )
            st.link_button("📤 Reenviarme por WhatsApp", link_whatsapp(ADMIN_WHATSAPP, msg_reenvio))
    elif not ADMIN_WHATSAPP:
        st.caption("Configurá tu número de WhatsApp en ⚙️ Config para poder reenviarte comprobantes.")
    else:
        st.caption("No hay comprobantes para reenviar todavía.")

# ==========================================================
# TAB 5 - SOCIOS
# ==========================================================
with tab_socios:
    st.subheader("🤝 Registrar Aporte de Capital")
    with st.form("form_socio", clear_on_submit=True):
        socio_sel = st.selectbox("Socio", ["Solange", "Cristian"])
        monto_aporte = st.number_input("Monto del aporte ($)", min_value=0.0, step=1000.0)
        fecha_aporte = st.date_input("Fecha del aporte", value=date.today())
        nota_aporte = st.text_input("Nota (opcional)")
        enviar_socio = st.form_submit_button("Guardar Aporte")

        if enviar_socio:
            if monto_aporte > 0:
                conn = get_connection()
                conn.execute(
                    "INSERT INTO socios (socio, monto, fecha, nota) VALUES (?, ?, ?, ?)",
                    (socio_sel, monto_aporte, fecha_aporte.isoformat(), nota_aporte),
                )
                conn.commit()
                conn.close()
                st.success(f"Aporte de {socio_sel} registrado ✅")
                st.rerun()
            else:
                st.error("El monto debe ser mayor a 0.")

    st.divider()
    st.subheader("📊 Resumen de Aportes")
    if socios_df.empty:
        st.info("Todavía no hay aportes registrados.")
    else:
        resumen = socios_df.groupby("socio")["monto"].sum().reset_index()
        resumen.columns = ["Socio", "Total Aportado"]
        st.dataframe(resumen, use_container_width=True, hide_index=True)

        st.markdown("**Historial de movimientos**")
        hist_socios = socios_df[["fecha", "socio", "monto", "nota"]].rename(
            columns={"fecha": "Fecha", "socio": "Socio", "monto": "Monto", "nota": "Nota"}
        ).sort_values("Fecha", ascending=False)
        st.dataframe(hist_socios, use_container_width=True, hide_index=True)

# ==========================================================
# TAB 6 - CONFIGURACIÓN
# ==========================================================
with tab_config:
    st.subheader("⚙️ Configuración General")
    st.markdown("Número de WhatsApp donde recibirás copia de todos los comprobantes (formato: código país + área + número, sin '+' ni espacios).")
    nuevo_admin = st.text_input("Tu WhatsApp de control", value=ADMIN_WHATSAPP, placeholder="5491122334455")
    if st.button("Guardar Número", use_container_width=True):
        set_config("admin_whatsapp", limpiar_numero(nuevo_admin))
        st.success("Número guardado ✅")
        st.rerun()

    st.divider()
    st.caption("CrediYa · Base de datos local en SQLite (prestamos.db) · Los datos se conservan entre sesiones.")
