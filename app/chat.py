"""
app/chat.py - Interfaz de Chat Streamlit para WVTR RAG
=====================================================
Chat simple estilo ChatGPT para consultar datos WVTR.

Ejecutar: streamlit run app/chat.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import streamlit as st
from rag import rag_query

# ============================================
# CONFIGURACIÓN DE LA PÁGINA
# ============================================

st.set_page_config(
    page_title="WVTR Assistant",
    page_icon="🧪",
    layout="centered"
)

# ============================================
# TÍTULO Y DESCRIPCIÓN
# ============================================

st.title("🧪 WVTR Assistant")
st.markdown("""
*Tu asistente experto en polímeros y barrera de empaque.*

Pregúntame sobre datos de **Water Vapor Transmission Rate (WVTR)** de polímeros.
""")

# ============================================
# HISTORIAL DE CHAT
# ============================================

# Inicializar historial en session_state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Mostrar mensajes anteriores
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ============================================
# INPUT DEL USUARIO
# ============================================

# Campo de entrada
if prompt := st.chat_input("Escribe tu pregunta sobre polímeros..."):
    
    # Mostrar pregunta del usuario
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Generar respuesta
    with st.chat_message("assistant"):
        with st.spinner("Buscando en la base de datos..."):
            try:
                result = rag_query(prompt)
                response = result["answer"]
                st.markdown(response)
                
                # Guardar en historial
                st.session_state.messages.append({"role": "assistant", "content": response})
                
            except Exception as e:
                error_msg = f"Error al procesar tu pregunta: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

# ============================================
# BARRA LATERAL CON INFORMACIÓN
# ============================================

with st.sidebar:
    st.header("ℹ️ Información")
    
    st.markdown("""
    **WVTR Assistant** utiliza:
    - 🧠 GPT-5.6 Luna para generar respuestas
    - 🔍 pgvector para búsqueda semántica
    - 📊 PostgreSQL con datos WVTR
    
    **Ejemplos de preguntas:**
    - ¿Cuál es el WVTR del PBAT?
    - Compara PHBV con PHBV/clay
    - ¿Qué polímeros tienen mejor barrera?
    - Dame los valores de PLA
    """)
    
    st.divider()
    
    if st.button("🗑️ Limpiar chat"):
        st.session_state.messages = []
        st.rerun()
    
    st.divider()
    st.caption("Desarrollado por Sebastian Sabogal")
