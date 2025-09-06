import os
import sys
import time
import uuid
import random
import signal
from typing import Optional, List, Dict, Any
from threading import Event, Thread
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Add parent directory to Python path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)
# Also add /app as fallback for container environment
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

import psycopg2
from psycopg2.extras import Json, register_uuid
from shared.config import DatabaseConfig, AppConfig, setup_logging

# Prometheus Metrics
EVENTS_GENERATED = Counter('events_generated_total', 'Total number of events generated')
EVENT_GENERATION_DURATION = Histogram('event_generation_duration_seconds', 'Time spent generating events')
DATABASE_ERRORS = Counter('database_errors_total', 'Total number of database errors', ['error_type'])
DATABASE_CONNECTIONS = Gauge('database_connections_active', 'Number of active database connections')
CONTENT_POOL_SIZE = Gauge('content_pool_size', 'Number of content items in pool')


@dataclass
class EventGeneratorConfig:
    """Configuration for the event generator."""
    min_interval: float = 1.0  # Minimum seconds between events
    max_interval: float = 10.0  # Maximum seconds between events
    batch_size: int = 1  # Number of events per batch
    content_pool_size: int = 100  # Number of content items to maintain
    
    @classmethod
    def from_env(cls) -> 'EventGeneratorConfig':
        return cls(
            min_interval=float(os.getenv('GEN_MIN_INTERVAL', '1.0')),
            max_interval=float(os.getenv('GEN_MAX_INTERVAL', '10.0')),
            batch_size=int(os.getenv('GEN_BATCH_SIZE', '1')),
            content_pool_size=int(os.getenv('GEN_CONTENT_POOL_SIZE', '100'))
        )


