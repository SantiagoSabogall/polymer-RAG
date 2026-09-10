"""
monitoring.py - Monitoreo y Métricas del Pipeline
=================================================
Proporciona logging estructurado, tracking de costos,
y métricas de rendimiento.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class StructuredLogger:
    """Logger con formato JSON estructurado."""
    
    def __init__(self, name):
        self.logger = logging.getLogger(name)
    
    def log_event(self, event_type, data):
        """Log en formato JSON estructurado."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "data": data
        }
        self.logger.info(json.dumps(event))
    
    def log_pipeline_start(self, total_pdfs):
        """Registra el inicio del pipeline."""
        self.log_event("pipeline_start", {"total_pdfs": total_pdfs})
    
    def log_pdf_processed(self, pdf_key, success, duration, tokens=None):
        """Registra el procesamiento de un PDF."""
        self.log_event("pdf_processed", {
            "pdf_key": pdf_key,
            "success": success,
            "duration_seconds": duration,
            "tokens": tokens
        })
    
    def log_pipeline_end(self, stats):
        """Registra el fin del pipeline."""
        self.log_event("pipeline_end", stats)


class CostTracker:
    """Tracking de costos de llamadas al LLM."""
    
    PRICING = {
        "openai/gpt-5.6-luna": {"input": 0.20, "output": 1.20},
        "deepseek/deepseek-v4-flash-0731": {"input": 0.05, "output": 0.10},
        "google/gemini-3.8-flash": {"input": 0.75, "output": 3.75},
        "z-ai/glm-5.3-flash": {"input": 0.075, "output": 0.25},
    }
    
    def __init__(self):
        self.costs = []
    
    def add_cost(self, model, tokens_in, tokens_out, latency):
        """Registra el costo de una llamada al LLM."""
        if model in self.PRICING:
            price = self.PRICING[model]
            cost_in = (tokens_in / 1_000_000) * price["input"]
            cost_out = (tokens_out / 1_000_000) * price["output"]
            total_cost = cost_in + cost_out
            
            self.costs.append({
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": total_cost,
                "latency": latency
            })
            return total_cost
        return 0
    
    def get_total_cost(self):
        """Retorna el costo total acumulado."""
        return sum(c["cost"] for c in self.costs)
    
    def get_summary(self):
        """Retorna un resumen de costos."""
        if not self.costs:
            return {"total_cost": 0, "total_calls": 0, "avg_latency": 0}
        
        return {
            "total_cost": self.get_total_cost(),
            "total_calls": len(self.costs),
            "avg_latency": sum(c["latency"] for c in self.costs) / len(self.costs)
        }


class PerformanceMetrics:
    """Métricas de rendimiento del pipeline."""
    
    def __init__(self):
        self.metrics = {
            "phase1": {"start": None, "end": None, "pdfs_processed": 0, "errors": 0},
            "phase2": {"start": None, "end": None, "pdfs_processed": 0, "errors": 0, "total_tokens": 0},
            "phase3": {"start": None, "end": None, "records_inserted": 0, "errors": 0}
        }
    
    def start_phase(self, phase):
        """Registra el inicio de una fase."""
        self.metrics[phase]["start"] = datetime.utcnow()
    
    def end_phase(self, phase):
        """Registra el fin de una fase."""
        self.metrics[phase]["end"] = datetime.utcnow()
    
    def increment(self, phase, metric):
        """Incrementa un contador en una fase."""
        self.metrics[phase][metric] += 1
    
    def add_tokens(self, count):
        """Agrega tokens al total de la fase 2."""
        self.metrics["phase2"]["total_tokens"] += count
    
    def get_duration(self, phase):
        """Retorna la duración de una fase en segundos."""
        start = self.metrics[phase]["start"]
        end = self.metrics[phase]["end"]
        if start and end:
            return (end - start).total_seconds()
        return 0
    
    def get_summary(self):
        """Retorna un resumen de todas las métricas."""
        return {
            "phase1_duration": self.get_duration("phase1"),
            "phase1_pdfs": self.metrics["phase1"]["pdfs_processed"],
            "phase1_errors": self.metrics["phase1"]["errors"],
            "phase2_duration": self.get_duration("phase2"),
            "phase2_pdfs": self.metrics["phase2"]["pdfs_processed"],
            "phase2_errors": self.metrics["phase2"]["errors"],
            "phase2_tokens": self.metrics["phase2"]["total_tokens"],
            "phase3_duration": self.get_duration("phase3"),
            "phase3_records": self.metrics["phase3"]["records_inserted"],
            "phase3_errors": self.metrics["phase3"]["errors"]
        }
