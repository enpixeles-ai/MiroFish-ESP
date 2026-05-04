"""
Script preconfigurado de simulación OASIS en paralelo para dos plataformas
Ejecuta simultáneamente las simulaciones de Twitter y Reddit, leyendo el mismo archivo de configuración

Características:
- Simulación paralela en dos plataformas (Twitter + Reddit)
- No cierra el entorno inmediatamente al terminar la simulación, entra en modo de espera de comandos
- Soporta recibir comandos de Interview mediante IPC
- Soporta entrevistas a un solo Agent y entrevistas en lote
- Soporta comando de cierre remoto del entorno

Uso:
    python run_parallel_simulation.py --config simulation_config.json
    python run_parallel_simulation.py --config simulation_config.json --no-wait  # Cierra inmediatamente al terminar
    python run_parallel_simulation.py --config simulation_config.json --twitter-only
    python run_parallel_simulation.py --config simulation_config.json --reddit-only

Estructura de logs:
    sim_xxx/
    ├── twitter/
    │   └── actions.jsonl    # Log de acciones de la plataforma Twitter
    ├── reddit/
    │   └── actions.jsonl    # Log de acciones de la plataforma Reddit
    ├── simulation.log       # Log del proceso de simulación principal
    └── run_state.json       # Estado de ejecución (para consultas por API)
"""

# ============================================================
# Solucionar problemas de codificación en Windows: configurar UTF-8 antes de todos los imports
# Esto es para corregir el problema de OASIS al leer archivos sin especificar codificación en librerías de terceros
# ============================================================
import sys
import os

if sys.platform == 'win32':
    # Configurar la codificación predeterminada de Python I/O como UTF-8
    # Esto afecta todas las llamadas a open() que no especifiquen codificación
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    # Reconfigurar los flujos de salida estándar a UTF-8 (soluciona caracteres incorrectos en consola)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # Forzar la codificación predeterminada (afecta la codificación predeterminada de la función open())
    # Nota: esto debe configurarse al iniciar Python; configurarlo en tiempo de ejecución puede no surtir efecto
    # Por eso también necesitamos hacer monkey-patch a la función open() integrada
    import builtins
    _original_open = builtins.open

    def _utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None,
                   newline=None, closefd=True, opener=None):
        """
        Envuelve la función open() para usar UTF-8 como codificación predeterminada en modo texto
        Esto puede corregir el problema de librerías de terceros (como OASIS) que leen archivos sin especificar codificación
        """
        # Solo establecer codificación predeterminada para modo texto (no binario) cuando no se especifica codificación
        if encoding is None and 'b' not in mode:
            encoding = 'utf-8'
        return _original_open(file, mode, buffering, encoding, errors,
                              newline, closefd, opener)

    builtins.open = _utf8_open

import argparse
import asyncio
import json
import logging
import multiprocessing
import random
import signal
import sqlite3
import warnings
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


# Variable global: para el manejo de señales
_shutdown_event = None
_cleanup_done = False

# Añadir el directorio backend al path
# El script está siempre ubicado en el directorio backend/scripts/
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# Cargar el archivo .env del directorio raíz del proyecto (contiene LLM_API_KEY y otras configuraciones)
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
    print(f"Configuración de entorno cargada: {_env_file}")
else:
    # Intentar cargar backend/.env
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
        print(f"Configuración de entorno cargada: {_backend_env}")


class MaxTokensWarningFilter(logging.Filter):
    """Filtra las advertencias de camel-ai sobre max_tokens (intencionalmente no configuramos max_tokens, dejamos que el modelo lo decida)"""

    def filter(self, record):
        # Filtrar logs que contengan advertencias de max_tokens
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# Añadir el filtro inmediatamente al cargar el módulo, para que surta efecto antes de que se ejecute el código de camel
logging.getLogger().addFilter(MaxTokensWarningFilter())


def disable_oasis_logging():
    """
    Deshabilitar el logging detallado de la librería OASIS
    Los logs de OASIS son demasiado redundantes (registran la observación y acción de cada agent); usamos nuestro propio action_logger
    """
    # Deshabilitar todos los loggers de OASIS
    oasis_loggers = [
        "social.agent",
        "social.twitter",
        "social.rec",
        "oasis.env",
        "table",
    ]

    for logger_name in oasis_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)  # Solo registrar errores críticos
        logger.handlers.clear()
        logger.propagate = False


def init_logging_for_simulation(simulation_dir: str):
    """
    Inicializar la configuración de logging para la simulación

    Args:
        simulation_dir: Ruta del directorio de simulación
    """
    # Deshabilitar el logging detallado de OASIS
    disable_oasis_logging()

    # Limpiar el directorio de logs antiguo (si existe)
    old_log_dir = os.path.join(simulation_dir, "log")
    if os.path.exists(old_log_dir):
        import shutil
        shutil.rmtree(old_log_dir, ignore_errors=True)


from action_logger import SimulationLogManager, PlatformActionLogger

try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph,
        generate_reddit_agent_graph
    )
    from oasis_runtime_limits import configure_camel_runtime_limits

    configure_camel_runtime_limits()
except ImportError as e:
    print(f"Error: dependencia faltante {e}")
    print("Por favor instala primero: pip install oasis-ai camel-ai")
    sys.exit(1)


# Acciones disponibles en Twitter (no incluye INTERVIEW, INTERVIEW solo puede activarse manualmente mediante ManualAction)
TWITTER_ACTIONS = [
    ActionType.CREATE_POST,
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.FOLLOW,
    ActionType.DO_NOTHING,
    ActionType.QUOTE_POST,
]

# Acciones disponibles en Reddit (no incluye INTERVIEW, INTERVIEW solo puede activarse manualmente mediante ManualAction)
REDDIT_ACTIONS = [
    ActionType.LIKE_POST,
    ActionType.DISLIKE_POST,
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_COMMENT,
    ActionType.DISLIKE_COMMENT,
    ActionType.SEARCH_POSTS,
    ActionType.SEARCH_USER,
    ActionType.TREND,
    ActionType.REFRESH,
    ActionType.DO_NOTHING,
    ActionType.FOLLOW,
    ActionType.MUTE,
]


# Constantes relacionadas con IPC
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

class CommandType:
    """Constantes de tipos de comando"""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class ParallelIPCHandler:
    """
    Manejador de comandos IPC para dos plataformas

    Gestiona los entornos de ambas plataformas y procesa comandos de Interview
    """

    def __init__(
        self,
        simulation_dir: str,
        twitter_env=None,
        twitter_agent_graph=None,
        reddit_env=None,
        reddit_agent_graph=None
    ):
        self.simulation_dir = simulation_dir
        self.twitter_env = twitter_env
        self.twitter_agent_graph = twitter_agent_graph
        self.reddit_env = reddit_env
        self.reddit_agent_graph = reddit_agent_graph

        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)

        # Asegurar que los directorios existan
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

    def update_status(self, status: str):
        """Actualizar el estado del entorno"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "twitter_available": self.twitter_env is not None,
                "reddit_available": self.reddit_env is not None,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

    def poll_command(self) -> Optional[Dict[str, Any]]:
        """Obtener comandos pendientes mediante polling"""
        if not os.path.exists(self.commands_dir):
            return None

        # Obtener archivos de comando (ordenados por tiempo)
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))

        command_files.sort(key=lambda x: x[1])

        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

        return None

    def send_response(self, command_id: str, status: str, result: Dict = None, error: str = None):
        """Enviar respuesta"""
        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }

        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)

        # Eliminar el archivo de comando
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass

    def _get_env_and_graph(self, platform: str):
        """
        Obtener el entorno y agent_graph de la plataforma especificada

        Args:
            platform: Nombre de la plataforma ("twitter" o "reddit")

        Returns:
            (env, agent_graph, platform_name) o (None, None, None)
        """
        if platform == "twitter" and self.twitter_env:
            return self.twitter_env, self.twitter_agent_graph, "twitter"
        elif platform == "reddit" and self.reddit_env:
            return self.reddit_env, self.reddit_agent_graph, "reddit"
        else:
            return None, None, None

    async def _interview_single_platform(self, agent_id: int, prompt: str, platform: str) -> Dict[str, Any]:
        """
        Ejecutar un Interview en una sola plataforma

        Returns:
            Diccionario con el resultado, o diccionario con error
        """
        env, agent_graph, actual_platform = self._get_env_and_graph(platform)

        if not env or not agent_graph:
            return {"platform": platform, "error": f"Plataforma {platform} no disponible"}

        try:
            agent = agent_graph.get_agent(agent_id)
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            actions = {agent: interview_action}
            await env.step(actions)

            result = self._get_interview_result(agent_id, actual_platform)
            result["platform"] = actual_platform
            return result

        except Exception as e:
            return {"platform": platform, "error": str(e)}

    async def handle_interview(self, command_id: str, agent_id: int, prompt: str, platform: str = None) -> bool:
        """
        Procesar comando de entrevista a un solo Agent

        Args:
            command_id: ID del comando
            agent_id: ID del Agent
            prompt: Pregunta de la entrevista
            platform: Plataforma específica (opcional)
                - "twitter": Solo entrevistar en la plataforma Twitter
                - "reddit": Solo entrevistar en la plataforma Reddit
                - None/no especificado: Entrevistar en ambas plataformas simultáneamente y devolver resultado integrado

        Returns:
            True indica éxito, False indica fallo
        """
        # Si se especificó una plataforma, solo entrevistar en esa plataforma
        if platform in ("twitter", "reddit"):
            result = await self._interview_single_platform(agent_id, prompt, platform)

            if "error" in result:
                self.send_response(command_id, "failed", error=result["error"])
                print(f"  Interview fallido: agent_id={agent_id}, platform={platform}, error={result['error']}")
                return False
            else:
                self.send_response(command_id, "completed", result=result)
                print(f"  Interview completado: agent_id={agent_id}, platform={platform}")
                return True

        # Plataforma no especificada: entrevistar en ambas plataformas simultáneamente
        if not self.twitter_env and not self.reddit_env:
            self.send_response(command_id, "failed", error="No hay entornos de simulación disponibles")
            return False

        results = {
            "agent_id": agent_id,
            "prompt": prompt,
            "platforms": {}
        }
        success_count = 0

        # Entrevistar en paralelo en ambas plataformas
        tasks = []
        platforms_to_interview = []

        if self.twitter_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "twitter"))
            platforms_to_interview.append("twitter")

        if self.reddit_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "reddit"))
            platforms_to_interview.append("reddit")

        # Ejecución en paralelo
        platform_results = await asyncio.gather(*tasks)

        for platform_name, platform_result in zip(platforms_to_interview, platform_results):
            results["platforms"][platform_name] = platform_result
            if "error" not in platform_result:
                success_count += 1

        if success_count > 0:
            self.send_response(command_id, "completed", result=results)
            print(f"  Interview completado: agent_id={agent_id}, plataformas exitosas={success_count}/{len(platforms_to_interview)}")
            return True
        else:
            errors = [f"{p}: {r.get('error', 'error desconocido')}" for p, r in results["platforms"].items()]
            self.send_response(command_id, "failed", error="; ".join(errors))
            print(f"  Interview fallido: agent_id={agent_id}, todas las plataformas fallaron")
            return False

    async def handle_batch_interview(self, command_id: str, interviews: List[Dict], platform: str = None) -> bool:
        """
        Procesar comando de entrevista en lote

        Args:
            command_id: ID del comando
            interviews: [{"agent_id": int, "prompt": str, "platform": str(opcional)}, ...]
            platform: Plataforma predeterminada (puede ser sobreescrita por cada elemento de interview)
                - "twitter": Solo entrevistar en la plataforma Twitter
                - "reddit": Solo entrevistar en la plataforma Reddit
                - None/no especificado: Cada Agent es entrevistado en ambas plataformas
        """
        # Agrupar por plataforma
        twitter_interviews = []
        reddit_interviews = []
        both_platforms_interviews = []  # Los que necesitan ser entrevistados en ambas plataformas

        for interview in interviews:
            item_platform = interview.get("platform", platform)
            if item_platform == "twitter":
                twitter_interviews.append(interview)
            elif item_platform == "reddit":
                reddit_interviews.append(interview)
            else:
                # Plataforma no especificada: entrevistar en ambas plataformas
                both_platforms_interviews.append(interview)

        # Distribuir both_platforms_interviews en las dos plataformas
        if both_platforms_interviews:
            if self.twitter_env:
                twitter_interviews.extend(both_platforms_interviews)
            if self.reddit_env:
                reddit_interviews.extend(both_platforms_interviews)

        results = {}

        # Procesar entrevistas en la plataforma Twitter
        if twitter_interviews and self.twitter_env:
            try:
                twitter_actions = {}
                for interview in twitter_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.twitter_agent_graph.get_agent(agent_id)
                        twitter_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Advertencia: no se pudo obtener el Agent de Twitter {agent_id}: {e}")

                if twitter_actions:
                    await self.twitter_env.step(twitter_actions)

                    for interview in twitter_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "twitter")
                        result["platform"] = "twitter"
                        results[f"twitter_{agent_id}"] = result
            except Exception as e:
                print(f"  Interview en lote de Twitter fallido: {e}")

        # Procesar entrevistas en la plataforma Reddit
        if reddit_interviews and self.reddit_env:
            try:
                reddit_actions = {}
                for interview in reddit_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.reddit_agent_graph.get_agent(agent_id)
                        reddit_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Advertencia: no se pudo obtener el Agent de Reddit {agent_id}: {e}")

                if reddit_actions:
                    await self.reddit_env.step(reddit_actions)

                    for interview in reddit_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "reddit")
                        result["platform"] = "reddit"
                        results[f"reddit_{agent_id}"] = result
            except Exception as e:
                print(f"  Interview en lote de Reddit fallido: {e}")

        if results:
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  Interview en lote completado: {len(results)} Agents")
            return True
        else:
            self.send_response(command_id, "failed", error="No hubo entrevistas exitosas")
            return False

    def _get_interview_result(self, agent_id: int, platform: str) -> Dict[str, Any]:
        """Obtener el resultado más reciente del Interview desde la base de datos"""
        db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")

        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }

        if not os.path.exists(db_path):
            return result

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Consultar el registro de Interview más reciente
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))

            row = cursor.fetchone()
            if row:
                user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json

            conn.close()

        except Exception as e:
            print(f"  Error al leer el resultado del Interview: {e}")

        return result

    async def process_commands(self) -> bool:
        """
        Procesar todos los comandos pendientes

        Returns:
            True indica continuar ejecutando, False indica que se debe salir
        """
        command = self.poll_command()
        if not command:
            return True

        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})

        print(f"\nComando IPC recibido: {command_type}, id={command_id}")

        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", ""),
                args.get("platform")
            )
            return True

        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", []),
                args.get("platform")
            )
            return True

        elif command_type == CommandType.CLOSE_ENV:
            print("Comando de cierre de entorno recibido")
            self.send_response(command_id, "completed", result={"message": "El entorno está por cerrarse"})
            return False

        else:
            self.send_response(command_id, "failed", error=f"Tipo de comando desconocido: {command_type}")
            return True


def load_config(config_path: str) -> Dict[str, Any]:
    """Cargar el archivo de configuración"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# Tipos de acciones no esenciales a filtrar (estas acciones tienen poco valor analítico)
