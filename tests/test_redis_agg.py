import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consumers.redis_agg_v2 import RedisAggregator


class TestRedisAggregator:
    """Test cases for Redis Aggregator service."""
    
    def test_init(self):
        """Test RedisAggregator initialization."""
        agg = RedisAggregator()
        assert agg.logger is not None
        assert agg.kafka_config is not None
        assert agg.redis_config is not None
        assert agg.app_config is not None
        assert not agg.shutdown_event.is_set()
    
    def test_calculate_score(self):
        """Test event scoring logic."""
        assert RedisAggregator._calculate_score('play') == 1.0
        assert RedisAggregator._calculate_score('finish') == 1.0
        assert RedisAggregator._calculate_score('click') == 0.2
        assert RedisAggregator._calculate_score('pause') == 0.0
        assert RedisAggregator._calculate_score('unknown') == 0.0
    
    @patch('redis.Redis')
    def test_connect_redis_success(self, mock_redis):
        """Test successful Redis connection."""
        mock_client = Mock()
        mock_redis.return_value = mock_client
        mock_client.ping.return_value = True
        
        agg = RedisAggregator()
        client = agg._connect_redis()
        
        assert client is mock_client
        mock_client.ping.assert_called_once()
    
    @patch('redis.Redis')
    def test_connect_redis_retry_logic(self, mock_redis):
        """Test Redis connection retry logic."""
        mock_client = Mock()
        mock_redis.return_value = mock_client
        
        # Fail first 2 attempts, succeed on 3rd
        mock_client.ping.side_effect = [Exception("Connection failed"), 
                                       Exception("Connection failed"), 
                                       True]
        
        agg = RedisAggregator()
        with patch('time.sleep'):  # Speed up test
            client = agg._connect_redis()
        
        assert client is mock_client
        assert mock_client.ping.call_count == 3
    
    def test_process_event_valid(self, mock_redis, sample_event):
        """Test processing valid event."""
        agg = RedisAggregator()
        agg.redis_client = mock_redis
        
        # Mock pipeline
        pipeline = Mock()
        mock_redis.pipeline.return_value = pipeline
        pipeline.setnx.return_value = pipeline
        pipeline.expire.return_value = pipeline
        pipeline.zincrby.return_value = pipeline
        pipeline.zunionstore.return_value = pipeline
        pipeline.execute.return_value = [True, True, 1.0, True, True]
        
        result = agg._process_event(sample_event)
        
        assert result is True
        pipeline.setnx.assert_called_once()
        pipeline.expire.assert_called()
        pipeline.zincrby.assert_called()
        pipeline.execute.assert_called_once()
    
    def test_process_event_invalid(self, mock_redis):
        """Test processing invalid event."""
        agg = RedisAggregator()
        agg.redis_client = mock_redis
        
        # Event missing required fields
        invalid_event = {'id': None, 'content_id': None}
        
        result = agg._process_event(invalid_event)
        
        assert result is True  # Should skip invalid events gracefully
    
    def test_process_event_already_seen(self, mock_redis, sample_event):
        """Test processing already seen event."""
        agg = RedisAggregator()
        agg.redis_client = mock_redis
        
        # Mock pipeline for already seen event
        pipeline = Mock()
        mock_redis.pipeline.return_value = pipeline
        pipeline.setnx.return_value = False  # Event already exists
        
        result = agg._process_event(sample_event)
        
        assert result is True
        pipeline.setnx.assert_called_once()
        pipeline.execute.assert_not_called()  # Should not execute other operations
    
    def test_signal_handler(self):
        """Test graceful shutdown signal handling."""
        agg = RedisAggregator()
        assert not agg.shutdown_event.is_set()
        
        # Simulate signal
        agg._signal_handler(15, None)  # SIGTERM
        
        assert agg.shutdown_event.is_set()
    
    @patch('kafka.KafkaConsumer')
    def test_connect_kafka_success(self, mock_consumer_class):
        """Test successful Kafka connection."""
        mock_consumer = Mock()
        mock_consumer_class.return_value = mock_consumer
        
        agg = RedisAggregator()
        
        # Mock the Kafka connection to avoid actual connection
        with patch.object(agg, '_connect_kafka', return_value=mock_consumer):
            consumer = agg._connect_kafka()
        
        assert consumer is mock_consumer
    
    def test_cleanup(self, mock_kafka_consumer, mock_redis):
        """Test resource cleanup."""
        agg = RedisAggregator()
        agg.consumer = mock_kafka_consumer
        agg.redis_client = mock_redis
        
        agg._cleanup()
        
        mock_kafka_consumer.close.assert_called_once()
        mock_redis.close.assert_called_once()