"""
Performance-based router with adaptive scoring and history tracking.
Selects best provider based on historical performance metrics.
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Metrics for a single execution."""
    timestamp: float
    latency_ms: float
    success: bool
    error_type: Optional[str] = None
    tokens_used: Optional[int] = None


@dataclass
class RoutePerformance:
    """Performance data for a specific route/model."""
    route_id: str
    metrics_history: List[PerformanceMetrics]
    ema_latency: float = 0.0
    ema_success_rate: float = 1.0
    ema_error_rate: float = 0.0
    current_score: float = 1.0
    total_requests: int = 0
    last_updated: float = 0.0


class PerformanceRouter:
    """
    Router that selects providers based on historical performance.
    Uses Exponential Moving Average (EMA) for adaptive scoring.
    """
    
    def __init__(
        self,
        history_file: str = "output/.router_history.json",
        max_history_size: int = 100,
        ema_alpha: float = 0.1,
        score_weights: Dict[str, float] = None
    ):
        """
        Initialize performance router.
        
        Args:
            history_file: Path to persist history
            max_history_size: Max metrics to keep per route
            ema_alpha: EMA smoothing factor (0-1, higher = more recent weight)
            score_weights: Weights for scoring components
        """
        self.history_file = Path(history_file)
        self.max_history_size = max_history_size
        self.ema_alpha = ema_alpha
        self.score_weights = score_weights or {
            "success": 0.6,
            "latency": 0.3,
            "error": 0.1
        }
        
        self._routes: Dict[str, RoutePerformance] = {}
        self._lock = threading.RLock()
        
        # Ensure output directory exists
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing history
        self._load_history()
        
    def _load_history(self):
        """Load performance history from file."""
        if not self.history_file.exists():
            logger.info("No history file found, starting fresh")
            return
            
        try:
            with open(self.history_file, 'r') as f:
                data = json.load(f)
                
            for route_id, route_data in data.items():
                metrics = [
                    PerformanceMetrics(**m) 
                    for m in route_data.get("metrics_history", [])
                ]
                
                self._routes[route_id] = RoutePerformance(
                    route_id=route_id,
                    metrics_history=metrics[-self.max_history_size:],
                    ema_latency=route_data.get("ema_latency", 0.0),
                    ema_success_rate=route_data.get("ema_success_rate", 1.0),
                    ema_error_rate=route_data.get("ema_error_rate", 0.0),
                    current_score=route_data.get("current_score", 1.0),
                    total_requests=route_data.get("total_requests", 0),
                    last_updated=route_data.get("last_updated", 0.0)
                )
                
            logger.info(f"Loaded history for {len(self._routes)} routes")
            
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
    
    def _save_history(self):
        """Save performance history to file."""
        try:
            data = {}
            for route_id, route in self._routes.items():
                # Convert metrics to dict format
                metrics_data = [
                    asdict(m) for m in route.metrics_history
                ]
                
                data[route_id] = {
                    "metrics_history": metrics_data,
                    "ema_latency": route.ema_latency,
                    "ema_success_rate": route.ema_success_rate,
                    "ema_error_rate": route.ema_error_rate,
                    "current_score": route.current_score,
                    "total_requests": route.total_requests,
                    "last_updated": route.last_updated
                }
            
            # Write atomically
            temp_file = self.history_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.history_file)
            
        except Exception as e:
            logger.error(f"Failed to save history: {e}")
    
    def record_execution(
        self,
        route_id: str,
        latency_ms: float,
        success: bool,
        error_type: Optional[str] = None,
        tokens_used: Optional[int] = None
    ):
        """
        Record execution metrics and update performance scores.
        
        Args:
            route_id: Identifier for the route/model
            latency_ms: Execution time in milliseconds
            success: Whether execution succeeded
            error_type: Type of error if failed
            tokens_used: Number of tokens consumed
        """
        with self._lock:
            # Get or create route performance
            if route_id not in self._routes:
                self._routes[route_id] = RoutePerformance(
                    route_id=route_id,
                    metrics_history=[],
                    ema_success_rate=0.5  # Start neutral for new routes
                )
            
            route = self._routes[route_id]
            
            # Add new metric
            metric = PerformanceMetrics(
                timestamp=time.time(),
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
                tokens_used=tokens_used
            )
            route.metrics_history.append(metric)
            
            # Trim history if needed
            if len(route.metrics_history) > self.max_history_size:
                route.metrics_history = route.metrics_history[-self.max_history_size:]
            
            # Update EMAs
            self._update_emas(route, metric)
            
            # Update score
            self._update_score(route)
            
            # Update metadata
            route.total_requests += 1
            route.last_updated = time.time()
            
            # Save periodically (every 10 requests)
            if route.total_requests % 10 == 0:
                self._save_history()
                
            logger.debug(f"Route '{route_id}': latency={latency_ms:.1f}ms, "
                        f"success={success}, score={route.current_score:.3f}")
    
    def _update_emas(self, route: RoutePerformance, metric: PerformanceMetrics):
        """Update exponential moving averages."""
        alpha = self.ema_alpha
        
        # Update latency EMA (only for successful requests)
        if metric.success:
            if route.ema_latency == 0:
                route.ema_latency = metric.latency_ms
            else:
                route.ema_latency = (alpha * metric.latency_ms + 
                                    (1 - alpha) * route.ema_latency)
        
        # Update success rate EMA
        success_value = 1.0 if metric.success else 0.0
        route.ema_success_rate = (alpha * success_value + 
                                  (1 - alpha) * route.ema_success_rate)
        
        # Update error rate EMA
        error_value = 0.0 if metric.success else 1.0
        route.ema_error_rate = (alpha * error_value + 
                               (1 - alpha) * route.ema_error_rate)
    
    def _update_score(self, route: RoutePerformance):
        """Calculate adaptive performance score."""
        # Normalize latency (assuming 1000ms is baseline)
        normalized_latency = min(route.ema_latency / 1000.0, 2.0)
        
        # Calculate weighted score
        score = (
            self.score_weights["success"] * route.ema_success_rate -
            self.score_weights["latency"] * normalized_latency -
            self.score_weights["error"] * route.ema_error_rate
        )
        
        # Ensure score is in reasonable range [0, 1]
        route.current_score = max(0.0, min(1.0, score))
    
    def select_best_route(
        self,
        available_routes: List[str],
        exploration_rate: float = 0.1
    ) -> Tuple[str, float]:
        """
        Select best route based on performance scores.
        
        Args:
            available_routes: List of route IDs to choose from
            exploration_rate: Probability of random selection (0-1)
            
        Returns:
            Tuple of (selected_route_id, score)
        """
        with self._lock:
            if not available_routes:
                raise ValueError("No available routes provided")
            
            # Exploration vs exploitation
            if np.random.random() < exploration_rate:
                # Random selection for exploration
                selected = np.random.choice(available_routes)
                score = self._routes.get(selected, RoutePerformance(
                    route_id=selected,
                    metrics_history=[]
                )).current_score
                logger.debug(f"Exploration: randomly selected '{selected}'")
                return selected, score
            
            # Get scores for available routes
            route_scores = []
            for route_id in available_routes:
                if route_id in self._routes:
                    score = self._routes[route_id].current_score
                else:
                    # New routes get neutral score
                    score = 0.5
                route_scores.append((route_id, score))
            
            # Sort by score (descending)
            route_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Select best
            selected, score = route_scores[0]
            logger.debug(f"Selected route '{selected}' with score {score:.3f}")
            
            return selected, score
    
    def get_route_stats(self, route_id: str) -> Optional[Dict[str, Any]]:
        """Get performance statistics for a specific route."""
        with self._lock:
            if route_id not in self._routes:
                return None
                
            route = self._routes[route_id]
            
            # Calculate recent stats
            recent_metrics = route.metrics_history[-10:]  # Last 10
            if recent_metrics:
                recent_success_rate = sum(
                    1 for m in recent_metrics if m.success
                ) / len(recent_metrics)
                recent_avg_latency = np.mean([
                    m.latency_ms for m in recent_metrics if m.success
                ]) if any(m.success for m in recent_metrics) else 0
            else:
                recent_success_rate = 0
                recent_avg_latency = 0
            
            return {
                "route_id": route_id,
                "current_score": route.current_score,
                "ema_latency_ms": route.ema_latency,
                "ema_success_rate": route.ema_success_rate,
                "ema_error_rate": route.ema_error_rate,
                "total_requests": route.total_requests,
                "recent_success_rate": recent_success_rate,
                "recent_avg_latency_ms": recent_avg_latency,
                "last_updated": datetime.fromtimestamp(route.last_updated).isoformat()
                if route.last_updated else None
            }
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get performance statistics for all routes."""
        with self._lock:
            stats = {}
            for route_id in self._routes:
                route_stats = self.get_route_stats(route_id)
                if route_stats:
                    stats[route_id] = route_stats
            return stats
    
    def reset_route(self, route_id: str):
        """Reset performance data for a specific route."""
        with self._lock:
            if route_id in self._routes:
                del self._routes[route_id]
                self._save_history()
                logger.info(f"Reset performance data for route '{route_id}'")
    
    def reset_all(self):
        """Reset all performance data."""
        with self._lock:
            self._routes.clear()
            if self.history_file.exists():
                self.history_file.unlink()
            logger.info("Reset all performance data")


# Global router instance
performance_router = PerformanceRouter()