FILTERED_ACTIONS = {'refresh', 'sign_up'}

# Tabla de mapeo de tipos de acción (nombre en la base de datos -> nombre estándar)
ACTION_TYPE_MAP = {
    'create_post': 'CREATE_POST',
    'like_post': 'LIKE_POST',
    'dislike_post': 'DISLIKE_POST',
    'repost': 'REPOST',
    'quote_post': 'QUOTE_POST',
    'follow': 'FOLLOW',
    'mute': 'MUTE',
    'create_comment': 'CREATE_COMMENT',
    'like_comment': 'LIKE_COMMENT',
    'dislike_comment': 'DISLIKE_COMMENT',
    'search_posts': 'SEARCH_POSTS',
    'search_user': 'SEARCH_USER',
    'trend': 'TREND',
    'do_nothing': 'DO_NOTHING',
    'interview': 'INTERVIEW',
}


def get_agent_names_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """
    Obtener el mapeo agent_id -> entity_name desde simulation_config

    Esto permite mostrar el nombre real de la entidad en actions.jsonl, en lugar de códigos como "Agent_0"

    Args:
        config: Contenido de simulation_config.json

    Returns:
        Diccionario de mapeo agent_id -> entity_name
    """
    agent_names = {}
    agent_configs = config.get("agent_configs", [])

    for agent_config in agent_configs:
        agent_id = agent_config.get("agent_id")
        entity_name = agent_config.get("entity_name", f"Agent_{agent_id}")
        if agent_id is not None:
            agent_names[agent_id] = entity_name

    return agent_names


def fetch_new_actions_from_db(
    db_path: str,
    last_rowid: int,
    agent_names: Dict[int, str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Obtener nuevos registros de acciones desde la base de datos y complementar con información de contexto completa

    Args:
        db_path: Ruta del archivo de base de datos
        last_rowid: Máximo rowid leído la última vez (se usa rowid en lugar de created_at porque el formato de created_at varía entre plataformas)
        agent_names: Mapeo agent_id -> agent_name

    Returns:
        (actions_list, new_last_rowid)
        - actions_list: Lista de acciones, cada elemento contiene agent_id, agent_name, action_type, action_args (con información de contexto)
        - new_last_rowid: Nuevo valor máximo de rowid
    """
    actions = []
    new_last_rowid = last_rowid

    if not os.path.exists(db_path):
        return actions, new_last_rowid

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Usar rowid para rastrear los registros ya procesados (rowid es el campo autoincremental integrado de SQLite)
        # Esto evita problemas con diferencias de formato en created_at (Twitter usa entero, Reddit usa cadena de fecha y hora)
        cursor.execute("""
            SELECT rowid, user_id, action, info
            FROM trace
            WHERE rowid > ?
            ORDER BY rowid ASC
        """, (last_rowid,))

        for rowid, user_id, action, info_json in cursor.fetchall():
            # Actualizar el máximo rowid
            new_last_rowid = rowid

            # Filtrar acciones no esenciales
            if action in FILTERED_ACTIONS:
                continue

            # Analizar los argumentos de la acción
            try:
                action_args = json.loads(info_json) if info_json else {}
            except json.JSONDecodeError:
                action_args = {}

            # Simplificar action_args, conservar solo los campos clave (mantener contenido completo sin truncar)
            simplified_args = {}
            if 'content' in action_args:
                simplified_args['content'] = action_args['content']
            if 'post_id' in action_args:
                simplified_args['post_id'] = action_args['post_id']
            if 'comment_id' in action_args:
                simplified_args['comment_id'] = action_args['comment_id']
            if 'quoted_id' in action_args:
                simplified_args['quoted_id'] = action_args['quoted_id']
            if 'new_post_id' in action_args:
                simplified_args['new_post_id'] = action_args['new_post_id']
            if 'follow_id' in action_args:
                simplified_args['follow_id'] = action_args['follow_id']
            if 'query' in action_args:
                simplified_args['query'] = action_args['query']
            if 'like_id' in action_args:
                simplified_args['like_id'] = action_args['like_id']
            if 'dislike_id' in action_args:
                simplified_args['dislike_id'] = action_args['dislike_id']

            # Convertir el nombre del tipo de acción
            action_type = ACTION_TYPE_MAP.get(action, action.upper())

            # Complementar con información de contexto (contenido de publicaciones, nombres de usuario, etc.)
            _enrich_action_context(cursor, action_type, simplified_args, agent_names)

            actions.append({
                'agent_id': user_id,
                'agent_name': agent_names.get(user_id, f'Agent_{user_id}'),
                'action_type': action_type,
                'action_args': simplified_args,
            })

        conn.close()
    except Exception as e:
        print(f"Error al leer acciones de la base de datos: {e}")

    return actions, new_last_rowid


def _enrich_action_context(
    cursor,
    action_type: str,
    action_args: Dict[str, Any],
    agent_names: Dict[int, str]
) -> None:
    """
    Complementar la acción con información de contexto (contenido de publicaciones, nombres de usuario, etc.)

    Args:
        cursor: Cursor de la base de datos
        action_type: Tipo de acción
        action_args: Argumentos de la acción (serán modificados)
        agent_names: Mapeo agent_id -> agent_name
    """
    try:
        # Me gusta/no me gusta en publicación: complementar con contenido y autor
        if action_type in ('LIKE_POST', 'DISLIKE_POST'):
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')

        # Repostear publicación: complementar con contenido y autor originales
        elif action_type == 'REPOST':
            new_post_id = action_args.get('new_post_id')
            if new_post_id:
                # El original_post_id del post reposteado apunta al post original
                cursor.execute("""
                    SELECT original_post_id FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    original_post_id = row[0]
                    original_info = _get_post_info(cursor, original_post_id, agent_names)
                    if original_info:
                        action_args['original_content'] = original_info.get('content', '')
                        action_args['original_author_name'] = original_info.get('author_name', '')

        # Citar publicación: complementar con contenido original, autor y comentario de cita
        elif action_type == 'QUOTE_POST':
            quoted_id = action_args.get('quoted_id')
            new_post_id = action_args.get('new_post_id')

            if quoted_id:
                original_info = _get_post_info(cursor, quoted_id, agent_names)
                if original_info:
                    action_args['original_content'] = original_info.get('content', '')
                    action_args['original_author_name'] = original_info.get('author_name', '')

            # Obtener el contenido del comentario de cita (quote_content)
            if new_post_id:
                cursor.execute("""
                    SELECT quote_content FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    action_args['quote_content'] = row[0]

        # Seguir usuario: complementar con el nombre del usuario seguido
        elif action_type == 'FOLLOW':
            follow_id = action_args.get('follow_id')
            if follow_id:
                # Obtener followee_id desde la tabla follow
                cursor.execute("""
                    SELECT followee_id FROM follow WHERE follow_id = ?
                """, (follow_id,))
                row = cursor.fetchone()
                if row:
                    followee_id = row[0]
                    target_name = _get_user_name(cursor, followee_id, agent_names)
                    if target_name:
                        action_args['target_user_name'] = target_name

        # Silenciar usuario: complementar con el nombre del usuario silenciado
        elif action_type == 'MUTE':
            # Obtener user_id o target_id desde action_args
            target_id = action_args.get('user_id') or action_args.get('target_id')
            if target_id:
                target_name = _get_user_name(cursor, target_id, agent_names)
                if target_name:
                    action_args['target_user_name'] = target_name

        # Me gusta/no me gusta en comentario: complementar con contenido y autor del comentario
        elif action_type in ('LIKE_COMMENT', 'DISLIKE_COMMENT'):
            comment_id = action_args.get('comment_id')
            if comment_id:
                comment_info = _get_comment_info(cursor, comment_id, agent_names)
                if comment_info:
                    action_args['comment_content'] = comment_info.get('content', '')
                    action_args['comment_author_name'] = comment_info.get('author_name', '')

        # Publicar comentario: complementar con información del post comentado
        elif action_type == 'CREATE_COMMENT':
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')

    except Exception as e:
        # El fallo al complementar contexto no afecta el flujo principal
        print(f"Error al complementar contexto de acción: {e}")


def _get_post_info(
    cursor,
    post_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Obtener información de una publicación

    Args:
        cursor: Cursor de la base de datos
        post_id: ID de la publicación
        agent_names: Mapeo agent_id -> agent_name

    Returns:
        Diccionario con content y author_name, o None
    """
    try:
        cursor.execute("""
            SELECT p.content, p.user_id, u.agent_id
            FROM post p
            LEFT JOIN user u ON p.user_id = u.user_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]

            # Usar preferentemente el nombre en agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Obtener el nombre desde la tabla user
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''

            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def _get_user_name(
    cursor,
    user_id: int,
    agent_names: Dict[int, str]
) -> Optional[str]:
    """
    Obtener el nombre de un usuario

    Args:
        cursor: Cursor de la base de datos
        user_id: ID del usuario
        agent_names: Mapeo agent_id -> agent_name

    Returns:
        Nombre del usuario, o None
    """
    try:
        cursor.execute("""
            SELECT agent_id, name, user_name FROM user WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            agent_id = row[0]
            name = row[1]
            user_name = row[2]

            # Usar preferentemente el nombre en agent_names
            if agent_id is not None and agent_id in agent_names:
                return agent_names[agent_id]
            return name or user_name or ''
    except Exception:
        pass
    return None


def _get_comment_info(
    cursor,
    comment_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Obtener información de un comentario

    Args:
        cursor: Cursor de la base de datos
        comment_id: ID del comentario
        agent_names: Mapeo agent_id -> agent_name

    Returns:
        Diccionario con content y author_name, o None
    """
    try:
        cursor.execute("""
            SELECT c.content, c.user_id, u.agent_id
            FROM comment c
            LEFT JOIN user u ON c.user_id = u.user_id
            WHERE c.comment_id = ?
        """, (comment_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]

            # Usar preferentemente el nombre en agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Obtener el nombre desde la tabla user
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''

            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def create_model(config: Dict[str, Any], use_boost: bool = False):
    """
    Crear el modelo LLM

    Soporta configuración dual de LLM para acelerar la simulación paralela:
    - Configuración general: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
    - Configuración de aceleración (opcional): LLM_BOOST_API_KEY, LLM_BOOST_BASE_URL, LLM_BOOST_MODEL_NAME

    Si se configura un LLM de aceleración, la simulación paralela puede usar diferentes proveedores de API para distintas plataformas, mejorando la concurrencia.

    Args:
        config: Diccionario de configuración de la simulación
        use_boost: Si se debe usar la configuración LLM de aceleración (si está disponible)
    """
    # Verificar si existe configuración de aceleración
    boost_api_key = os.environ.get("LLM_BOOST_API_KEY", "")
    boost_base_url = os.environ.get("LLM_BOOST_BASE_URL", "")
    boost_model = os.environ.get("LLM_BOOST_MODEL_NAME", "")
    has_boost_config = bool(boost_api_key)

    # Seleccionar qué LLM usar según el parámetro y la configuración disponible
    if use_boost and has_boost_config:
        # Usar configuración de aceleración
        llm_api_key = boost_api_key
        llm_base_url = boost_base_url
        llm_model = boost_model or os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[LLM acelerado]"
    else:
        # Usar configuración general
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[LLM general]"

    # Si no hay nombre de modelo en .env, usar config como respaldo
    if not llm_model:
        llm_model = config.get("llm_model", "gpt-4o-mini")

    # Configurar las variables de entorno requeridas por camel-ai
    if llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key

    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Falta la configuración de API Key. Por favor configura LLM_API_KEY en el archivo .env del directorio raíz del proyecto")

    if llm_base_url:
        os.environ["OPENAI_API_BASE_URL"] = llm_base_url

    print(f"{config_label} model={llm_model}, base_url={llm_base_url[:40] if llm_base_url else 'predeterminado'}...")

    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=llm_model,
    )


def get_active_agents_for_round(
    env,
    config: Dict[str, Any],
    current_hour: int,
    round_num: int
) -> List:
    """Decidir qué Agents activar en esta ronda según el tiempo y la configuración"""
    time_config = config.get("time_config", {})
    agent_configs = config.get("agent_configs", [])

    base_min = time_config.get("agents_per_hour_min", 5)
    base_max = time_config.get("agents_per_hour_max", 20)

    peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])

    if current_hour in peak_hours:
        multiplier = time_config.get("peak_activity_multiplier", 1.5)
    elif current_hour in off_peak_hours:
        multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
    else:
        multiplier = 1.0

    target_count = int(random.uniform(base_min, base_max) * multiplier)

    candidates = []
    for cfg in agent_configs:
        agent_id = cfg.get("agent_id", 0)
        active_hours = cfg.get("active_hours", list(range(8, 23)))
        activity_level = cfg.get("activity_level", 0.5)

        if current_hour not in active_hours:
            continue

        if random.random() < activity_level:
            candidates.append(agent_id)

    selected_ids = random.sample(
        candidates,
        min(target_count, len(candidates))
    ) if candidates else []

    active_agents = []
    for agent_id in selected_ids:
        try:
            agent = env.agent_graph.get_agent(agent_id)
            active_agents.append((agent_id, agent))
        except Exception:
            pass

    return active_agents


class PlatformSimulation:
    """Contenedor de resultados de simulación de plataforma"""
    def __init__(self):
        self.env = None
        self.agent_graph = None
        self.total_actions = 0
        self.failed = False
        self.error = None


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def get_oasis_semaphore(platform: str) -> int:
    default_limit = _env_int("MIROFISH_OASIS_SEMAPHORE", 8, minimum=1)
    return _env_int(f"MIROFISH_{platform.upper()}_SEMAPHORE", default_limit, minimum=1)


def compact_error(exc: Exception, max_chars: int = 900) -> str:
    error = str(exc) or exc.__class__.__name__
    error = " ".join(error.split())
    return error[:max_chars] + ("..." if len(error) > max_chars else "")


async def run_env_step_with_retries(
    env,
    actions,
    platform: str,
    round_num: int,
    action_logger: Optional[PlatformActionLogger],
    main_logger: Optional[SimulationLogManager],
) -> tuple[bool, Optional[str]]:
    max_attempts = _env_int("MIROFISH_ROUND_STEP_ATTEMPTS", 2, minimum=1)

    for attempt in range(1, max_attempts + 1):
        try:
            await env.step(actions)
            return True, None
        except Exception as exc:
            error = compact_error(exc)
            if main_logger:
                main_logger.warning(
                    f"[{platform}] Error en ronda {round_num}, intento {attempt}/{max_attempts}: {error}"
                )
            if action_logger:
                action_logger.log_event(
                    "round_error",
                    round=round_num,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=error,
                )
            if attempt < max_attempts:
                await asyncio.sleep(min(2 * attempt, 8))

    return False, error


async def run_platform_safely(
    platform: str,
    action_logger: Optional[PlatformActionLogger],
    main_logger: Optional[SimulationLogManager],
    coro,
) -> PlatformSimulation:
    try:
        return await coro
    except Exception as exc:
        result = PlatformSimulation()
        result.failed = True
        result.error = compact_error(exc)
        if main_logger:
            main_logger.error(f"[{platform}] Simulación fallida: {result.error}")
        if action_logger:
            action_logger.log_event("simulation_error", error=result.error)
        return result


async def run_parallel_platforms(
    config: Dict[str, Any],
    simulation_dir: str,
    twitter_logger: PlatformActionLogger,
    reddit_logger: PlatformActionLogger,
    main_logger: SimulationLogManager,
    max_rounds: Optional[int],
) -> tuple[Optional[PlatformSimulation], Optional[PlatformSimulation]]:
    tasks = {
        asyncio.create_task(
            run_platform_safely(
                "Twitter",
                twitter_logger,
                main_logger,
                run_twitter_simulation(config, simulation_dir, twitter_logger, main_logger, max_rounds),
            )
        ): "twitter",
        asyncio.create_task(
            run_platform_safely(
                "Reddit",
                reddit_logger,
                main_logger,
                run_reddit_simulation(config, simulation_dir, reddit_logger, main_logger, max_rounds),
            )
        ): "reddit",
    }

    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None
    pending = set(tasks.keys())

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        should_cancel_pending = False

        for task in done:
            platform = tasks[task]
            try:
                result = task.result()
            except Exception as exc:
                result = PlatformSimulation()
                result.failed = True
                result.error = compact_error(exc)
                main_logger.error(f"[{platform}] Tarea de plataforma fallida: {result.error}")

            if platform == "twitter":
                twitter_result = result
            else:
                reddit_result = result

            if result and result.failed:
                should_cancel_pending = True

        if should_cancel_pending and pending:
            for task in pending:
                pending_platform = tasks[task]
                main_logger.warning(
                    f"[{pending_platform}] Cancelando plataforma pendiente porque otra plataforma falló"
                )
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            pending.clear()

    return twitter_result, reddit_result


async def run_twitter_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Ejecutar la simulación Twitter

    Args:
        config: Configuración de la simulación
        simulation_dir: Directorio de la simulación
        action_logger: Registrador de acciones
        main_logger: Gestor del log principal
        max_rounds: Número máximo de rondas de simulación (opcional, para limitar simulaciones largas)

    Returns:
        PlatformSimulation: Objeto resultado que contiene env y agent_graph
    """
    result = PlatformSimulation()

    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Twitter] {msg}")
        print(f"[Twitter] {msg}")

    log_info("Inicializando...")

    # Twitter usa la configuración LLM general
    model = create_model(config, use_boost=False)

    # OASIS Twitter usa formato CSV
    profile_path = os.path.join(simulation_dir, "twitter_profiles.csv")
    if not os.path.exists(profile_path):
        log_info(f"Error: el archivo de perfil no existe: {profile_path}")
        return result

    result.agent_graph = await generate_twitter_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=TWITTER_ACTIONS,
    )

    # Obtener el mapeo de nombres reales de Agents desde el archivo de configuración (usar entity_name en lugar del Agent_X predeterminado)
    agent_names = get_agent_names_from_config(config)
    # Si no se encuentra algún agent en la configuración, usar el nombre predeterminado de OASIS
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "twitter_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    semaphore = get_oasis_semaphore("twitter")
    log_info(f"Concurrencia OASIS: {semaphore}")
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        semaphore=semaphore,
    )

    await result.env.reset()
    log_info("Entorno iniciado")

    if action_logger:
        action_logger.log_simulation_start(config)

    total_actions = 0
    last_rowid = 0  # Rastrear la última fila procesada en la base de datos (usar rowid para evitar diferencias de formato en created_at)

    # Ejecutar eventos iniciales
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])

    # Registrar inicio de round 0 (fase de eventos iniciales)
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0

    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                initial_actions[agent] = ManualAction(
                    action_type=ActionType.CREATE_POST,
                    action_args={"content": content}
                )

                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass

        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"Se publicaron {len(initial_actions)} publicaciones iniciales")

    # Registrar fin de round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)

    # Bucle principal de simulación
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round

    # Si se especificó máximo de rondas, limitar
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Rondas limitadas: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

    start_time = datetime.now()
    max_consecutive_failures = _env_int(
        "MIROFISH_MAX_CONSECUTIVE_ROUND_FAILURES", 3, minimum=1
    )
    consecutive_failures = 0
    platform_error = None

    for round_num in range(total_rounds):
        # Verificar si se recibió señal de salida
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Señal de salida recibida, deteniendo simulación en la ronda {round_num + 1}")
            break

        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )

        # Registrar inicio de ronda independientemente de si hay agents activos
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)

        if not active_agents:
            # Registrar fin de ronda también cuando no hay agents activos (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        actions = {agent: LLMAction() for _, agent in active_agents}
        step_ok, step_error = await run_env_step_with_retries(
            result.env, actions, "Twitter", round_num + 1, action_logger, main_logger
        )
        if not step_ok:
            consecutive_failures += 1
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            if consecutive_failures >= max_consecutive_failures:
                platform_error = (
                    f"Twitter acumuló {consecutive_failures} rondas fallidas consecutivas. "
                    f"Último error: {step_error}"
                )
                break
            continue

        consecutive_failures = 0

        # Obtener las acciones realmente ejecutadas desde la base de datos y registrarlas
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )

        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)

        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")

    # Nota: no cerrar el entorno, conservarlo para Interview

    if platform_error:
        result.failed = True
        result.error = platform_error
        if main_logger:
            main_logger.error(f"[Twitter] {platform_error}")
        if action_logger:
            action_logger.log_event("simulation_error", error=platform_error, total_actions=total_actions)
    elif action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)

    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Bucle de simulación completado! Tiempo: {elapsed:.1f}s, acciones totales: {total_actions}")

    return result


