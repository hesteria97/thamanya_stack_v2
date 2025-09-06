import os
import sys
import time
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

# Add parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from shared.config import setup_logging


class EventModel(BaseModel):
    """Pydantic model for event validation."""
    id: int
    content_id: str
    user_id: str
    event_type: str
    event_ts: str
    device: Optional[str] = None
    content_type: Optional[str] = None
    length_seconds: Optional[int] = None
    engagement_seconds: Optional[float] = None
    engagement_pct: Optional[float] = None
    raw_payload: Optional[str] = None


@dataclass
class ApiConfig:
    """API configuration."""
    port: int = 8088
    secret_key: str = "default-secret-key"
    enable_rate_limiting: bool = True
    max_requests_per_minute: int = 1000
    
    @classmethod
    def from_env(cls) -> 'ApiConfig':
        return cls(
            port=int(os.getenv('EXTERNAL_API_PORT', '8088')),
            secret_key=os.getenv('API_SECRET_KEY', 'default-secret-key'),
            enable_rate_limiting=os.getenv('ENABLE_RATE_LIMITING', 'true').lower() == 'true',
            max_requests_per_minute=int(os.getenv('MAX_REQUESTS_PER_MINUTE', '1000'))
        )


class ExternalAPI:
    """Professional external API with validation and monitoring."""
    
    def __init__(self):
        self.logger = setup_logging(__name__)
        self.config = ApiConfig.from_env()
        
        # Flask app setup
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = self.config.secret_key
        
        # Request tracking for rate limiting
        self.request_counts: Dict[str, Dict[str, int]] = {}
        
        # Metrics
        self.metrics = {
            'total_requests': 0,
            'valid_events': 0,
            'invalid_events': 0,
            'rate_limited': 0
        }
        
        self._setup_routes()
        self.logger.info("ExternalAPI initialized")
    
    def _setup_routes(self) -> None:
        """Set up Flask routes."""
        
        @self.app.before_request
        def before_request():
            """Pre-request middleware."""
            if self.config.enable_rate_limiting:
                if not self._check_rate_limit():
                    self.metrics['rate_limited'] += 1
                    return jsonify({
                        'error': 'Rate limit exceeded',
                        'max_requests_per_minute': self.config.max_requests_per_minute
                    }), 429
            
            self.metrics['total_requests'] += 1
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint."""
            return jsonify({
                'status': 'healthy',
                'timestamp': time.time(),
                'metrics': self.metrics
            }), 200
        
        @self.app.route('/metrics', methods=['GET'])
        def metrics_endpoint():
            """Prometheus-style metrics endpoint."""
            metrics_text = []
            for key, value in self.metrics.items():
                metrics_text.append(f'external_api_{key} {value}')
            
            return '\n'.join(metrics_text), 200, {'Content-Type': 'text/plain'}
        
        @self.app.route('/events', methods=['POST'])
        def receive_events():
            """Receive and validate events."""
            try:
                # Get JSON data
                data = request.get_json(force=True, silent=True)
                if not data:
                    self.metrics['invalid_events'] += 1
                    return jsonify({
                        'error': 'Invalid JSON payload'
                    }), 400
                
                # Validate event structure
                try:
                    event = EventModel(**data)
                    self.metrics['valid_events'] += 1
                    
                    self.logger.info(f"Received valid event: {event.id} - {event.event_type}")
                    
                    # Process event (placeholder for actual processing)
                    self._process_event(event.dict())
                    
                    return jsonify({
                        'status': 'success',
                        'event_id': event.id,
                        'message': 'Event processed successfully'
                    }), 200
                    
                except ValidationError as e:
                    self.metrics['invalid_events'] += 1
                    self.logger.warning(f"Invalid event received: {e}")
                    return jsonify({
                        'error': 'Event validation failed',
                        'details': e.errors()
                    }), 400
                
            except Exception as e:
                self.logger.error(f"Error processing event: {e}")
                return jsonify({
                    'error': 'Internal server error',
                    'message': str(e)
                }), 500
        
        @self.app.route('/', methods=['GET'])
        def index():
            """Root endpoint."""
            return jsonify({
                'service': 'Thamanya External API',
                'version': '2.0',
                'status': 'running'
            }), 200
        
        @self.app.errorhandler(404)
        def not_found(error):
            return jsonify({'error': 'Endpoint not found'}), 404
        
        @self.app.errorhandler(500)
        def internal_error(error):
            self.logger.error(f"Internal server error: {error}")
            return jsonify({'error': 'Internal server error'}), 500
    
    def _check_rate_limit(self) -> bool:
        """Simple in-memory rate limiting."""
        client_ip = request.remote_addr
        current_minute = int(time.time() // 60)
        
        if client_ip not in self.request_counts:
            self.request_counts[client_ip] = {}
        
        # Clean old entries
        for minute in list(self.request_counts[client_ip].keys()):
            if minute < current_minute - 1:
                del self.request_counts[client_ip][minute]
        
        # Count requests in current minute
        current_count = self.request_counts[client_ip].get(current_minute, 0)
        
        if current_count >= self.config.max_requests_per_minute:
            return False
        
        # Increment counter
        self.request_counts[client_ip][current_minute] = current_count + 1
        return True
    
    def _process_event(self, event_data: Dict[str, Any]) -> None:
        """Process received event (placeholder for actual business logic)."""
        # Here you could add:
        # - Additional validation
        # - Event enrichment
        # - Forwarding to other systems
        # - Storage in database
        # - Trigger downstream processes
        
        self.logger.debug(f"Processing event: {event_data.get('id')}")
        
        # Example: Log important events
        if event_data.get('event_type') == 'finish':
            self.logger.info(f"Content {event_data.get('content_id')} completed by user")
    
    def run(self, host: str = '127.0.0.1', port: Optional[int] = None, debug: bool = False):
        """Run the Flask application."""
        port = port or self.config.port
        self.logger.info(f"Starting External API on {host}:{port}")
        
        self.app.run(
            host=host,
            port=port,
            debug=debug,
            threaded=True
        )


# Create global app instance for WSGI servers
api_instance = ExternalAPI()
app = api_instance.app

if __name__ == "__main__":
    # Run in development mode
    import os
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    api_instance.run(debug=debug_mode)