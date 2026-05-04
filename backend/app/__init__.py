"""
MiroFish Backend - Fábrica de aplicaciones Flask
"""

import os
import json
import warnings

# Suprimir advertencias de resource_tracker de multiprocessing (provenientes de librerías de terceros como transformers)
# Debe configurarse antes de todas las demás importaciones
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Función de fábrica de aplicaciones Flask"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Configurar codificación JSON: asegurar que el texto se muestre directamente (en lugar del formato \uXXXX)
    # Flask >= 2.3 usa app.json.ensure_ascii, versiones anteriores usan la configuración JSON_AS_ASCII
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # Configurar logging
    logger = setup_logger('mirofish')
    
    # Solo imprimir información de inicio en el subproceso del reloader (evitar doble impresión en modo debug)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Iniciando MiroFish Backend...")
        logger.info("=" * 50)
    
    # Habilitar CORS solo para orígenes configurados
    CORS(app, resources={r"/api/*": {"origins": app.config.get('CORS_ORIGINS', [])}})
    
    # Registrar función de limpieza de procesos de simulación (asegurar que todos los procesos de simulación terminen al cerrar el servidor)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Función de limpieza de procesos de simulación registrada")
    
    # Middleware de logging de solicitudes
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"Solicitud: {request.method} {request.path}")
        if app.config.get('DEBUG') and request.content_type and 'json' in request.content_type:
            logger.debug(f"Cuerpo de solicitud: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        if not app.config.get('DEBUG') and response.is_json:
            data = response.get_json(silent=True)
            if isinstance(data, dict) and "traceback" in data:
                data.pop("traceback", None)
                response.set_data(json.dumps(data, ensure_ascii=False))
                response.content_length = len(response.get_data())
        logger.debug(f"Respuesta: {response.status_code}")
        return response
    
    # Registrar blueprints
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    
    # Verificación de salud
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}
    
    if should_log_startup:
        logger.info("MiroFish Backend iniciado correctamente")
    
    return app
