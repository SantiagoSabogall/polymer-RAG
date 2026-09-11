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
# PREGUNTAS SUGERIDAS
# ============================================

SUGGESTED_QUESTIONS = [
    "¿Cuántos registros hay en la base de datos?",
    "¿Cuál es el WVTR del PBAT?",
    "¿Qué polímeros tienen mejor barrera que LDPE?",
    "Compara PHBV con PHBV/clay nanocomposite",
    "Dame los valores de PLA",
    "¿Qué polímeros son biodegradables?",
]

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
# MENSAJE DE BIENVENIDA + PREGUNTAS SUGERIDAS
# ============================================

# Mostrar bienvenida solo si no hay mensajes
if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown("""
¡Hola! 👋 Soy **WVTR Assistant**, tu experto en polímeros y barrera de empaque.

Puedo ayudarte a:
- 🔍 Buscar datos WVTR de polímeros específicos
- 📊 Comparar propiedades de barrera entre materiales
- 📋 Obtener listas de polímeros por condiciones
- 📈 Analizar tendencias en los datos

**Elige una pregunta sugerida o escribe la tuya:**
""")
        
        # Botones de preguntas sugeridas
        st.markdown("**Preguntas sugeridas:**")
        cols = st.columns(2)
        for i, question in enumerate(SUGGESTED_QUESTIONS):
            col = cols[i % 2]
            if col.button(question, key=f"suggest_{i}", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": question})
                st.rerun()

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
