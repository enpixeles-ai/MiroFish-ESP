"""
Servicio de generación de ontología
Interfaz 1: Analiza el contenido del texto y genera definiciones de tipos de entidades y relaciones para simulación social
"""

import json
import re
import unicodedata
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger


logger = get_logger('mirofish.ontology')


RESERVED_ATTRIBUTE_NAMES = {"name", "uuid", "group_id", "created_at", "summary"}


def _ascii_text(value: Any) -> str:
    text = str(value or "").strip()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _split_identifier_words(value: Any) -> List[str]:
    text = _ascii_text(value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return [part for part in re.split(r"[^a-zA-Z0-9]+", text) if part]


def _to_pascal_case(value: Any, fallback: str = "UnknownEntity") -> str:
    words = _split_identifier_words(value)
    result = "".join(word[:1].upper() + word[1:].lower() for word in words)
    return result or fallback


def _to_screaming_snake_case(value: Any, fallback: str = "RELATED_TO") -> str:
    words = _split_identifier_words(value)
    result = "_".join(word.upper() for word in words)
    if result == "COLLABORABLES_WITH":
        return "COLLABORATES_WITH"
    return result or fallback


def _to_snake_case(value: Any, fallback: str = "attribute_text") -> str:
    words = _split_identifier_words(value)
    result = "_".join(word.lower() for word in words)
    if not result or result in RESERVED_ATTRIBUTE_NAMES:
        return fallback
    return result


def _escape_docstring(value: Any) -> str:
    return str(value or "").replace('"""', '\\"\\"\\"').replace("\n", " ")


# Prompt de sistema para la generación de ontología
ONTOLOGY_SYSTEM_PROMPT = """Responde en español latino para analysis_summary, descriptions, explanations y examples.
Mantén SIEMPRE en inglés técnico los campos name de entity_types, edge_types y attributes:
- entity_types[].name: English PascalCase, por ejemplo ImmigrationLawyer, CommunityOrganizer.
- edge_types[].name: English UPPER_SNAKE_CASE, por ejemplo WORKS_FOR, INFLUENCES.
- attributes[].name: English snake_case, por ejemplo full_name, influence_level.

Eres un experto profesional en diseño de ontologías para grafos de conocimiento. Tu tarea es analizar el contenido del texto y los requisitos de simulación para diseñar tipos de entidades y tipos de relaciones adecuados para la **simulación de opinión pública en redes sociales**.

**Importante: Debes generar datos en formato JSON válido. No incluyas ningún otro contenido.**

## Contexto de la tarea principal

Estamos construyendo un **sistema de simulación de opinión pública en redes sociales**. En este sistema:
- Cada entidad es una "cuenta" o "sujeto" capaz de publicar, interactuar y difundir información en redes sociales
- Las entidades se influyen mutuamente: comparten, comentan y responden
- Necesitamos simular las reacciones de cada parte en eventos de opinión pública y las rutas de propagación de información

Por lo tanto, **las entidades deben ser sujetos reales que existan en la realidad y puedan expresarse e interactuar en redes sociales**:

**Pueden ser**:
- Individuos concretos (figuras públicas, involucrados, líderes de opinión, expertos académicos, ciudadanos comunes)
- Empresas y corporaciones (incluidas sus cuentas oficiales)
- Organizaciones institucionales (universidades, asociaciones, ONG, sindicatos, etc.)
- Organismos gubernamentales, entes reguladores
- Medios de comunicación (periódicos, canales de TV, medios independientes, sitios web)
- Las propias plataformas de redes sociales
- Representantes de grupos específicos (como asociaciones de ex alumnos, grupos de fans, colectivos de defensa de derechos, etc.)

**No pueden ser**:
- Conceptos abstractos (como "opinión pública", "emoción", "tendencia")
- Temas/asuntos (como "integridad académica", "reforma educativa")
- Puntos de vista/actitudes (como "partido a favor", "partido en contra")

## Formato de salida

Genera una salida en formato JSON con la siguiente estructura:

```json
{
    "entity_types": [
        {
            "name": "nombre del tipo de entidad (en inglés, PascalCase)",
            "description": "descripción breve (en inglés, máx. 100 caracteres)",
            "attributes": [
                {
                    "name": "nombre del atributo (en inglés, snake_case)",
                    "type": "text",
                    "description": "descripción del atributo"
                }
            ],
            "examples": ["entidad de ejemplo 1", "entidad de ejemplo 2"]
        }
    ],
    "edge_types": [
        {
            "name": "nombre del tipo de relación (en inglés, UPPER_SNAKE_CASE)",
            "description": "descripción breve (en inglés, máx. 100 caracteres)",
            "source_targets": [
                {"source": "tipo de entidad origen", "target": "tipo de entidad destino"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "breve análisis explicativo del contenido del texto (en español)"
}
```

## Guía de diseño (¡muy importante!)

### 1. Diseño de tipos de entidad — debe seguirse estrictamente

**Requisito de cantidad: exactamente 10 tipos de entidad**

**Requisito de estructura jerárquica (debe incluir simultáneamente tipos concretos y tipos de reserva)**:

Tus 10 tipos de entidad deben incluir los siguientes niveles:

A. **Tipos de reserva (obligatorios, colocados al final de la lista como los últimos 2)**:
   - `Person`: tipo de reserva para cualquier persona natural individual. Cuando una persona no encaje en ningún otro tipo de persona más específico, se clasifica aquí.
   - `Organization`: tipo de reserva para cualquier organización institucional. Cuando una organización no encaje en ningún tipo de organización más específico, se clasifica aquí.

B. **Tipos concretos (8, diseñados según el contenido del texto)**:
   - Diseña tipos más específicos para los roles principales que aparecen en el texto
   - Por ejemplo: si el texto trata un evento académico, puede haber `Student`, `Professor`, `University`
   - Por ejemplo: si el texto trata un evento empresarial, puede haber `Company`, `CEO`, `Employee`

**Por qué se necesitan tipos de reserva**:
- En el texto aparecerán todo tipo de personas, como "docentes de primaria y secundaria", "transeúntes" o "algún usuario de internet"
- Si no hay un tipo específico que coincida, deben clasificarse en `Person`
- Del mismo modo, organizaciones pequeñas, grupos temporales, etc. deben clasificarse en `Organization`

**Principios de diseño para tipos concretos**:
- Identifica los tipos de roles que aparecen con mayor frecuencia o que son clave en el texto
- Cada tipo concreto debe tener límites claros para evitar superposiciones
- La descripción (description) debe explicar claramente la diferencia entre este tipo y el tipo de reserva

### 2. Diseño de tipos de relación

- Cantidad: 6-10
- Las relaciones deben reflejar vínculos reales en las interacciones de redes sociales
- Asegúrate de que los source_targets de las relaciones cubran los tipos de entidad que has definido

### 3. Diseño de atributos

- 1-3 atributos clave por tipo de entidad
- **Nota**: los nombres de atributos no pueden usar `name`, `uuid`, `group_id`, `created_at`, `summary` (estas son palabras reservadas del sistema)
- Se recomienda usar: `full_name`, `title`, `role`, `position`, `location`, `description`, etc.

## Referencia de tipos de entidad

**Personas (concretos)**:
- Student: Estudiante
- Professor: Profesor/Académico
- Journalist: Periodista
- Celebrity: Celebridad/Influencer
- Executive: Alto ejecutivo
- Official: Funcionario de gobierno
- Lawyer: Abogado
- Doctor: Médico

**Personas (reserva)**:
- Person: Cualquier persona natural (se usa cuando no encaja en los tipos concretos anteriores)

**Organizaciones (concretos)**:
- University: Universidad o institución de educación superior
- Company: Empresa o corporación
- GovernmentAgency: Organismo gubernamental
- MediaOutlet: Medio de comunicación
- Hospital: Hospital
- School: Escuela de primaria o secundaria
- NGO: Organización no gubernamental

**Organizaciones (reserva)**:
- Organization: Cualquier organización institucional (se usa cuando no encaja en los tipos concretos anteriores)

## Referencia de tipos de relación

- WORKS_FOR: Trabaja para
- STUDIES_AT: Estudia en
- AFFILIATED_WITH: Afiliado/vinculado a
- REPRESENTS: Representa
- REGULATES: Regula
- REPORTS_ON: Reporta/cubre
- COMMENTS_ON: Comenta
- RESPONDS_TO: Responde a
- SUPPORTS: Apoya
- OPPOSES: Se opone
- COLLABORATES_WITH: Colabora con
- COMPETES_WITH: Compite con
"""


class OntologyGenerator:
    """
    Generador de ontología
    Analiza el contenido del texto y genera definiciones de tipos de entidades y relaciones
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Genera la definición de ontología
        
        Args:
            document_texts: Lista de textos de documentos
            simulation_requirement: Descripción del requisito de simulación
            additional_context: Contexto adicional
            
        Returns:
            Definición de ontología (entity_types, edge_types, etc.)
        """
        # Construir el mensaje de usuario
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # Llamar al LLM
        try:
            result = self.llm_client.chat_json(
                messages=messages,
                temperature=0.2,
                max_tokens=8192
            )
        except Exception as exc:
            logger.error(
                "No se pudo generar ontología válida con LLM; usando ontología segura por defecto. "
                f"Error: {type(exc).__name__}: {exc}"
            )
            result = self._build_fallback_ontology(simulation_requirement)
        
        # Validación y post-procesamiento
        result = self._validate_and_process(result)
        
        return result

    def _build_fallback_ontology(self, simulation_requirement: str) -> Dict[str, Any]:
        """Ontología conservadora para que crear proyecto no falle por salida LLM truncada."""
        summary = (simulation_requirement or "").strip().replace("\n", " ")
        if len(summary) > 240:
            summary = summary[:237] + "..."
        if not summary:
            summary = "Ontología base generada automáticamente porque el LLM no devolvió JSON válido."

        entity_types = [
            {
                "name": "BusinessOwner",
                "description": "Owner or decision maker in a business context.",
                "attributes": [
                    {"name": "full_name", "type": "text", "description": "Full name"},
                    {"name": "business_role", "type": "text", "description": "Business role"},
                ],
                "examples": ["Founder", "Manager"],
            },
            {
                "name": "Customer",
                "description": "Customer, lead, or affected end user.",
                "attributes": [
                    {"name": "full_name", "type": "text", "description": "Full name"},
                    {"name": "customer_need", "type": "text", "description": "Primary need"},
                ],
                "examples": ["Lead", "Client"],
            },
            {
                "name": "ServiceProvider",
                "description": "Provider offering a product or service.",
                "attributes": [
                    {"name": "org_name", "type": "text", "description": "Organization name"},
                    {"name": "service_type", "type": "text", "description": "Service category"},
                ],
                "examples": ["Agency", "Consultant"],
            },
            {
                "name": "CommunityGroup",
                "description": "Community or audience segment.",
                "attributes": [
                    {"name": "group_label", "type": "text", "description": "Group label"},
                    {"name": "shared_interest", "type": "text", "description": "Shared interest"},
                ],
                "examples": ["WhatsApp group", "Local community"],
            },
            {
                "name": "Influencer",
                "description": "Person who shapes audience opinions.",
                "attributes": [
                    {"name": "full_name", "type": "text", "description": "Full name"},
                    {"name": "channel", "type": "text", "description": "Main channel"},
                ],
                "examples": ["Creator", "Community leader"],
            },
            {
                "name": "MediaChannel",
                "description": "Media or social platform channel.",
                "attributes": [
                    {"name": "channel_name", "type": "text", "description": "Channel name"},
                    {"name": "platform_type", "type": "text", "description": "Platform type"},
                ],
                "examples": ["Facebook", "TikTok"],
            },
            {
                "name": "Competitor",
                "description": "Competing organization or alternative option.",
                "attributes": [
                    {"name": "org_name", "type": "text", "description": "Organization name"},
                    {"name": "offer_type", "type": "text", "description": "Competing offer"},
                ],
                "examples": ["Competitor brand", "Alternative provider"],
            },
            {
                "name": "PublicInstitution",
                "description": "Public agency, regulator, or institution.",
                "attributes": [
                    {"name": "org_name", "type": "text", "description": "Institution name"},
                    {"name": "jurisdiction", "type": "text", "description": "Jurisdiction"},
                ],
                "examples": ["Government agency", "Regulator"],
            },
            {
                "name": "Person",
                "description": "Any individual person not covered by specific types.",
                "attributes": [
                    {"name": "full_name", "type": "text", "description": "Full name"},
                    {"name": "role", "type": "text", "description": "Role or occupation"},
                ],
                "examples": ["Citizen", "User"],
            },
            {
                "name": "Organization",
                "description": "Any organization not covered by specific types.",
                "attributes": [
                    {"name": "org_name", "type": "text", "description": "Organization name"},
                    {"name": "org_type", "type": "text", "description": "Organization type"},
                ],
                "examples": ["Company", "Association"],
            },
        ]

        edge_types = [
            {
                "name": "INFLUENCES",
                "description": "Shapes opinions or decisions.",
                "source_targets": [{"source": "Influencer", "target": "Customer"}],
                "attributes": [],
            },
            {
                "name": "SERVES",
                "description": "Provides service to a customer or group.",
                "source_targets": [{"source": "ServiceProvider", "target": "Customer"}],
                "attributes": [],
            },
            {
                "name": "COMPETES_WITH",
                "description": "Competes for attention, trust, or conversion.",
                "source_targets": [{"source": "ServiceProvider", "target": "Competitor"}],
                "attributes": [],
            },
            {
                "name": "COMMUNICATES_THROUGH",
                "description": "Uses a channel to communicate.",
                "source_targets": [{"source": "Person", "target": "MediaChannel"}],
                "attributes": [],
            },
            {
                "name": "BELONGS_TO",
                "description": "Belongs to a community or organization.",
                "source_targets": [{"source": "Person", "target": "CommunityGroup"}],
                "attributes": [],
            },
            {
                "name": "REGULATES",
                "description": "Regulates or governs an organization.",
                "source_targets": [{"source": "PublicInstitution", "target": "Organization"}],
                "attributes": [],
            },
            {
                "name": "RESPONDS_TO",
                "description": "Responds to another actor or message.",
                "source_targets": [{"source": "Customer", "target": "ServiceProvider"}],
                "attributes": [],
            },
            {
                "name": "COLLABORATES_WITH",
                "description": "Collaborates with another actor.",
                "source_targets": [{"source": "Organization", "target": "Organization"}],
                "attributes": [],
            },
        ]

        return {
            "entity_types": entity_types,
            "edge_types": edge_types,
            "analysis_summary": (
                "Se usó una ontología base segura porque la respuesta JSON del LLM fue inválida. "
                f"Resumen del requisito: {summary}"
            ),
        }
    
    # Longitud máxima del texto enviado al LLM (50 000 caracteres)
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """Construir el mensaje de usuario"""
        
        # Combinar textos
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # Si el texto supera los 50 000 caracteres, truncar (solo afecta el contenido enviado al LLM, no afecta la construcción del grafo)
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(el texto original tiene {original_length} caracteres; se han tomado los primeros {self.MAX_TEXT_LENGTH_FOR_LLM} para el análisis de ontología)..."
        
        message = f"""## Requisito de simulación

{simulation_requirement}

## Contenido del documento

{combined_text}
"""
        
        if additional_context:
            message += f"""
## Notas adicionales

{additional_context}
"""
        
        message += """
Basándote en el contenido anterior, diseña los tipos de entidades y tipos de relaciones adecuados para la simulación de opinión pública social.

**Reglas que deben cumplirse obligatoriamente**:
1. Se deben generar exactamente 10 tipos de entidad
2. Los últimos 2 deben ser los tipos de reserva: Person (reserva personal) y Organization (reserva organizacional)
3. Los primeros 8 son tipos concretos diseñados según el contenido del texto
4. Todos los tipos de entidad deben ser sujetos que puedan expresarse en la realidad; no pueden ser conceptos abstractos
5. Los nombres de atributos no pueden usar palabras reservadas como name, uuid, group_id, etc.; usa full_name, org_name, etc. como alternativas
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validar y post-procesar el resultado"""
        
        # Asegurarse de que los campos necesarios existen
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        entity_name_map = {}

        # Validar tipos de entidad
        for entity in result["entity_types"]:
            original_name = entity.get("name", "")
            entity["name"] = _to_pascal_case(original_name)
            entity_name_map[original_name] = entity["name"]
            if entity["name"] != original_name:
                logger.warning(f"Nombre de entidad normalizado: {original_name!r} -> {entity['name']!r}")

            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []

            normalized_attributes = []
            for index, attr in enumerate(entity.get("attributes", []), start=1):
                original_attr_name = attr.get("name", "")
                fallback_attr_name = f"attribute_{index}_text"
                attr["name"] = _to_snake_case(original_attr_name, fallback_attr_name)
                if attr["name"] != original_attr_name:
                    logger.warning(
                        f"Atributo normalizado en {entity['name']}: {original_attr_name!r} -> {attr['name']!r}"
                    )
                attr["type"] = "text"
                normalized_attributes.append(attr)
            entity["attributes"] = normalized_attributes

            # Asegurarse de que la descripción no supere los 100 caracteres
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # Validar tipos de relación
        for edge in result["edge_types"]:
            original_edge_name = edge.get("name", "")
            edge["name"] = _to_screaming_snake_case(original_edge_name)
            if edge["name"] != original_edge_name:
                logger.warning(f"Nombre de relación normalizado: {original_edge_name!r} -> {edge['name']!r}")

            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []

            for source_target in edge.get("source_targets", []):
                source = source_target.get("source")
                target = source_target.get("target")
                if source in entity_name_map:
                    source_target["source"] = entity_name_map[source]
                elif source:
                    source_target["source"] = _to_pascal_case(source)
                if target in entity_name_map:
                    source_target["target"] = entity_name_map[target]
                elif target:
                    source_target["target"] = _to_pascal_case(target)

            normalized_edge_attributes = []
            for index, attr in enumerate(edge.get("attributes", []), start=1):
                attr["name"] = _to_snake_case(attr.get("name", ""), f"edge_attribute_{index}_text")
                attr["type"] = "text"
                normalized_edge_attributes.append(attr)
            edge["attributes"] = normalized_edge_attributes

            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Límite de la API Zep: máximo 10 tipos de entidad personalizados y máximo 10 tipos de aristas personalizados
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10

        seen_names = set()
        deduped_entities = []
        for entity in result["entity_types"]:
            name = entity.get("name")
            if name and name not in seen_names:
                seen_names.add(name)
                deduped_entities.append(entity)
            elif name:
                logger.warning(f"Tipo de entidad duplicado eliminado: {name}")
        result["entity_types"] = deduped_entities
        
        # Definición de tipos de reserva
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # Verificar si ya existen los tipos de reserva
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # Tipos de reserva que se deben agregar
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # Si después de agregar se supera el límite de 10, se deben eliminar algunos tipos existentes
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # Calcular cuántos se deben eliminar
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # Eliminar desde el final (conservando los tipos concretos más importantes al inicio)
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # Agregar tipos de reserva
            result["entity_types"].extend(fallbacks_to_add)
        
        # Asegurar finalmente que no se supere el límite (programación defensiva)
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        Convierte la definición de ontología a código Python (similar a ontology.py)
        
        Args:
            ontology: Definición de ontología
            
        Returns:
            Cadena de código Python
        """
        code_lines = [
            '"""',
            'Definición de tipos de entidad personalizados',
            'Generado automáticamente por MiroFish para simulación de opinión pública social',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== Definición de tipos de entidad ==============',
            '',
        ]
        
        # Generar tipos de entidad
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{_escape_docstring(desc)}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description={json.dumps(str(attr_desc), ensure_ascii=False)},')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== Definición de tipos de relación ==============')
        code_lines.append('')
        
        # Generar tipos de relación
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # Convertir a nombre de clase PascalCase
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{_escape_docstring(desc)}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description={json.dumps(str(attr_desc), ensure_ascii=False)},')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # Generar diccionario de tipos
        code_lines.append('# ============== Configuración de tipos ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # Generar el mapeo source_targets de aristas
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)