async def run_reddit_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Ejecutar la simulación Reddit

    Args:
        config: Configuración de la simulación
        simulation_dir: Directorio de la simulación
        action_logger: Registrador de acciones
        main_logger: Gestor del log principal
        max_rounds: Número máximo de rondas de simulación (opcional, para limitar simulaciones largas)

    Returns:
        PlatformSimulation: Objeto resultado que contiene env y agent_graph
    """
    result = PlatformSimulation()

    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Reddit] {msg}")
        print(f"[Reddit] {msg}")

    log_info("Inicializando...")

    # Reddit usa la configuración LLM de aceleración (si está disponible, de lo contrario usa la configuración general)
    model = create_model(config, use_boost=True)

    profile_path = os.path.join(simulation_dir, "reddit_profiles.json")
    if not os.path.exists(profile_path):
        log_info(f"Error: el archivo de perfil no existe: {profile_path}")
        return result

    result.agent_graph = await generate_reddit_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=REDDIT_ACTIONS,
    )

    # Obtener el mapeo de nombres reales de Agents desde el archivo de configuración (usar entity_name en lugar del Agent_X predeterminado)
    agent_names = get_agent_names_from_config(config)
    # Si no se encuentra algún agent en la configuración, usar el nombre predeterminado de OASIS
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "reddit_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    semaphore = get_oasis_semaphore("reddit")
    log_info(f"Concurrencia OASIS: {semaphore}")
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=semaphore,
    )

    await result.env.reset()
    log_info("Entorno iniciado")

    if action_logger:
        action_logger.log_simulation_start(config)

    total_actions = 0
    last_rowid = 0  # Rastrear la última fila procesada en la base de datos (usar rowid para evitar diferencias de formato en created_at)

    # Ejecutar eventos iniciales
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])

    # Registrar inicio de round 0 (fase de eventos iniciales)
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0

    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )

                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass

        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"Se publicaron {len(initial_actions)} publicaciones iniciales")

    # Registrar fin de round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)

    # Bucle principal de simulación
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round

    # Si se especificó máximo de rondas, limitar
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Rondas limitadas: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

    start_time = datetime.now()
    max_consecutive_failures = _env_int(
        "MIROFISH_MAX_CONSECUTIVE_ROUND_FAILURES", 3, minimum=1
    )
    consecutive_failures = 0
    platform_error = None

    for round_num in range(total_rounds):
        # Verificar si se recibió señal de salida
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Señal de salida recibida, deteniendo simulación en la ronda {round_num + 1}")
            break

        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )

        # Registrar inicio de ronda independientemente de si hay agents activos
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)

        if not active_agents:
            # Registrar fin de ronda también cuando no hay agents activos (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        actions = {agent: LLMAction() for _, agent in active_agents}
        step_ok, step_error = await run_env_step_with_retries(
            result.env, actions, "Reddit", round_num + 1, action_logger, main_logger
        )
        if not step_ok:
            consecutive_failures += 1
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            if consecutive_failures >= max_consecutive_failures:
                platform_error = (
                    f"Reddit acumuló {consecutive_failures} rondas fallidas consecutivas. "
                    f"Último error: {step_error}"
                )
                break
            continue

        consecutive_failures = 0

        # Obtener las acciones realmente ejecutadas desde la base de datos y registrarlas
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )

        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)

        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")

    # Nota: no cerrar el entorno, conservarlo para Interview

    if platform_error:
        result.failed = True
        result.error = platform_error
        if main_logger:
            main_logger.error(f"[Reddit] {platform_error}")
        if action_logger:
            action_logger.log_event("simulation_error", error=platform_error, total_actions=total_actions)
    elif action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)

    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Bucle de simulación completado! Tiempo: {elapsed:.1f}s, acciones totales: {total_actions}")

    return result


async def main():
    parser = argparse.ArgumentParser(description='Simulación OASIS paralela en dos plataformas')
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='Ruta del archivo de configuración (simulation_config.json)'
    )
    parser.add_argument(
        '--twitter-only',
        action='store_true',
        help='Solo ejecutar la simulación Twitter'
    )
    parser.add_argument(
        '--reddit-only',
        action='store_true',
        help='Solo ejecutar la simulación Reddit'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='Número máximo de rondas de simulación (opcional, para limitar simulaciones largas)'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='Cerrar el entorno inmediatamente al terminar la simulación, sin entrar en modo de espera de comandos'
    )

    args = parser.parse_args()

    # Crear el evento shutdown al inicio de la función main para que todo el programa pueda responder a señales de salida
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    if not os.path.exists(args.config):
        print(f"Error: el archivo de configuración no existe: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."
    wait_for_commands = not args.no_wait

    # Inicializar la configuración de logging (deshabilitar logs de OASIS, limpiar archivos antiguos)
    init_logging_for_simulation(simulation_dir)

    # Crear el gestor de logs
    log_manager = SimulationLogManager(simulation_dir)
    twitter_logger = log_manager.get_twitter_logger()
    reddit_logger = log_manager.get_reddit_logger()

    log_manager.info("=" * 60)
    log_manager.info("Simulación OASIS paralela en dos plataformas")
    log_manager.info(f"Archivo de configuración: {args.config}")
    log_manager.info(f"ID de simulación: {config.get('simulation_id', 'unknown')}")
    log_manager.info(f"Modo de espera de comandos: {'habilitado' if wait_for_commands else 'deshabilitado'}")
    log_manager.info("=" * 60)

    time_config = config.get("time_config", {})
    total_hours = time_config.get('total_simulation_hours', 72)
    minutes_per_round = time_config.get('minutes_per_round', 30)
    config_total_rounds = (total_hours * 60) // minutes_per_round

    log_manager.info(f"Parámetros de simulación:")
    log_manager.info(f"  - Duración total de simulación: {total_hours} horas")
    log_manager.info(f"  - Tiempo por ronda: {minutes_per_round} minutos")
    log_manager.info(f"  - Total de rondas configurado: {config_total_rounds}")
    if args.max_rounds:
        log_manager.info(f"  - Límite máximo de rondas: {args.max_rounds}")
        if args.max_rounds < config_total_rounds:
            log_manager.info(f"  - Rondas a ejecutar realmente: {args.max_rounds} (limitado)")
    log_manager.info(f"  - Cantidad de Agents: {len(config.get('agent_configs', []))}")

    log_manager.info("Estructura de logs:")
    log_manager.info(f"  - Log principal: simulation.log")
    log_manager.info(f"  - Acciones Twitter: twitter/actions.jsonl")
    log_manager.info(f"  - Acciones Reddit: reddit/actions.jsonl")
    log_manager.info("=" * 60)

    start_time = datetime.now()

    # Almacenar los resultados de simulación de ambas plataformas
    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None

    if args.twitter_only:
        twitter_result = await run_platform_safely(
            "Twitter",
            twitter_logger,
            log_manager,
            run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds),
        )
    elif args.reddit_only:
        reddit_result = await run_platform_safely(
            "Reddit",
            reddit_logger,
            log_manager,
            run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds),
        )
    else:
        # Ejecución en paralelo (cada plataforma usa su propio registrador de logs)
        twitter_result, reddit_result = await run_parallel_platforms(
            config,
            simulation_dir,
            twitter_logger,
            reddit_logger,
            log_manager,
            args.max_rounds,
        )

    total_elapsed = (datetime.now() - start_time).total_seconds()
    log_manager.info("=" * 60)
    log_manager.info(f"Bucle de simulación completado! Tiempo total: {total_elapsed:.1f}s")

    platform_failed = any(
        result and result.failed for result in [twitter_result, reddit_result]
    )
    if platform_failed:
        wait_for_commands = False
        log_manager.warning(
            "Se omite el modo de espera de comandos porque una plataforma falló"
        )

    # Entrar en modo de espera de comandos si corresponde
    if wait_for_commands:
        log_manager.info("")
        log_manager.info("=" * 60)
        log_manager.info("Entrando en modo de espera de comandos - El entorno permanece activo")
        log_manager.info("Comandos soportados: interview, batch_interview, close_env")
        log_manager.info("=" * 60)

        # Crear el manejador IPC
        ipc_handler = ParallelIPCHandler(
            simulation_dir=simulation_dir,
            twitter_env=twitter_result.env if twitter_result else None,
            twitter_agent_graph=twitter_result.agent_graph if twitter_result else None,
            reddit_env=reddit_result.env if reddit_result else None,
            reddit_agent_graph=reddit_result.agent_graph if reddit_result else None
        )
        ipc_handler.update_status("alive")

        # Bucle de espera de comandos (usando el _shutdown_event global)
        try:
            while not _shutdown_event.is_set():
                should_continue = await ipc_handler.process_commands()
                if not should_continue:
                    break
                # Usar wait_for en lugar de sleep para poder responder al shutdown_event
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                    break  # Señal de salida recibida
                except asyncio.TimeoutError:
                    pass  # Tiempo agotado, continuar el bucle
        except KeyboardInterrupt:
            print("\nSeñal de interrupción recibida")
        except asyncio.CancelledError:
            print("\nTarea cancelada")
        except Exception as e:
            print(f"\nError al procesar comandos: {e}")

        log_manager.info("\nCerrando entorno...")
        ipc_handler.update_status("stopped")

    # Cerrar los entornos
    if twitter_result and twitter_result.env:
        await twitter_result.env.close()
        log_manager.info("[Twitter] Entorno cerrado")

    if reddit_result and reddit_result.env:
        await reddit_result.env.close()
        log_manager.info("[Reddit] Entorno cerrado")

    log_manager.info("=" * 60)
    log_manager.info(f"Todo completado!")
    log_manager.info(f"Archivos de log:")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'simulation.log')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'twitter', 'actions.jsonl')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'reddit', 'actions.jsonl')}")
    log_manager.info("=" * 60)


def setup_signal_handlers(loop=None):
    """
    Configurar los manejadores de señales para asegurar la salida correcta al recibir SIGTERM/SIGINT

    Escenario de simulación persistente: no salir al terminar la simulación, esperar comandos de interview
    Al recibir una señal de terminación, se necesita:
    1. Notificar al bucle asyncio para que salga de la espera
    2. Dar al programa la oportunidad de limpiar los recursos correctamente (cerrar base de datos, entorno, etc.)
    3. Y luego salir
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\nSeñal {sig_name} recibida, saliendo...")

        if not _cleanup_done:
            _cleanup_done = True
            # Establecer el evento para notificar al bucle asyncio que salga (dándole la oportunidad de limpiar recursos)
            if _shutdown_event:
                _shutdown_event.set()

        # No llamar a sys.exit() directamente, dejar que el bucle asyncio salga normalmente y limpie los recursos
        # Si se recibe la señal repetidamente, entonces forzar la salida
        else:
            print("Salida forzada...")
            sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPrograma interrumpido")
    except SystemExit:
        pass
    finally:
        # Limpiar el rastreador de recursos de multiprocessing (evitar advertencias al salir)
        try:
            from multiprocessing import resource_tracker
            resource_tracker._resource_tracker._stop()
        except Exception:
            pass
        print("Proceso de simulación finalizado")
