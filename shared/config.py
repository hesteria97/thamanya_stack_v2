import os
import logging
from dataclasses import dataclass
from typing import Optional

@dataclass
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    
    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        return cls(
            host=os.getenv('PGHOST', 'postgres'),
            port=int(os.getenv('PGPORT', '5432')),
            database=os.getenv('PGDATABASE', 'thm'),
            user=os.getenv('PGUSER', 'app'),
            password=os.getenv('PGPASSWORD', 'app')
        )

@dataclass
class KafkaConfig:
    bootstrap_servers: str
    security_protocol: str = "PLAINTEXT"
    
    @classmethod
    def from_env(cls) -> 'KafkaConfig':
        return cls(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP', 'redpanda:9092'),
            security_protocol=os.getenv('KAFKA_SECURITY_PROTOCOL', 'PLAINTEXT')
        )

@dataclass
class RedisConfig:
    host: str
    port: int
    password: Optional[str] = None
    
    @classmethod
    def from_env(cls) -> 'RedisConfig':
        return cls(
            host=os.getenv('REDIS_HOST', 'redis'),
            port=int(os.getenv('REDIS_PORT', '6379')),
            password=os.getenv('REDIS_PASSWORD')
        )

@dataclass
class AppConfig:
    environment: str
    log_level: str
    enable_metrics: bool
    
    @classmethod
    def from_env(cls) -> 'AppConfig':
        return cls(
            environment=os.getenv('ENVIRONMENT', 'development'),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            enable_metrics=os.getenv('ENABLE_METRICS', 'true').lower() == 'true'
        )

def setup_logging(name: str, level: str = 'INFO') -> logging.Logger:
    """Configure structured logging for the application."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger