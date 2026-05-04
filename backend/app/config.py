"""
Gestión de configuración
Carga unificada desde el archivo .env en la raíz del proyecto
"""

import os
from dotenv import load_dotenv

# Cargar archivo .env de la raíz del proyecto
# Ruta: MiroFish/.env (relativa a backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # Si no hay .env en la raíz, cargar variables de entorno (para producción)
    load_dotenv(override=True)


class Config:
    """Clase de configuración de Flask"""
    
    # Configuración de Flask
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if DEBUG and not SECRET_KEY:
        SECRET_KEY = 'mirofish-dev-secret-key'
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get(
            'CORS_ORIGINS',
            'http://localhost:3000,http://127.0.0.1:3000'
        ).split(',')
        if origin.strip()
    ]
    
    # Configuración JSON - deshabilitar escape ASCII para mostrar Unicode directamente
    JSON_AS_ASCII = False
    
    # Configuración LLM (formato OpenAI unificado)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Configuración de Zep
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # Configuración de subida de archivos
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # Configuración de procesamiento de texto
    DEFAULT_CHUNK_SIZE = 500  # Tamaño de fragmento predeterminado
    DEFAULT_CHUNK_OVERLAP = 50  # Tamaño de superposición predeterminado
    
    # Configuración de simulación OASIS
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # Configuración de acciones disponibles en plataformas OASIS
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Configuración de Report Agent
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """Validar configuración necesaria"""
        errors = []
        if not cls.SECRET_KEY:
            errors.append("SECRET_KEY no configurado")
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY no configurado")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY no configurado")
        return errors
