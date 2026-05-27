"""
Memory Compaction Skill para Origin.
Compresion inteligente de memorias antiguas para mantener contexto largo eficiente.
Inspirado en claude-mem (progressive disclosure), construido desde cero.
"""

import time
import logging
from typing import Dict, Any, List
from datetime import datetime
from collections import defaultdict

import numpy as np

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.memory_compaction")

# ── Umbrales de compactacion ───────────────────────────────────────
DUPLICATE_THRESHOLD = 0.92  # Similitud coseno para considerar duplicado
CLUSTER_THRESHOLD = 0.75  # Similitud para agrupar memorias relacionadas
MAX_CLUSTER_SIZE = 10  # Maximo de memorias por cluster
STALE_DAYS = 14  # Dias para considerar una memoria "vieja"
ANCIENT_DAYS = 60  # Dias para considerar "antigua" (candidata a compresion agresiva)


class MemoryCompactionSkill(BaseSkill):
    """
    Skill: Compactacion y mantenimiento de memorias.

    Acciones:
        stats       - Estadisticas del estado actual de la memoria
        duplicates  - Detecta y lista memorias duplicadas/casi-duplicadas
        cluster     - Agrupa memorias por similitud tematica
        compact     - Genera resumen compacto de un grupo de memorias (para reemplazo)
        plan        - Analisis completo: identifica que compactar, que borrar, que mantener
        prune       - Elimina duplicados del cache RAM (requiere memory_manager)

    La skill trabaja sobre listas de memorias pasadas como input.
    No modifica datos directamente (salvo 'prune') — retorna recomendaciones.
    """

    VALID_ACTIONS = {"stats", "duplicates", "cluster", "compact", "plan", "prune"}

    def __init__(self):
        super().__init__(name="memory_compaction", description="Analiza, compacta y optimiza las memorias de Origin")

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if not action:
            return False, f"Se requiere 'action'. Validas: {self.VALID_ACTIONS}"
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        # La mayoria de acciones necesitan memorias
        if action in ("duplicates", "cluster", "compact", "plan"):
            memories = inputs.get("memories", [])
            if not memories:
                return False, "Se requiere 'memories': lista de dicts con 'content', 'type', 'timestamp'"

        if action == "compact":
            if not inputs.get("memories"):
                return False, "Se requiere 'memories' para compactar"

        return True, ""

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs["action"]

            if action == "stats":
                result = self._stats(inputs)
            elif action == "duplicates":
                result = self._find_duplicates(inputs)
            elif action == "cluster":
                result = self._cluster_memories(inputs)
            elif action == "compact":
                result = self._compact(inputs)
            elif action == "plan":
                result = self._compaction_plan(inputs)
            elif action == "prune":
                result = self._prune(inputs)
            else:
                result = {"error": f"Accion no implementada: {action}"}

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()

            has_error = isinstance(result, dict) and result.get("error") is not None
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }

        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"Memory compaction error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": elapsed,
            }

    # ── stats: estado general de la memoria ────────────────────────

    def _stats(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Calcula estadisticas sobre las memorias disponibles."""
        memories = inputs.get("memories", [])
        conversation_count = inputs.get("conversation_count", 0)

        if not memories:
            # Intentar obtener del memory_manager si esta disponible
            mm = inputs.get("memory_manager")
            if mm:
                memories = [m.to_dict() for m in mm.memories.values()]
                conversation_count = len(mm.conversation_history)

        now = datetime.now()
        by_type = defaultdict(int)
        ages = []
        total_chars = 0

        for mem in memories:
            by_type[mem.get("type", "unknown")] += 1
            total_chars += len(mem.get("content", ""))

            ts = mem.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                    age_days = (now - dt).days
                    ages.append(age_days)
                except (ValueError, TypeError):
                    pass

        stale_count = sum(1 for a in ages if a > STALE_DAYS)
        ancient_count = sum(1 for a in ages if a > ANCIENT_DAYS)

        return {
            "total_memories": len(memories),
            "conversation_history_size": conversation_count,
            "by_type": dict(by_type),
            "total_chars": total_chars,
            "avg_chars_per_memory": round(total_chars / max(len(memories), 1)),
            "age_distribution": {
                "fresh (< 7 days)": sum(1 for a in ages if a < 7),
                "recent (7-14 days)": sum(1 for a in ages if 7 <= a < STALE_DAYS),
                "stale (14-60 days)": stale_count,
                "ancient (60+ days)": ancient_count,
            },
            "oldest_days": max(ages) if ages else 0,
            "newest_days": min(ages) if ages else 0,
            "health": self._assess_health(len(memories), stale_count, ancient_count, total_chars),
        }

    @staticmethod
    def _assess_health(total: int, stale: int, ancient: int, chars: int) -> Dict[str, Any]:
        """Evalua la salud del sistema de memorias."""
        issues = []
        score = 100

        if total > 500:
            issues.append("Demasiadas memorias (>500), se recomienda compactar")
            score -= 20
        if stale > total * 0.4 and total > 10:
            issues.append(f"{stale} memorias obsoletas (>40%), considerar compactacion")
            score -= 15
        if ancient > 20:
            issues.append(f"{ancient} memorias antiguas (>60 dias), compactar urgente")
            score -= 25
        if chars > 500_000:
            issues.append(f"Uso alto de memoria: {chars // 1000}KB en texto")
            score -= 10

        if not issues:
            issues.append("Memoria en buen estado")

        return {
            "score": max(0, score),
            "status": "healthy" if score >= 70 else "needs_attention" if score >= 40 else "critical",
            "issues": issues,
        }

    # ── duplicates: detectar duplicados ────────────────────────────

    def _find_duplicates(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detecta memorias duplicadas o casi-duplicadas por similitud coseno.
        Requiere embeddings en las memorias.
        """
        memories = inputs["memories"]
        threshold = inputs.get("threshold", DUPLICATE_THRESHOLD)

        # Extraer embeddings
        indexed = []
        for i, mem in enumerate(memories):
            emb = mem.get("embedding")
            if emb and isinstance(emb, list):
                indexed.append((i, mem, np.array(emb)))

        if len(indexed) < 2:
            return {"duplicates": [], "total_checked": len(indexed), "note": "Insuficientes memorias con embeddings"}

        # Calcular similitudes por pares
        duplicate_groups = []
        seen = set()

        for i in range(len(indexed)):
            if i in seen:
                continue
            idx_i, mem_i, emb_i = indexed[i]
            group = [{"index": idx_i, "content": mem_i.get("content", "")[:200]}]

            for j in range(i + 1, len(indexed)):
                if j in seen:
                    continue
                idx_j, mem_j, emb_j = indexed[j]

                sim = self._cosine_similarity(emb_i, emb_j)
                if sim >= threshold:
                    group.append(
                        {
                            "index": idx_j,
                            "content": mem_j.get("content", "")[:200],
                            "similarity": round(float(sim), 4),
                        }
                    )
                    seen.add(j)

            if len(group) > 1:
                seen.add(i)
                duplicate_groups.append(
                    {
                        "group_size": len(group),
                        "memories": group,
                    }
                )

        total_duplicates = sum(g["group_size"] - 1 for g in duplicate_groups)

        return {
            "duplicate_groups": duplicate_groups[:20],  # Limitar output
            "total_duplicates": total_duplicates,
            "total_checked": len(indexed),
            "threshold": threshold,
            "can_save": total_duplicates,
            "recommendation": f"Se pueden eliminar {total_duplicates} memorias redundantes"
            if total_duplicates > 0
            else "No se encontraron duplicados significativos",
        }

    # ── cluster: agrupar por tema ──────────────────────────────────

    def _cluster_memories(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Agrupa memorias por similitud tematica usando clustering greedy.
        """
        memories = inputs["memories"]
        threshold = inputs.get("threshold", CLUSTER_THRESHOLD)

        # Extraer embeddings
        indexed = []
        for i, mem in enumerate(memories):
            emb = mem.get("embedding")
            if emb and isinstance(emb, list):
                indexed.append((i, mem, np.array(emb)))

        if len(indexed) < 2:
            return {"clusters": [], "note": "Insuficientes memorias con embeddings"}

        # Clustering greedy: asignar cada memoria al cluster mas cercano
        clusters: List[List[int]] = []
        centroids: List[np.ndarray] = []

        for idx, mem, emb in indexed:
            best_cluster = -1
            best_sim = 0

            for c_idx, centroid in enumerate(centroids):
                sim = self._cosine_similarity(emb, centroid)
                if sim > best_sim and sim >= threshold:
                    best_sim = sim
                    best_cluster = c_idx

            if best_cluster >= 0 and len(clusters[best_cluster]) < MAX_CLUSTER_SIZE:
                clusters[best_cluster].append(idx)
                # Actualizar centroide (media movil)
                n = len(clusters[best_cluster])
                centroids[best_cluster] = centroids[best_cluster] * ((n - 1) / n) + emb * (1 / n)
            else:
                clusters.append([idx])
                centroids.append(emb.copy())

        # Formatear resultado
        result_clusters = []
        for c_idx, member_indices in enumerate(clusters):
            members = []
            for m_idx in member_indices:
                mem = memories[m_idx]
                members.append(
                    {
                        "index": m_idx,
                        "content": mem.get("content", "")[:200],
                        "type": mem.get("type", "unknown"),
                        "timestamp": mem.get("timestamp", ""),
                    }
                )
            result_clusters.append(
                {
                    "cluster_id": c_idx,
                    "size": len(members),
                    "members": members,
                    "compactable": len(members) >= 3,
                }
            )

        # Ordenar: clusters mas grandes primero
        result_clusters.sort(key=lambda c: c["size"], reverse=True)

        compactable = sum(1 for c in result_clusters if c["compactable"])

        return {
            "clusters": result_clusters[:30],
            "total_clusters": len(result_clusters),
            "compactable_clusters": compactable,
            "singleton_memories": sum(1 for c in result_clusters if c["size"] == 1),
            "recommendation": f"{compactable} clusters con 3+ memorias pueden compactarse"
            if compactable > 0
            else "No hay clusters suficientemente grandes para compactar",
        }

    # ── compact: generar resumen compacto ──────────────────────────

    def _compact(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Genera un resumen compacto de un grupo de memorias.
        No usa LLM — hace compresion heuristica.
        Para compresion con LLM, el Mind debe post-procesar el resultado.
        """
        memories = inputs["memories"]
        strategy = inputs.get("strategy", "progressive")  # progressive | key_facts | merge

        if strategy == "progressive":
            return self._compact_progressive(memories)
        elif strategy == "key_facts":
            return self._compact_key_facts(memories)
        elif strategy == "merge":
            return self._compact_merge(memories)
        else:
            return {"error": f"Estrategia desconocida: {strategy}"}

    def _compact_progressive(self, memories: List[Dict]) -> Dict[str, Any]:
        """
        Compactacion progresiva (inspirada en claude-mem):
        - Nivel 1 (reciente): contenido completo
        - Nivel 2 (medio): primera oracion de cada memoria
        - Nivel 3 (antiguo): solo palabras clave y hechos
        """
        now = datetime.now()
        levels = {"full": [], "summary": [], "keywords": []}

        for mem in memories:
            content = mem.get("content", "")
            ts = mem.get("timestamp", "")

            try:
                dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                age_days = (now - dt).days
            except (ValueError, TypeError):
                age_days = 999

            if age_days < 7:
                levels["full"].append(content)
            elif age_days < STALE_DAYS:
                # Extraer primera oracion
                first_sentence = content.split(".")[0].strip()
                if first_sentence:
                    levels["summary"].append(first_sentence + ".")
            else:
                # Extraer palabras clave (>4 chars, no stopwords)
                words = set(
                    w.strip(".,;:!?()[]\"'") for w in content.split() if len(w) > 4 and w.lower() not in _STOPWORDS
                )
                if words:
                    levels["keywords"].append(", ".join(list(words)[:10]))

        # Construir texto compactado
        parts = []
        if levels["full"]:
            parts.append("## Recientes (detalle completo)")
            parts.extend(f"- {c}" for c in levels["full"])
        if levels["summary"]:
            parts.append("\n## Resumidos")
            parts.extend(f"- {s}" for s in levels["summary"])
        if levels["keywords"]:
            parts.append("\n## Conceptos clave")
            parts.extend(f"- {k}" for k in levels["keywords"])

        compacted_text = "\n".join(parts)
        original_chars = sum(len(m.get("content", "")) for m in memories)
        compacted_chars = len(compacted_text)

        return {
            "compacted_text": compacted_text,
            "original_count": len(memories),
            "original_chars": original_chars,
            "compacted_chars": compacted_chars,
            "compression_ratio": round(compacted_chars / max(original_chars, 1), 3),
            "levels": {k: len(v) for k, v in levels.items()},
            "strategy": "progressive",
            "llm_prompt": self._build_llm_compaction_prompt(memories),
        }

    def _compact_key_facts(self, memories: List[Dict]) -> Dict[str, Any]:
        """Extrae solo los hechos clave de las memorias."""
        facts = []
        for mem in memories:
            content = mem.get("content", "")
            # Heuristica: oraciones que contienen datos especificos
            sentences = [s.strip() for s in content.replace("\n", ". ").split(".") if s.strip()]
            for s in sentences:
                # Priorizar oraciones con numeros, nombres propios, o palabras clave
                has_data = any(c.isdigit() for c in s) or any(w[0].isupper() for w in s.split() if w)
                if has_data and len(s) > 15:
                    facts.append(s + ".")

        # Deduplicar facts similares
        unique_facts = list(dict.fromkeys(facts))[:30]

        original_chars = sum(len(m.get("content", "")) for m in memories)
        compacted_chars = sum(len(f) for f in unique_facts)

        return {
            "key_facts": unique_facts,
            "original_count": len(memories),
            "original_chars": original_chars,
            "compacted_chars": compacted_chars,
            "compression_ratio": round(compacted_chars / max(original_chars, 1), 3),
            "strategy": "key_facts",
        }

    def _compact_merge(self, memories: List[Dict]) -> Dict[str, Any]:
        """Merge simple: concatena contenidos unicos, elimina redundancia textual."""
        seen_sentences = set()
        merged_parts = []

        for mem in memories:
            content = mem.get("content", "")
            sentences = [s.strip() for s in content.replace("\n", ". ").split(".") if s.strip()]
            for s in sentences:
                normalized = s.lower().strip()
                if normalized not in seen_sentences and len(normalized) > 10:
                    seen_sentences.add(normalized)
                    merged_parts.append(s.strip())

        merged = ". ".join(merged_parts) + "." if merged_parts else ""
        original_chars = sum(len(m.get("content", "")) for m in memories)

        return {
            "merged_text": merged[:5000],
            "unique_sentences": len(merged_parts),
            "original_count": len(memories),
            "original_chars": original_chars,
            "compacted_chars": len(merged),
            "compression_ratio": round(len(merged) / max(original_chars, 1), 3),
            "strategy": "merge",
        }

    def _build_llm_compaction_prompt(self, memories: List[Dict]) -> str:
        """
        Construye el prompt para que el Mind use el LLM para comprimir.
        El Mind puede llamar a llm_router.call_llm() con este prompt.
        """
        memory_text = ""
        for i, mem in enumerate(memories, 1):
            content = mem.get("content", "")[:500]
            mtype = mem.get("type", "unknown")
            ts = mem.get("timestamp", "?")
            memory_text += f"\n[{i}] ({mtype}, {ts}): {content}"

        return f"""Comprime las siguientes {len(memories)} memorias en un resumen conciso.
Mantiene: hechos clave, decisiones, preferencias del usuario, datos especificos.
Elimina: redundancias, contexto obvio, detalles triviales.
Formato: lista de bullets con la informacion esencial.
Maximo: 500 caracteres.

MEMORIAS:{memory_text}

RESUMEN COMPACTADO:"""

    # ── plan: analisis completo ────────────────────────────────────

    def _compaction_plan(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analisis completo: que compactar, que borrar, que mantener.
        Combina stats + duplicates + clusters.
        """
        inputs["memories"]
        stats = self._stats(inputs)
        duplicates = self._find_duplicates(inputs)
        clusters = self._cluster_memories(inputs)

        # Generar recomendaciones
        actions = []

        if duplicates.get("total_duplicates", 0) > 0:
            actions.append(
                {
                    "action": "remove_duplicates",
                    "priority": "high",
                    "impact": f"Eliminar {duplicates['total_duplicates']} memorias redundantes",
                    "estimated_savings": f"{duplicates['total_duplicates']} memorias",
                }
            )

        compactable = clusters.get("compactable_clusters", 0)
        if compactable > 0:
            actions.append(
                {
                    "action": "compact_clusters",
                    "priority": "medium",
                    "impact": f"Compactar {compactable} grupos tematicos en resumenes",
                    "estimated_savings": f"~{compactable * 3} memorias -> {compactable} resumenes",
                }
            )

        ancient = stats.get("age_distribution", {}).get("ancient (60+ days)", 0)
        if ancient > 5:
            actions.append(
                {
                    "action": "archive_ancient",
                    "priority": "low",
                    "impact": f"Archivar {ancient} memorias antiguas (>60 dias)",
                    "estimated_savings": f"{ancient} memorias comprimidas",
                }
            )

        if not actions:
            actions.append(
                {
                    "action": "none",
                    "priority": "none",
                    "impact": "Memoria en buen estado, no se requieren acciones",
                    "estimated_savings": "N/A",
                }
            )

        return {
            "stats": stats,
            "duplicate_summary": {
                "found": duplicates.get("total_duplicates", 0),
                "groups": len(duplicates.get("duplicate_groups", [])),
            },
            "cluster_summary": {
                "total": clusters.get("total_clusters", 0),
                "compactable": compactable,
                "singletons": clusters.get("singleton_memories", 0),
            },
            "recommended_actions": actions,
            "overall_health": stats.get("health", {}),
        }

    # ── prune: eliminar duplicados del RAM ─────────────────────────

    def _prune(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Elimina memorias duplicadas del MemoryManager (cache RAM).
        Requiere que se pase el memory_manager como input.
        """
        mm = inputs.get("memory_manager")
        if mm is None:
            return {"error": "Se requiere 'memory_manager' para prune"}

        threshold = inputs.get("threshold", DUPLICATE_THRESHOLD)

        # Convertir memorias a formato de analisis
        memories_list = []
        mem_ids = []
        for mid, mem in mm.memories.items():
            memories_list.append(
                {
                    "content": mem.content,
                    "type": mem.type,
                    "timestamp": mem.timestamp.isoformat(),
                    "embedding": mem.embedding,
                }
            )
            mem_ids.append(mid)

        # Encontrar duplicados
        dup_result = self._find_duplicates({"memories": memories_list, "threshold": threshold})
        to_remove = set()

        for group in dup_result.get("duplicate_groups", []):
            members = group.get("memories", [])
            # Mantener el primero, eliminar el resto
            for member in members[1:]:
                idx = member.get("index")
                if idx is not None and idx < len(mem_ids):
                    to_remove.add(mem_ids[idx])

        # Eliminar del memory manager
        removed = 0
        for mid in to_remove:
            if mid in mm.memories:
                del mm.memories[mid]
                removed += 1

        return {
            "removed": removed,
            "remaining": len(mm.memories),
            "ids_removed": list(to_remove)[:20],
        }

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Similitud coseno entre dos vectores."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a < 1e-8 or norm_b < 1e-8:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


# ── Stopwords basicas (espanol + ingles) ───────────────────────────
_STOPWORDS = {
    # Espanol
    "como",
    "para",
    "este",
    "esta",
    "esos",
    "esas",
    "unos",
    "unas",
    "pero",
    "sino",
    "porque",
    "cuando",
    "donde",
    "mientras",
    "desde",
    "hasta",
    "entre",
    "sobre",
    "hacia",
    "contra",
    "segun",
    "durante",
    "tiene",
    "hacer",
    "puede",
    "haber",
    "estar",
    "siendo",
    "seria",
    "tambien",
    "mismo",
    "antes",
    "despues",
    "ahora",
    "entonces",
    "todavia",
    "aunque",
    "siempre",
    "nunca",
    # Ingles
    "this",
    "that",
    "these",
    "those",
    "with",
    "from",
    "have",
    "been",
    "were",
    "will",
    "would",
    "could",
    "should",
    "about",
    "which",
    "their",
    "there",
    "after",
    "before",
    "between",
    "through",
    "during",
    "without",
    "again",
    "other",
    "another",
    "being",
    "because",
    "while",
    "where",
    "still",
    "might",
}
