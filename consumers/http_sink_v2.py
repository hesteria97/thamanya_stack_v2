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
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from shared.config import KafkaConfig, RedisConfig, AppConfig, setup_logging


class HttpSink:
    """Professional HTTP sink service with circuit breaker and retry logic."""
    
    def __init__(self):
        self.logger = setup_logging(__name__)
        self.shutdown_event = Event()
        
        # Load configuration
        self.kafka_config = KafkaConfig.from_env()
        self.redis_config = RedisConfig.from_env()
        self.app_config = AppConfig.from_env()
        self.endpoint = os.getenv('EXTERNAL_ENDPOINT', 'http://external-api:8088/events')
        
        # Initialize connections
        self.consumer: Optional[KafkaConsumer] = None
        self.redis_client: Optional[redis.Redis] = None
        self.http_session: Optional[requests.Session] = None
        
        # Circuit breaker state
        self.circuit_breaker_failures = 0
        self.circuit_breaker_last_failure = 0
        self.circuit_breaker_open_duration = 30  # seconds
        self.circuit_breaker_failure_threshold = 5
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("HttpSink initialized")
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_event.set()
    
    def _setup_http_session(self) -> requests.Session:
        """Configure HTTP session with retry strategy."""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set reasonable timeouts
        session.timeout = (5, 30)  # (connect, read)
        
        return session
    
    def _connect_kafka(self) -> KafkaConsumer:
        """Establish Kafka connection with retry logic."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                consumer = KafkaConsumer(
                    'thm.enriched.events',
                    bootstrap_servers=self.kafka_config.bootstrap_servers,
                    group_id='http-sink',
                    enable_auto_commit=False,
                    auto_offset_reset='earliest',
                    value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                    client_id='http-sink-consumer',
                    consumer_timeout_ms=1000
                )
                self.logger.info("Successfully connected to Kafka")
                return consumer
            except Exception as e:
                self.logger.warning(f"Kafka connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
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
                    socket_connect_timeout=5,
                    socket_timeout=5
                )
                client.ping()
                self.logger.info("Successfully connected to Redis")
                return client
            except Exception as e:
                self.logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
    
    def _is_circuit_breaker_open(self) -> bool:
        """Check if circuit breaker is open."""
        if self.circuit_breaker_failures < self.circuit_breaker_failure_threshold:
            return False
        
        time_since_last_failure = time.time() - self.circuit_breaker_last_failure
        if time_since_last_failure > self.circuit_breaker_open_duration:
            self.logger.info("Circuit breaker half-open, attempting recovery")
            return False
        
        return True
    
    def _record_circuit_breaker_success(self) -> None:
        """Record successful HTTP call."""
        if self.circuit_breaker_failures > 0:
            self.logger.info("Circuit breaker reset after successful call")
            self.circuit_breaker_failures = 0
    
    def _record_circuit_breaker_failure(self) -> None:
        """Record failed HTTP call."""
        self.circuit_breaker_failures += 1
        self.circuit_breaker_last_failure = time.time()
        
        if self.circuit_breaker_failures >= self.circuit_breaker_failure_threshold:
            self.logger.warning(f"Circuit breaker opened after {self.circuit_breaker_failures} failures")
    
    def _send_event(self, record: Dict[str, Any]) -> bool:
        """Send event to external endpoint with circuit breaker."""
        if self._is_circuit_breaker_open():
            self.logger.warning("Circuit breaker is open, skipping HTTP call")
            return False
        
        try:
            response = self.http_session.post(
                self.endpoint,
                json=record,
                timeout=(5, 30)
            )
            response.raise_for_status()
            
            self._record_circuit_breaker_success()
            self.logger.debug(f"Successfully sent event {record.get('id')} to {self.endpoint}")
            return True
            
        except requests.exceptions.RequestException as e:
            self._record_circuit_breaker_failure()
            self.logger.error(f"HTTP request failed for event {record.get('id')}: {e}")
            return False
    
    def _process_event(self, record: Dict[str, Any]) -> bool:
        """Process a single event with deduplication."""
        try:
            event_id = record.get('id')
            if not event_id:
                self.logger.warning(f"Event missing ID, skipping: {record}")
                return True
            
            # Check for deduplication
            dedup_key = f'ext:seen:{event_id}'
            if not self.redis_client.setnx(dedup_key, 1):
                self.logger.debug(f"Event {event_id} already processed, skipping")
                return True
            
            # Set expiration for dedup key
            self.redis_client.expire(dedup_key, 86400)  # 24 hours
            
            # Send event
            success = self._send_event(record)
            
            if success:
                self.logger.info(f"Successfully processed event {event_id}")
            else:
                # Remove dedup key on failure so we can retry later
                self.redis_client.delete(dedup_key)
                self.logger.warning(f"Failed to process event {event_id}, will retry")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error processing event {record.get('id', 'unknown')}: {e}")
            return False
    
    def run(self) -> None:
        """Main processing loop."""
        try:
            # Establish connections
            self.consumer = self._connect_kafka()
            self.redis_client = self._connect_redis()
            self.http_session = self._setup_http_session()
            
            self.logger.info("Starting message processing loop")
            
            while not self.shutdown_event.is_set():
                try:
                    message_batch = self.consumer.poll(timeout_ms=1000)
                    
                    if not message_batch:
                        continue
                    
                    processed_count = 0
                    failed_count = 0
                    
                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            if self.shutdown_event.is_set():
                                break
                            
                            success = self._process_event(message.value)
                            if success:
                                processed_count += 1
                            else:
                                failed_count += 1
                    
                    # Commit only successful messages
                    if processed_count > 0:
                        self.consumer.commit()
                        self.logger.debug(f"Committed {processed_count} messages, {failed_count} failed")
                
                except Exception as e:
                    self.logger.error(f"Error in processing loop: {e}")
                    time.sleep(1)
            
        except Exception as e:
            self.logger.error(f"Fatal error in HttpSink: {e}")
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
        
        if self.http_session:
            try:
                self.http_session.close()
                self.logger.info("HTTP session closed")
            except Exception as e:
                self.logger.warning(f"Error closing HTTP session: {e}")


if __name__ == "__main__":
    sink = HttpSink()
    try:
        sink.run()
    except KeyboardInterrupt:
        sink.logger.info("Received keyboard interrupt")
    except Exception as e:
        sink.logger.error(f"Unhandled exception: {e}")
        sys.exit(1)