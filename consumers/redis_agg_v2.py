import sys
import os
import time
import json
import signal
from typing import Dict, Any, Optional
from threading import Event

# Add parent directory to Python path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)
# Also add /app as fallback for container environment
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

from kafka import KafkaConsumer
import redis
from shared.config import KafkaConfig, RedisConfig, AppConfig, setup_logging
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Prometheus Metrics
MESSAGES_PROCESSED = Counter('messages_processed_total', 'Total number of messages processed')
REDIS_OPERATIONS = Counter('redis_operations_total', 'Total number of Redis operations', ['operation_type'])
MESSAGE_PROCESSING_DURATION = Histogram('message_processing_duration_seconds', 'Time spent processing messages')
KAFKA_LAG = Gauge('kafka_consumer_lag', 'Current consumer lag')
REDIS_CONNECTION_STATUS = Gauge('redis_connection_active', 'Redis connection status (1=connected, 0=disconnected)')
KAFKA_CONNECTION_STATUS = Gauge('kafka_connection_active', 'Kafka connection status (1=connected, 0=disconnected)')


class RedisAggregator:
    """Professional Redis aggregation service with proper error handling and graceful shutdown."""
    
    def __init__(self):
        self.logger = setup_logging(__name__)
        self.shutdown_event = Event()
        
        # Load configuration
        self.kafka_config = KafkaConfig.from_env()
        self.redis_config = RedisConfig.from_env()
        self.app_config = AppConfig.from_env()
        
        # Initialize connections
        self.consumer: Optional[KafkaConsumer] = None
        self.redis_client: Optional[redis.Redis] = None
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Start Prometheus metrics server
        try:
            start_http_server(9092)
            self.logger.info("Prometheus metrics server started on port 9092")
        except Exception as e:
            self.logger.warning(f"Failed to start metrics server: {e}")
        
        self.logger.info("RedisAggregator initialized")
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_event.set()
    
    def _connect_kafka(self) -> KafkaConsumer:
        """Establish Kafka connection with retry logic."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                consumer = KafkaConsumer(
                    'thm.enriched.events',
                    bootstrap_servers=self.kafka_config.bootstrap_servers,
                    group_id='redis-agg',
                    enable_auto_commit=False,  # Manual commit for better control
                    auto_offset_reset='earliest',
                    value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                    client_id='redis-agg-consumer',
                    consumer_timeout_ms=1000  # Allow checking for shutdown
                )
                KAFKA_CONNECTION_STATUS.set(1)
                self.logger.info("Successfully connected to Kafka")
                return consumer
            except Exception as e:
                self.logger.warning(f"Kafka connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise
    
    def _connect_redis(self) -> redis.Redis:
        """Establish Redis connection with retry logic."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                client = redis.Redis(
                    host=self.redis_config.host,
                    port=self.redis_config.port,
                    password=self.redis_config.password,
                    db=0,
                    decode_responses=False,  # Keep binary for compatibility
                    socket_connect_timeout=5,
                    socket_timeout=5
                )
                # Test connection
                client.ping()
                REDIS_CONNECTION_STATUS.set(1)
                self.logger.info("Successfully connected to Redis")
                return client
            except Exception as e:
                self.logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
    
    @staticmethod
    def _calculate_score(event_type: str) -> float:
        """Calculate engagement score for different event types."""
        scores = {
            'play': 1.0,
            'finish': 1.0,
            'click': 0.2
        }
        return scores.get(event_type, 0.0)
    
    def _process_event(self, record: Dict[str, Any]) -> bool:
        """Process a single event with atomic Redis operations."""
        with MESSAGE_PROCESSING_DURATION.time():
            try:
                event_id = record.get('id')
                content_id = record.get('content_id')
                event_type = record.get('event_type')
            
                if not event_id or not content_id or not event_type:
                    self.logger.warning(f"Invalid record missing required fields: {record}")
                    return True  # Skip this record
                
                # Use Redis pipeline for atomic operations
                pipe = self.redis_client.pipeline()
                
                # Check if event was already processed
                seen_key = f'seen:{event_id}'
                if not pipe.setnx(seen_key, 1):
                    self.logger.debug(f"Event {event_id} already processed, skipping")
                    return True
                
                # Set expiration and calculate score
                pipe.expire(seen_key, 900)  # 15 minutes
                score = self._calculate_score(event_type)
                
                if score > 0:
                    # Update minute-based leaderboard
                    minute_bucket = int(time.time() // 60)
                    minute_key = f'thm:top:{minute_bucket}'
                    
                    pipe.zincrby(minute_key, score, content_id)
                    pipe.expire(minute_key, 660)  # 11 minutes
                    
                    # Update 10-minute rolling leaderboard
                    keys = [f'thm:top:{i}' for i in range(minute_bucket - 9, minute_bucket + 1)]
                    pipe.zunionstore('thm:top:10m', keys)
                    pipe.expire('thm:top:10m', 120)  # 2 minutes
                
                # Execute all commands atomically
                pipe.execute()
                REDIS_OPERATIONS.labels(operation_type='pipeline').inc()
                MESSAGES_PROCESSED.inc()
                
                self.logger.info(f"Processed event {event_id}: {content_id} +{score} ({event_type})")
                return True
                
            except Exception as e:
                self.logger.error(f"Error processing event {record.get('id', 'unknown')}: {e}")
                return False
    
    def run(self) -> None:
        """Main processing loop with error handling and graceful shutdown."""
        try:
            # Establish connections
            self.consumer = self._connect_kafka()
            self.redis_client = self._connect_redis()
            
            self.logger.info("Starting message processing loop")
            
            while not self.shutdown_event.is_set():
                try:
                    # Poll for messages with timeout
                    message_batch = self.consumer.poll(timeout_ms=1000)
                    
                    if not message_batch:
                        continue
                    
                    # Process messages
                    processed_count = 0
                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            if self.shutdown_event.is_set():
                                break
                            
                            success = self._process_event(message.value)
                            if success:
                                processed_count += 1
                    
                    # Commit offsets for processed messages
                    if processed_count > 0:
                        self.consumer.commit()
                        self.logger.debug(f"Committed {processed_count} messages")
                
                except Exception as e:
                    self.logger.error(f"Error in processing loop: {e}")
                    time.sleep(1)  # Brief pause before retry
            
        except Exception as e:
            self.logger.error(f"Fatal error in RedisAggregator: {e}")
            raise
        finally:
            self._cleanup()
    
    def _cleanup(self) -> None:
        """Clean up resources."""
        self.logger.info("Cleaning up resources")
        
        if self.consumer:
            try:
                self.consumer.close()
                self.logger.info("Kafka consumer closed")
            except Exception as e:
                self.logger.warning(f"Error closing Kafka consumer: {e}")
        
        if self.redis_client:
            try:
                self.redis_client.close()
                self.logger.info("Redis client closed")
            except Exception as e:
                self.logger.warning(f"Error closing Redis client: {e}")


if __name__ == "__main__":
    aggregator = RedisAggregator()
    try:
        aggregator.run()
    except KeyboardInterrupt:
        aggregator.logger.info("Received keyboard interrupt")
    except Exception as e:
        aggregator.logger.error(f"Unhandled exception: {e}")
        sys.exit(1)