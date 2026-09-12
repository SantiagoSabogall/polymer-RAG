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
# CONFIGURACION DE LA PAGINA
# ============================================

st.set_page_config(
    page_title="WVTR Assistant",
    page_icon=" ",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ============================================
# ESTILOS CSS - INTER + LORA
# ============================================

st.markdown("""
<style>
    /* Importar fuentes desde Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600;700&display=swap');
    
    /* Fuente principal - Inter para todo */
    .stApp, .stMarkdown, .stChatMessage, .stTextInput, .stButton {
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Titulos - Lora */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Lora', serif !important;
    }
    
    h1 {
        font-weight: 700 !important;
        font-size: 2.5rem !important;
    }
    
    h2 {
        font-weight: 600 !important;
        font-size: 1.8rem !important;
    }
    
    h3 {
        font-weight: 600 !important;
        font-size: 1.4rem !important;
    }
    
    /* Texto general */
    .stMarkdown p {
        font-family: 'Inter', sans-serif !important;
        font-size: 1rem !important;
        line-height: 1.6 !important;
    }
    
    /* Mensajes del usuario */
    .stChatMessage[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Mensajes del asistente */
    .stChatMessage[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Input del usuario */
    .stTextInput input, .stTextArea textarea {
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Botones */
    .stButton button {
        font-family: 'Inter', sans-serif !important;
        font-weight: 500 !important;
    }
    
    /* Sidebar oculto */
    [data-testid="stSidebar"] {
        display: none;
    }
    
    /* Eliminar padding innecesario */
    .block-container {
        padding-top: 2rem !important;
        max-width: 800px !important;
    }
    
    /* Chat input */
    .stChatInput {
        font-family: 'Inter', sans-serif !important;
    }
</style>
""", unsafe_allow_html=True)

# ============================================
# PREGUNTAS SUGERIDAS
# ============================================

SUGGESTED_QUESTIONS = [
    "Cuantos registros hay en la base de datos?",
    "Cual es el WVTR del PBAT?",
    "Que polimeros tienen mejor barrera que LDPE?",
    "Compara PHBV con PHBV/clay nanocomposite",
    "Dame los valores de PLA",
    "Que polimeros son biodegradables?",
]

# ============================================
# TITULO Y DESCRIPCION
# ============================================

st.title("WVTR Assistant")
st.markdown("""
*Tu asistente experto en polimeros y barrera de empaque.*

Preguntame sobre datos de **Water Vapor Transmission Rate (WVTR)** de polimeros.
""")

# ============================================
# HISTORIAL DE CHAT
# ============================================

# Inicializar historial en session_state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Inicializar pregunta pendiente
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

# Mostrar mensajes anteriores
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ============================================
# PROCESAR PREGUNTA PENDIENTE (de botones sugeridos)
# ============================================

if st.session_state.pending_question:
    prompt = st.session_state.pending_question
    st.session_state.pending_question = None

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Buscando en la base de datos..."):
            try:
                result = rag_query(prompt)
                response = result["answer"]
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
            except Exception as e:
                error_msg = f"Error al procesar tu pregunta: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

# ============================================
# MENSAJE DE BIENVENIDA + PREGUNTAS SUGERIDAS
# ============================================

# Mostrar bienvenida solo si no hay mensajes
if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown("""
Hola! Soy **WVTR Assistant**, tu experto en polimeros y barrera de empaque.

Puedo ayudarte a:
- Buscar datos WVTR de polimeros especificos
- Comparar propiedades de barrera entre materiales
- Obtener listas de polimeros por condiciones
- Analizar tendencias en los datos

**Elige una pregunta sugerida o escribe la tuya:**
""")
        
        # Botones de preguntas sugeridas
        st.markdown("**Preguntas sugeridas:**")
        cols = st.columns(2)
        for i, question in enumerate(SUGGESTED_QUESTIONS):
            col = cols[i % 2]
            if col.button(question, key=f"suggest_{i}", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": question})
                st.session_state.pending_question = question
                st.rerun()

# ============================================
# INPUT DEL USUARIO
# ============================================

# Campo de entrada
if prompt := st.chat_input("Escribe tu pregunta sobre polimeros..."):
    
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
