"""
Ejecutor de simulación OASIS
Ejecuta la simulación en segundo plano y registra las acciones de cada Agente; admite monitoreo de estado en tiempo real
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# Indica si la función de limpieza ya fue registrada
_cleanup_registered = False

# Detección de plataforma
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """Estado del ejecutor"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_RUN_STATUSES = {
    RunnerStatus.STOPPED,
    RunnerStatus.COMPLETED,
    RunnerStatus.FAILED,
}

ACTIVE_RUN_STATUSES = {
    RunnerStatus.STARTING,
    RunnerStatus.RUNNING,
    RunnerStatus.PAUSED,
    RunnerStatus.STOPPING,
}


@dataclass
class AgentAction:
    """Registro de acción de un Agente"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """Resumen de cada ronda"""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """Estado de ejecución de la simulación (en tiempo real)"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # Información de progreso
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # Rondas y tiempo simulado independientes por plataforma (para visualización paralela de dos plataformas)
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # Estado de plataforma
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # Estado de finalización de plataforma (detectado a través del evento simulation_end en actions.jsonl)
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # Resumen por ronda
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # Acciones recientes (para visualización en tiempo real en el frontend)
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # Marcas de tiempo
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # Información de error
    error: Optional[str] = None
    
    # ID de proceso (para detener)
    process_pid: Optional[int] = None
    
    def add_action(self, action: AgentAction):
        """Agregar acción a la lista de acciones recientes"""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        
        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1
        
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # Rondas y tiempo independientes por plataforma
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """Información detallada que incluye acciones recientes"""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    Ejecutor de simulación
    
    Responsable de:
    1. Ejecutar la simulación OASIS en un proceso en segundo plano
    2. Parsear los logs de ejecución y registrar las acciones de cada Agente
    3. Proporcionar una interfaz de consulta de estado en tiempo real
    4. Admitir operaciones de pausa/detención/reanudación
    """
    
    # Directorio de almacenamiento del estado de ejecución
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )
    
    # Directorio de scripts
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # Estado de ejecución en memoria
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}  # Almacena los manejadores de archivo stdout
    _stderr_files: Dict[str, Any] = {}  # Almacena los manejadores de archivo stderr
    
    # Configuración de actualización de memoria del grafo
    _graph_memory_enabled: Dict[str, bool] = {}  # simulation_id -> enabled
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Obtener estado de ejecución"""
        if simulation_id in cls._run_states:
            state = cls._run_states[simulation_id]
            cls._reconcile_process_state(state)
            return state
        
        # Intentar cargar desde archivo
        state = cls._load_run_state(simulation_id)
        if state:
            cls._reconcile_process_state(state)
            cls._run_states[simulation_id] = state
        return state

    @classmethod
    def _is_managed_process_alive(cls, simulation_id: str, pid: Optional[int]) -> bool:
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            return True

        if not pid or IS_WINDOWS:
            return False

        proc_cmdline = f"/proc/{pid}/cmdline"
        try:
            with open(proc_cmdline, "rb") as f:
                cmdline = f.read().decode("utf-8", errors="ignore").replace("\x00", " ")
            return simulation_id in cmdline and "run_" in cmdline and "simulation.py" in cmdline
        except Exception:
            return False

    @classmethod
    def _reconcile_process_state(cls, state: SimulationRunState):
        if state.runner_status not in ACTIVE_RUN_STATUSES:
            return

        if cls._is_managed_process_alive(state.simulation_id, state.process_pid):
            return

        state.runner_status = RunnerStatus.FAILED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = state.completed_at or datetime.now().isoformat()
        state.error = state.error or "El proceso de simulación no existe; se marcó como fallida para evitar estado running falso"
        cls._save_run_state(state)
    
    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Cargar estado de ejecución desde archivo"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # Rondas y tiempo independientes por plataforma
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )
            
            # Cargar acciones recientes
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"Error al cargar el estado de ejecución: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """Guardar estado de ejecución en archivo"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")
        
        data = state.to_detail_dict()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        cls._run_states[state.simulation_id] = state
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",  # twitter / reddit / parallel
        max_rounds: int = None,  # Número máximo de rondas de simulación (opcional, para truncar simulaciones demasiado largas)
        enable_graph_memory_update: bool = False,  # Si se deben actualizar las actividades en el grafo Zep
        graph_id: str = None  # ID del grafo Zep (obligatorio cuando se habilita la actualización del grafo)
    ) -> SimulationRunState:
        """
        Iniciar la simulación
        
        Args:
            simulation_id: ID de la simulación
            platform: Plataforma de ejecución (twitter/reddit/parallel)
            max_rounds: Número máximo de rondas (opcional, para truncar simulaciones demasiado largas)
            enable_graph_memory_update: Si se deben actualizar dinámicamente las actividades de los Agentes en el grafo Zep
            graph_id: ID del grafo Zep (obligatorio cuando se habilita la actualización del grafo)
            
        Returns:
            SimulationRunState
        """
        # Verificar si ya está en ejecución
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"La simulación ya está en ejecución: {simulation_id}")
        
        # Cargar la configuración de la simulación
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError(f"La configuración de simulación no existe; por favor llame primero a la interfaz /prepare")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Inicializar el estado de ejecución
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # Si se especificó un número máximo de rondas, truncar
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                logger.info(f"Rondas truncadas: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
        )
        
        cls._save_run_state(state)
        
        # Si se habilita la actualización de memoria del grafo, crear el actualizador
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("Se debe proporcionar graph_id cuando se habilita la actualización de memoria del grafo")
            
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"Actualización de memoria del grafo habilitada: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"Error al crear el actualizador de memoria del grafo: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False
        
        # Determinar qué script ejecutar (los scripts están en el directorio backend/scripts/)
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"El script no existe: {script_path}")
        
        # Crear la cola de acciones
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # Iniciar el proceso de simulación
        try:
            # Construir el comando de ejecución usando la ruta completa
            # Nueva estructura de logs:
            #   twitter/actions.jsonl - Log de acciones de Twitter
            #   reddit/actions.jsonl  - Log de acciones de Reddit
            #   simulation.log        - Log del proceso principal
            
            cmd = [
                sys.executable,  # Intérprete de Python
                script_path,
                "--config", config_path,  # Usando la ruta completa del archivo de configuración
            ]
            
            # Si se especificó un número máximo de rondas, agregar a los argumentos de la línea de comandos
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])
            
            # Crear el archivo de log principal para evitar que el proceso se bloquee por el buffer lleno de la tubería stdout/stderr
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # Configurar variables de entorno del subproceso para garantizar la codificación UTF-8 en Windows
            # Esto corrige el problema de librerías de terceros (como OASIS) que no especifican codificación al leer archivos
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'  # Compatible con Python 3.7+; hace que todos los open() usen UTF-8 por defecto
            env['PYTHONIOENCODING'] = 'utf-8'  # Garantiza que stdout/stderr usen UTF-8
            
            # Establecer el directorio de trabajo en el directorio de simulación (aquí se generarán los archivos de base de datos, etc.)
            # Usar start_new_session=True para crear un nuevo grupo de procesos y poder terminar todos los subprocesos con os.killpg
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,  # stderr también se escribe en el mismo archivo
                text=True,
                encoding='utf-8',  # Especificar codificación explícitamente
                bufsize=1,
                env=env,  # Pasar las variables de entorno con la configuración UTF-8
                start_new_session=True,  # Crear nuevo grupo de procesos para poder terminar todos los procesos relacionados al cerrar el servidor
            )
            
            # Guardar los manejadores de archivo para cerrarlos después
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None  # Ya no se necesita un stderr separado
            
            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # Iniciar hilo de monitoreo
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"Simulación iniciada correctamente: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """Monitorear el proceso de simulación y parsear los logs de acciones"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # Nueva estructura de logs: logs de acciones separados por plataforma
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:  # El proceso sigue en ejecución
                # Leer el log de acciones de Twitter
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # Leer el log de acciones de Reddit
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # Actualizar estado
                cls._save_run_state(state)
                time.sleep(2)
            
            # Después de que el proceso termina, leer el log una última vez
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # El proceso ha terminado
            exit_code = process.returncode
            
            if exit_code == 0:
                if state.runner_status == RunnerStatus.FAILED:
                    state.completed_at = datetime.now().isoformat()
                    logger.error(f"Simulación finalizada con errores de plataforma: {simulation_id}, error={state.error}")
                else:
                    state.runner_status = RunnerStatus.COMPLETED
                    state.completed_at = datetime.now().isoformat()
                    logger.info(f"Simulación completada: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # Leer la información de error del archivo de log principal
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]  # Tomar los últimos 2000 caracteres
                except Exception:
                    pass
                state.error = f"Código de salida del proceso: {exit_code}, error: {error_info}"
                logger.error(f"Simulación fallida: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            
        except Exception as e:
            logger.error(f"Excepción en el hilo de monitoreo: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # Detener el actualizador de memoria del grafo
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"Actualización de memoria del grafo detenida: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"Error al detener el actualizador de memoria del grafo: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # Limpiar los recursos del proceso
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # Cerrar los manejadores de archivo de log
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        Leer el archivo de log de acciones
        
        Args:
            log_path: Ruta del archivo de log
            position: Posición de la última lectura
            state: Objeto de estado de ejecución
            platform: Nombre de la plataforma (twitter/reddit)
            
        Returns:
            Nueva posición de lectura
        """
        # Verificar si la actualización de memoria del grafo está habilitada
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # Procesar entradas de tipo evento
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")
                                
                                # Detectar el evento simulation_end y marcar la plataforma como completada
                                if event_type == "simulation_end":
                                    if platform == "twitter":
                                        state.twitter_completed = True
                                        state.twitter_running = False
                                        logger.info(f"Simulación de Twitter completada: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    elif platform == "reddit":
                                        state.reddit_completed = True
                                        state.reddit_running = False
                                        logger.info(f"Simulación de Reddit completada: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    
                                    # Verificar si todas las plataformas habilitadas han completado
                                    # Si solo se ejecutó una plataforma, solo verificar esa
                                    # Si se ejecutaron dos plataformas, ambas deben completar
                                    all_completed = cls._check_all_platforms_completed(state)
                                    if all_completed:
                                        state.runner_status = RunnerStatus.COMPLETED
                                        state.completed_at = datetime.now().isoformat()
                                        logger.info(f"Simulación de todas las plataformas completada: {state.simulation_id}")
                                
                                # Actualizar información de ronda (desde el evento round_end)
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)
                                    
                                    # Actualizar rondas y tiempo independientes por plataforma
                                    if platform == "twitter":
                                        if round_num > state.twitter_current_round:
                                            state.twitter_current_round = round_num
                                        state.twitter_simulated_hours = simulated_hours
                                    elif platform == "reddit":
                                        if round_num > state.reddit_current_round:
                                            state.reddit_current_round = round_num
                                        state.reddit_simulated_hours = simulated_hours
                                    
                                    # La ronda global es el máximo de las dos plataformas
                                    if round_num > state.current_round:
                                        state.current_round = round_num
                                    # El tiempo global es el máximo de las dos plataformas
                                    state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)

                                elif event_type == "simulation_error":
                                    error = action_data.get("error") or f"Error en plataforma {platform}"
                                    state.runner_status = RunnerStatus.FAILED
                                    state.error = error
                                    if platform == "twitter":
                                        state.twitter_running = False
                                    elif platform == "reddit":
                                        state.reddit_running = False
                                    logger.error(f"Error de simulación reportado por {platform}: {state.simulation_id}, error={error}")
                                
                                continue
                            
                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            state.add_action(action)
                            
                            # Actualizar ronda
                            if action.round_num and action.round_num > state.current_round:
                                state.current_round = action.round_num
                            
                            # Si la actualización de memoria del grafo está habilitada, enviar la actividad a Zep
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"Error al leer el log de acciones: {log_path}, error={e}")
            return position
    
    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """
        Verificar si todas las plataformas habilitadas han completado la simulación
        
        Se determina si una plataforma está habilitada verificando si existe el archivo actions.jsonl correspondiente
        
        Returns:
            True si todas las plataformas habilitadas han completado
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        twitter_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        # Verificar qué plataformas están habilitadas (comprobando si existe el archivo)
        twitter_enabled = os.path.exists(twitter_log)
        reddit_enabled = os.path.exists(reddit_log)
        
        # Si una plataforma está habilitada pero no ha completado, retornar False
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False
        
        # Al menos una plataforma está habilitada y ha completado
        return twitter_enabled or reddit_enabled
    
    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """
        Terminar el proceso y sus subprocesos de forma multiplataforma
        
        Args:
            process: El proceso a terminar
            simulation_id: ID de la simulación (para logs)
            timeout: Tiempo de espera para que el proceso salga (en segundos)
        """
        if IS_WINDOWS:
            # Windows: usar el comando taskkill para terminar el árbol de procesos
            # /F = forzar terminación, /T = terminar árbol de procesos (incluidos subprocesos)
            logger.info(f"Terminando árbol de procesos (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # Intentar primero una terminación elegante
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Forzar terminación
                    logger.warning(f"El proceso no responde; forzando terminación: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill falló; intentando terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: usar terminación por grupo de procesos
            # Dado que se usó start_new_session=True, el ID del grupo de procesos es igual al PID del proceso principal
            pgid = os.getpgid(process.pid)
            logger.info(f"Terminando grupo de procesos (Unix): simulation={simulation_id}, pgid={pgid}")
            
            # Enviar primero SIGTERM a todo el grupo de procesos
            os.killpg(pgid, signal.SIGTERM)
            
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Si después del tiempo de espera aún no terminó, forzar SIGKILL
                logger.warning(f"El grupo de procesos no respondió a SIGTERM; forzando terminación: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)

    @classmethod
    def _terminate_pid_group(cls, pid: Optional[int], simulation_id: str, timeout: int = 10):
        """Terminar un grupo de procesos cuando solo queda el PID persistido."""
        if not pid or IS_WINDOWS:
            return

        if not cls._is_managed_process_alive(simulation_id, pid):
            return

        try:
            pgid = os.getpgid(pid)
            logger.info(f"Terminando grupo de procesos persistido: simulation={simulation_id}, pgid={pgid}")
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as e:
            logger.warning(f"No se pudo enviar SIGTERM al grupo persistido: {simulation_id}, error={e}")
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not cls._is_managed_process_alive(simulation_id, pid):
                return
            time.sleep(0.2)

        try:
            logger.warning(f"El grupo persistido no respondió a SIGTERM; forzando: {simulation_id}")
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception as e:
            logger.warning(f"No se pudo enviar SIGKILL al grupo persistido: {simulation_id}, error={e}")
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """Detener la simulación"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"La simulación no existe: {simulation_id}")
        
        if state.runner_status in TERMINAL_RUN_STATUSES:
            process = cls._processes.get(simulation_id)
            if process and process.poll() is None:
                try:
                    cls._terminate_process(process, simulation_id)
                except Exception as e:
                    logger.warning(f"No se pudo terminar proceso terminal activo: {simulation_id}, error={e}")
            else:
                cls._terminate_pid_group(state.process_pid, simulation_id)

            if state.twitter_running or state.reddit_running:
                state.twitter_running = False
                state.reddit_running = False
                cls._save_run_state(state)
            return state

        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"La simulación no está en ejecución: {simulation_id}, status={state.runner_status}")
        
        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)
        
        # Terminar el proceso
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                # El proceso ya no existe
                pass
            except Exception as e:
                logger.error(f"Error al terminar el grupo de procesos: {simulation_id}, error={e}")
                # Retroceder a terminación directa del proceso
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
        
        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)
        
        # Detener el actualizador de memoria del grafo
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"Actualización de memoria del grafo detenida: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"Error al detener el actualizador de memoria del grafo: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)
        
        logger.info(f"Simulación detenida: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Leer acciones desde un único archivo de acciones
        
        Args:
            file_path: Ruta del archivo de log de acciones
            default_platform: Plataforma predeterminada (se usa cuando el registro de acción no tiene campo platform)
            platform_filter: Filtrar por plataforma
            agent_id: Filtrar por ID de Agente
            round_num: Filtrar por número de ronda
        """
        if not os.path.exists(file_path):
            return []
        
        actions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # Omitir registros que no son acciones (como eventos simulation_start, round_start, round_end)
                    if "event_type" in data:
                        continue
                    
                    # Omitir registros sin agent_id (no son acciones de Agente)
                    if "agent_id" not in data:
                        continue
                    
                    # Obtener plataforma: priorizar el campo platform del registro; de lo contrario, usar la plataforma predeterminada
                    record_platform = data.get("platform") or default_platform or ""
                    
                    # Filtrar
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Obtener el historial completo de acciones de todas las plataformas (sin límite de paginación)
        
        Args:
            simulation_id: ID de la simulación
            platform: Filtrar por plataforma (twitter/reddit)
            agent_id: Filtrar por Agente
            round_num: Filtrar por número de ronda
            
        Returns:
            Lista completa de acciones (ordenada por marca de tiempo, más reciente primero)
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []
        
        # Leer archivo de acciones de Twitter (platform se establece automáticamente como "twitter" según la ruta)
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # Rellena automáticamente el campo platform
                platform_filter=platform,
                agent_id=agent_id, 
                round_num=round_num
            ))
        
        # Leer archivo de acciones de Reddit (platform se establece automáticamente como "reddit" según la ruta)
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # Rellena automáticamente el campo platform
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))
        
        # Si los archivos separados por plataforma no existen, intentar leer el formato de archivo único antiguo
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # El archivo de formato antiguo debería tener el campo platform
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )
        
        # Ordenar por marca de tiempo (más reciente primero)
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Obtener historial de acciones (con paginación)
        
        Args:
            simulation_id: ID de la simulación
            limit: Límite de resultados a devolver
            offset: Desplazamiento
            platform: Filtrar por plataforma
            agent_id: Filtrar por Agente
            round_num: Filtrar por número de ronda
            
        Returns:
            Lista de acciones
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        # Paginación
        return actions[offset:offset + limit]
    
    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Obtener la línea de tiempo de la simulación (resumida por ronda)
        
        Args:
            simulation_id: ID de la simulación
            start_round: Ronda inicial
            end_round: Ronda final
            
        Returns:
            Información resumida por ronda
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        # Agrupar por ronda
        rounds: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            round_num = action.round_num
            
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue
            
            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            r = rounds[round_num]
            
            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1
            
            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp
        
        # Convertir a lista
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        
        return result
    
    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        Obtener estadísticas de cada Agente
        
        Returns:
            Lista de estadísticas de Agentes
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp
        
        # Ordenar por número total de acciones
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)
        
        return result
    
    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        Limpiar los logs de ejecución de la simulación (para forzar el reinicio de la simulación)
        
        Se eliminarán los siguientes archivos:
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db (base de datos de simulación)
        - reddit_simulation.db (base de datos de simulación)
        - env_status.json (estado del entorno)
        
        Nota: no se eliminará el archivo de configuración (simulation_config.json) ni los archivos de perfil
        
        Args:
            simulation_id: ID de la simulación
            
        Returns:
            Información del resultado de la limpieza
        """
        import shutil
        
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "El directorio de simulación no existe; no es necesario limpiar"}
        
        cleaned_files = []
        errors = []
        
        # Lista de archivos a eliminar (incluidos los archivos de base de datos)
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # Base de datos de la plataforma Twitter
            "reddit_simulation.db",   # Base de datos de la plataforma Reddit
            "env_status.json",        # Archivo de estado del entorno
        ]
        
        # Lista de directorios a limpiar (contienen logs de acciones)
        dirs_to_clean = ["twitter", "reddit"]
        
        # Eliminar archivos
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"Error al eliminar {filename}: {str(e)}")
        
        # Limpiar los logs de acciones en los directorios de plataforma
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                actions_file = os.path.join(dir_path, "actions.jsonl")
                if os.path.exists(actions_file):
                    try:
                        os.remove(actions_file)
                        cleaned_files.append(f"{dir_name}/actions.jsonl")
                    except Exception as e:
                        errors.append(f"Error al eliminar {dir_name}/actions.jsonl: {str(e)}")
        
        # Limpiar el estado de ejecución en memoria
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]
        
        logger.info(f"Limpieza de logs de simulación completada: {simulation_id}, archivos eliminados: {cleaned_files}")
        
        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # Indicador para prevenir limpieza duplicada
    _cleanup_done = False
    
    @classmethod
    def cleanup_all_simulations(cls):
        """
        Limpiar todos los procesos de simulación en ejecución
        
        Se llama al cerrar el servidor para garantizar que todos los subprocesos sean terminados
        """
        # Prevenir limpieza duplicada
        if cls._cleanup_done:
            return
        cls._cleanup_done = True
        
        # Verificar si hay contenido que limpiar (evitar que procesos vacíos impriman logs innecesarios)
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        
        if not has_processes and not has_updaters:
            return  # Sin contenido que limpiar; retorno silencioso
        
        logger.info("Limpiando todos los procesos de simulación...")
        
        # Primero detener todos los actualizadores de memoria del grafo (stop_all imprimirá logs internamente)
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"Error al detener el actualizador de memoria del grafo: {e}")
        cls._graph_memory_enabled.clear()
        
        # Copiar el diccionario para evitar modificarlo durante la iteración
        processes = list(cls._processes.items())
        
        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # El proceso sigue en ejecución
                    logger.info(f"Terminando proceso de simulación: {simulation_id}, pid={process.pid}")
                    
                    try:
                        # Usar el método de terminación de procesos multiplataforma
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # El proceso puede que ya no exista; intentar terminación directa
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()
                    
                    # Actualizar run_state.json
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "Servidor cerrado; simulación terminada"
                        cls._save_run_state(state)
                    
                    # También actualizar state.json para establecer el estado como stopped
                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        logger.info(f"Intentando actualizar state.json: {state_file}")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                            logger.info(f"state.json actualizado a stopped: {simulation_id}")
                        else:
                            logger.warning(f"state.json no existe: {state_file}")
                    except Exception as state_err:
                        logger.warning(f"Error al actualizar state.json: {simulation_id}, error={state_err}")
                        
            except Exception as e:
                logger.error(f"Error al limpiar el proceso: {simulation_id}, error={e}")
        
        # Limpiar manejadores de archivo
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()
        
        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()
        
        # Limpiar el estado en memoria
        cls._processes.clear()
        cls._action_queues.clear()
        
        logger.info("Limpieza de procesos de simulación completada")
    
    @classmethod
    def register_cleanup(cls):
        """
        Registrar la función de limpieza
        
        Se llama al iniciar la aplicación Flask para garantizar que todos los procesos de simulación sean limpiados al cerrar el servidor
        """
        global _cleanup_registered
        
        if _cleanup_registered:
            return
        
        # En modo debug de Flask, solo registrar la limpieza en el subproceso del reloader (el proceso que ejecuta realmente la app)
        # WERKZEUG_RUN_MAIN=true indica que es el subproceso del reloader
        # Si no está en modo debug, esta variable de entorno no existe; también se debe registrar
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        
        # En modo debug, solo registrar en el subproceso del reloader; en modo no-debug, siempre registrar
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # Marcar como registrado para evitar que el subproceso intente nuevamente
            return
        
        # Guardar los manejadores de señal originales
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP solo existe en sistemas Unix (macOS/Linux); Windows no lo tiene
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)
        
        def cleanup_handler(signum=None, frame=None):
            """Manejador de señales: primero limpia los procesos de simulación, luego llama al manejador original"""
            # Solo imprimir log si hay procesos que limpiar
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"Señal {signum} recibida; iniciando limpieza...")
            cls.cleanup_all_simulations()
            
            # Llamar al manejador de señal original para que Flask salga normalmente
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: enviado cuando el terminal se cierra
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # Comportamiento predeterminado: salida normal
                    sys.exit(0)
            else:
                # Si el manejador original no es invocable (como SIG_DFL), usar comportamiento predeterminado
                raise KeyboardInterrupt
        
        # Registrar manejador atexit (como respaldo)
        atexit.register(cls.cleanup_all_simulations)
        
        # Registrar manejadores de señal (solo en el hilo principal)
        try:
            # SIGTERM: señal predeterminada del comando kill
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: cierre de terminal (solo sistemas Unix)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # No está en el hilo principal; solo se puede usar atexit
            logger.warning("No se pueden registrar manejadores de señal (no en el hilo principal); usando solo atexit")
        
        _cleanup_registered = True
    
    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        Obtener la lista de IDs de todas las simulaciones en ejecución
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running
    
    # ============== Funcionalidad de Entrevistas ==============
    
    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        Verificar si el entorno de simulación está activo (puede recibir comandos de entrevista)

        Args:
            simulation_id: ID de la simulación

        Returns:
            True si el entorno está activo; False si el entorno está cerrado
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        Obtener información detallada del estado del entorno de simulación

        Args:
            simulation_id: ID de la simulación

        Returns:
            Diccionario de detalles de estado que contiene status, twitter_available, reddit_available, timestamp
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        Entrevistar a un solo Agente

        Args:
            simulation_id: ID de la simulación
            agent_id: ID del Agente
            prompt: Pregunta de la entrevista
            platform: Plataforma específica (opcional)
                - "twitter": Solo entrevistar en la plataforma Twitter
                - "reddit": Solo entrevistar en la plataforma Reddit
                - None: En simulación de dos plataformas, entrevistar en ambas simultáneamente y devolver resultado combinado
            timeout: Tiempo de espera (en segundos)

        Returns:
            Diccionario con el resultado de la entrevista

        Raises:
            ValueError: La simulación no existe o el entorno no está en ejecución
            TimeoutError: Tiempo de espera agotado esperando respuesta
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"La simulación no existe: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"El entorno de simulación no está en ejecución o ya está cerrado; no se puede ejecutar la entrevista: {simulation_id}")

        logger.info(f"Enviando comando de entrevista: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        Entrevistar a múltiples Agentes en lote

        Args:
            simulation_id: ID de la simulación
            interviews: Lista de entrevistas; cada elemento contiene {"agent_id": int, "prompt": str, "platform": str (opcional)}
            platform: Plataforma predeterminada (opcional; será sobreescrita por el campo platform de cada entrevista)
                - "twitter": Predeterminado: solo entrevistar en la plataforma Twitter
                - "reddit": Predeterminado: solo entrevistar en la plataforma Reddit
                - None: En simulación de dos plataformas, cada Agente es entrevistado en ambas plataformas
            timeout: Tiempo de espera (en segundos)

        Returns:
            Diccionario con los resultados de la entrevista en lote

        Raises:
            ValueError: La simulación no existe o el entorno no está en ejecución
            TimeoutError: Tiempo de espera agotado esperando respuesta
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"La simulación no existe: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"El entorno de simulación no está en ejecución o ya está cerrado; no se puede ejecutar la entrevista: {simulation_id}")

        logger.info(f"Enviando comando de entrevista en lote: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        Entrevistar a todos los Agentes (entrevista global)

        Usa la misma pregunta para entrevistar a todos los Agentes de la simulación

        Args:
            simulation_id: ID de la simulación
            prompt: Pregunta de la entrevista (todos los Agentes usan la misma pregunta)
            platform: Plataforma específica (opcional)
                - "twitter": Solo entrevistar en la plataforma Twitter
                - "reddit": Solo entrevistar en la plataforma Reddit
                - None: En simulación de dos plataformas, cada Agente es entrevistado en ambas plataformas
            timeout: Tiempo de espera (en segundos)

        Returns:
            Diccionario con los resultados de la entrevista global
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"La simulación no existe: {simulation_id}")

        # Obtener la información de todos los Agentes desde el archivo de configuración
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"La configuración de simulación no existe: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"No hay Agentes en la configuración de simulación: {simulation_id}")

        # Construir la lista de entrevistas en lote
        interviews = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            if agent_id is not None:
                interviews.append({
                    "agent_id": agent_id,
                    "prompt": prompt
                })

        logger.info(f"Enviando comando de entrevista global: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        Cerrar el entorno de simulación (sin detener el proceso de simulación)
        
        Envía al entorno de simulación el comando de cierre para que salga elegantemente del modo de espera de comandos
        
        Args:
            simulation_id: ID de la simulación
            timeout: Tiempo de espera (en segundos)
            
        Returns:
            Diccionario con el resultado de la operación
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"La simulación no existe: {simulation_id}")
        
        ipc_client = SimulationIPCClient(sim_dir)
        
        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "El entorno ya está cerrado"
            }
        
        logger.info(f"Enviando comando de cierre del entorno: simulation_id={simulation_id}")
        
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            
            return {
                "success": response.status.value == "completed",
                "message": "Comando de cierre del entorno enviado",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # El tiempo de espera puede deberse a que el entorno está cerrándose
            return {
                "success": True,
                "message": "Comando de cierre del entorno enviado (tiempo de espera agotado; el entorno puede estar cerrándose)"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Obtener historial de entrevistas desde una sola base de datos"""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Error al leer el historial de entrevistas ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Obtener el historial de entrevistas (leído desde la base de datos)
        
        Args:
            simulation_id: ID de la simulación
            platform: Tipo de plataforma (reddit/twitter/None)
                - "reddit": Solo obtener el historial de la plataforma Reddit
                - "twitter": Solo obtener el historial de la plataforma Twitter
                - None: Obtener todo el historial de ambas plataformas
            agent_id: ID de Agente específico (opcional; solo obtener el historial de ese Agente)
            limit: Límite de resultados a devolver por plataforma
            
        Returns:
            Lista del historial de entrevistas
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        results = []
        
        # Determinar las plataformas a consultar
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # Si no se especifica plataforma, consultar ambas plataformas
            platforms = ["twitter", "reddit"]
        
        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)
        
        # Ordenar por tiempo en orden descendente
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # Si se consultaron múltiples plataformas, limitar el total
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]
        
        return results
