import pytest
import os
import time
from typing import Generator
from unittest.mock import Mock, patch

# Test configuration
os.environ.update({
    'POSTGRES_HOST': 'localhost',
    'POSTGRES_PORT': '5432',
    'POSTGRES_DB': 'test_thm',
    'POSTGRES_USER': 'test',
    'POSTGRES_PASSWORD': 'test',
    'REDIS_HOST': 'localhost',
    'REDIS_PORT': '6379',
    'REDIS_PASSWORD': 'test',
    'KAFKA_BOOTSTRAP': 'localhost:9092',
    'ENVIRONMENT': 'test',
    'LOG_LEVEL': 'DEBUG'
})

# Apply patches globally to prevent any real connections during tests
@pytest.fixture(autouse=True)
def mock_external_connections():
    """Automatically mock external connections for all tests."""
    with patch('kafka.KafkaConsumer') as mock_kafka, \
         patch('redis.Redis') as mock_redis, \
         patch('psycopg2.connect') as mock_postgres:
        
        # Setup Kafka mock
        kafka_consumer = Mock()
        mock_kafka.return_value = kafka_consumer
        kafka_consumer.close = Mock()
        
        # Setup Redis mock
        redis_client = Mock()
        mock_redis.return_value = redis_client
        redis_client.ping.return_value = True
        redis_client.close = Mock()
        
        # Setup PostgreSQL mock
        pg_conn = Mock()
        mock_postgres.return_value = pg_conn
        pg_conn.close = Mock()
        
        yield


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch('redis.Redis') as mock:
        client = Mock()
        mock.return_value = client
        
        # Mock common Redis operations
        client.ping.return_value = True
        client.setnx.return_value = True
        client.expire.return_value = True
        client.zincrby.return_value = 1.0
        client.zunionstore.return_value = True
        client.pipeline.return_value = client
        client.execute.return_value = [True, True, 1.0, True, True]
        
        yield client


@pytest.fixture
def mock_kafka_consumer():
    """Mock Kafka consumer."""
    with patch('kafka.KafkaConsumer') as mock:
        consumer = Mock()
        mock.return_value = consumer
        
        # Mock consumer methods
        consumer.poll.return_value = {}
        consumer.commit.return_value = None
        consumer.close.return_value = None
        
        yield consumer


@pytest.fixture
def mock_postgres():
    """Mock PostgreSQL connection."""
    with patch('psycopg2.connect') as mock_connect:
        conn = Mock()
        cursor = Mock()
        
        mock_connect.return_value = conn
        conn.cursor.return_value = cursor
        conn.autocommit = True
        
        # Mock cursor operations
        cursor.fetchone.return_value = (1,)
        cursor.fetchall.return_value = [('test-id',)]
        cursor.execute.return_value = None
        cursor.executemany.return_value = None
        
        yield conn, cursor


@pytest.fixture
def sample_event():
    """Sample event data for testing."""
    return {
        'id': 12345,
        'content_id': 'content-123',
        'user_id': 'user-456',
        'event_type': 'play',
        'event_ts': '2024-01-01 12:00:00',
        'device': 'web-chrome',
        'content_type': 'video',
        'length_seconds': 120,
        'engagement_seconds': 60.5,
        'engagement_pct': 50.42,
        'raw_payload': '{"test": true}'
    }


@pytest.fixture
def sample_invalid_event():
    """Sample invalid event data for testing."""
    return {
        'id': 'invalid-id',  # Should be int
        'content_id': None,  # Should not be None
        'event_type': 'invalid_type',  # Invalid event type
    }


@pytest.fixture(scope="session")
def test_config():
    """Test configuration."""
    return {
        'postgres': {
            'host': 'localhost',
            'port': 5432,
            'database': 'test_thm',
            'user': 'test',
            'password': 'test'
        },
        'redis': {
            'host': 'localhost',
            'port': 6379,
            'password': 'test'
        },
        'kafka': {
            'bootstrap_servers': 'localhost:9092'
        }
    }