class EventGenerator:
    """Professional event generator with proper error handling and graceful shutdown."""
    
    EVENTS = ["play", "pause", "finish", "click"]
    DEVICES = ["ios", "android", "web-chrome", "web-safari"]
    CONTENT_TYPES = ["podcast", "newsletter", "video"]
    
    def __init__(self):
        self.logger = setup_logging(__name__)
        self.shutdown_event = Event()
        
        # Load configuration
        self.db_config = DatabaseConfig.from_env()
        self.app_config = AppConfig.from_env()
        self.gen_config = EventGeneratorConfig.from_env()
        
        # Database connection
        self.connection: Optional[psycopg2.extensions.connection] = None
        self.cursor: Optional[psycopg2.extensions.cursor] = None
        
        # Content pool for faster event generation
        self.content_pool: List[str] = []
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Start Prometheus metrics server
        try:
            start_http_server(9091)
            self.logger.info("Prometheus metrics server started on port 9091")
        except Exception as e:
            self.logger.warning(f"Failed to start metrics server: {e}")
        
        self.logger.info("EventGenerator initialized")
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_event.set()
    
    def _connect_database(self) -> psycopg2.extensions.connection:
        """Connect to PostgreSQL with retry logic."""
        max_retries = 30
        for attempt in range(max_retries):
            try:
                connection = psycopg2.connect(
                    host=self.db_config.host,
                    port=self.db_config.port,
                    dbname=self.db_config.database,
                    user=self.db_config.user,
                    password=self.db_config.password,
                    connect_timeout=10
                )
                connection.autocommit = True
                register_uuid(conn_or_curs=connection)
                DATABASE_CONNECTIONS.set(1)
                
                self.logger.info("Successfully connected to PostgreSQL")
                return connection
                
            except Exception as e:
                DATABASE_ERRORS.labels(error_type='connection').inc()
                self.logger.warning(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    raise
    
    def _ensure_content_pool(self) -> None:
        """Ensure we have enough content in our pool."""
        try:
            # Check current content count
            self.cursor.execute("SELECT COUNT(*) FROM content")
            current_count = self.cursor.fetchone()[0]
            
            needed = max(0, self.gen_config.content_pool_size - current_count)
            
            if needed > 0:
                self.logger.info(f"Creating {needed} content items")
                
                for _ in range(needed):
                    self._create_content_item()
            
            # Refresh content pool
            self.cursor.execute("SELECT id FROM content ORDER BY random() LIMIT %s", 
                              (self.gen_config.content_pool_size,))
            self.content_pool = [str(row[0]) for row in self.cursor.fetchall()]
            CONTENT_POOL_SIZE.set(len(self.content_pool))
            
            self.logger.info(f"Content pool refreshed with {len(self.content_pool)} items")
            
        except Exception as e:
            self.logger.error(f"Error managing content pool: {e}")
            raise
    
    def _create_content_item(self) -> str:
        """Create a single content item."""
        content_id = uuid.uuid4()
        slug = f"auto-{uuid.uuid4().hex[:8]}"
        title = f"Auto Generated Content {random.randint(1000, 9999)}"
        content_type = random.choice(self.CONTENT_TYPES)
        length_seconds = random.randint(60, 1800)
        
        self.cursor.execute("""
            INSERT INTO content (id, slug, title, content_type, length_seconds) 
            VALUES (%s, %s, %s, %s, %s)
        """, (content_id, slug, title, content_type, length_seconds))
        
        return str(content_id)
    
    def _generate_event(self) -> Dict[str, Any]:
        """Generate a realistic event."""
        if not self.content_pool:
            self._ensure_content_pool()
        
        content_id = random.choice(self.content_pool)
        user_id = uuid.uuid4()
        event_type = random.choice(self.EVENTS)
        device = random.choice(self.DEVICES)
        
        # Generate realistic duration based on event type
        if event_type in ("click", "pause"):
            duration_ms = None
        else:
            duration_ms = random.randint(1000, 180000)
        
        return {
            'content_id': content_id,
            'user_id': str(user_id),
            'event_type': event_type,
            'duration_ms': duration_ms,
            'device': device,
            'raw_payload': {"generator": "v2", "timestamp": time.time()}
        }
    
    def _insert_events(self, events: List[Dict[str, Any]]) -> int:
        """Insert multiple events in a single transaction."""
        if not events:
            return 0
        
        try:
            insert_sql = """
                INSERT INTO engagement_events 
                (content_id, user_id, event_type, event_ts, duration_ms, device, raw_payload) 
                VALUES (%(content_id)s, %(user_id)s, %(event_type)s, now(), 
                        %(duration_ms)s, %(device)s, %(raw_payload)s)
            """
            
            # Prepare data for batch insert
            batch_data = []
            for event in events:
                batch_data.append({
                    'content_id': event['content_id'],
                    'user_id': event['user_id'],
                    'event_type': event['event_type'],
                    'duration_ms': event['duration_ms'],
                    'device': event['device'],
                    'raw_payload': Json(event['raw_payload'])
                })
            
            with EVENT_GENERATION_DURATION.time():
                self.cursor.executemany(insert_sql, batch_data)
            
            EVENTS_GENERATED.inc(len(events))
            return len(events)
            
        except Exception as e:
            DATABASE_ERRORS.labels(error_type='insert').inc()
            self.logger.error(f"Error inserting events: {e}")
            raise
    
    def run(self) -> None:
        """Main event generation loop."""
        try:
            # Establish database connection
            self.connection = self._connect_database()
            self.cursor = self.connection.cursor()
            
            # Initialize content pool
            self._ensure_content_pool()
            
            self.logger.info("Starting event generation loop")
            
            events_generated = 0
            content_refresh_counter = 0
            
            while not self.shutdown_event.is_set():
                try:
                    # Generate batch of events
                    events = []
                    for _ in range(self.gen_config.batch_size):
                        events.append(self._generate_event())
                    
                    # Insert events
                    inserted = self._insert_events(events)
                    events_generated += inserted
                    
                    self.logger.info(f"Generated {inserted} events (total: {events_generated})")
                    
                    # Periodically refresh content pool
                    content_refresh_counter += 1
                    if content_refresh_counter >= 100:
                        self._ensure_content_pool()
                        content_refresh_counter = 0
                    
                    # Wait before next batch
                    interval = random.uniform(self.gen_config.min_interval, self.gen_config.max_interval)
                    self.shutdown_event.wait(interval)
                
                except Exception as e:
                    self.logger.error(f"Error in generation loop: {e}")
                    time.sleep(5)  # Brief pause before retry
            
        except Exception as e:
            self.logger.error(f"Fatal error in EventGenerator: {e}")
            raise
        finally:
            self._cleanup()
    
    def _cleanup(self) -> None:
        """Clean up database resources."""
        self.logger.info("Cleaning up resources")
        
        if self.cursor:
            try:
                self.cursor.close()
                self.logger.info("Database cursor closed")
            except Exception as e:
                self.logger.warning(f"Error closing cursor: {e}")
        
        if self.connection:
            try:
                self.connection.close()
                self.logger.info("Database connection closed")
            except Exception as e:
                self.logger.warning(f"Error closing connection: {e}")


if __name__ == "__main__":
    generator = EventGenerator()
    try:
        generator.run()
    except KeyboardInterrupt:
        generator.logger.info("Received keyboard interrupt")
    except Exception as e:
        generator.logger.error(f"Unhandled exception: {e}")
        sys.exit(1)