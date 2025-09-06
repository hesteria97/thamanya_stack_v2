import pytest
import json
import sys
import os
from unittest.mock import patch, Mock

# Add parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from external.app_v2 import ExternalAPI


class TestExternalAPI:
    """Test cases for External API service."""
    
    @pytest.fixture
    def api(self):
        """Create API instance for testing."""
        with patch.dict(os.environ, {'ENVIRONMENT': 'test'}):
            return ExternalAPI()
    
    @pytest.fixture
    def client(self, api):
        """Flask test client."""
        api.app.config['TESTING'] = True
        return api.app.test_client()
    
    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get('/health')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['status'] == 'healthy'
        assert 'timestamp' in data
        assert 'metrics' in data
    
    def test_metrics_endpoint(self, client):
        """Test metrics endpoint."""
        response = client.get('/metrics')
        assert response.status_code == 200
        assert response.content_type.startswith('text/plain')
        
        metrics_text = response.data.decode('utf-8')
        assert 'external_api_total_requests' in metrics_text
        assert 'external_api_valid_events' in metrics_text
    
    def test_root_endpoint(self, client):
        """Test root endpoint."""
        response = client.get('/')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['service'] == 'Thamanya External API'
        assert data['version'] == '2.0'
        assert data['status'] == 'running'
    
    def test_events_endpoint_valid(self, client, sample_event):
        """Test events endpoint with valid data."""
        response = client.post('/events', 
                              data=json.dumps(sample_event),
                              content_type='application/json')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert data['event_id'] == sample_event['id']
    
    def test_events_endpoint_invalid_json(self, client):
        """Test events endpoint with invalid JSON."""
        response = client.post('/events', 
                              data='invalid json',
                              content_type='application/json')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
    
    def test_events_endpoint_validation_error(self, client, sample_invalid_event):
        """Test events endpoint with validation errors."""
        response = client.post('/events', 
                              data=json.dumps(sample_invalid_event),
                              content_type='application/json')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['error'] == 'Event validation failed'
        assert 'details' in data
    
    def test_events_endpoint_empty_payload(self, client):
        """Test events endpoint with empty payload."""
        response = client.post('/events',
                              data='',
                              content_type='application/json')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['error'] == 'Invalid JSON payload'
    
    def test_404_handler(self, client):
        """Test 404 error handler."""
        response = client.get('/nonexistent')
        assert response.status_code == 404
        
        data = json.loads(response.data)
        assert data['error'] == 'Endpoint not found'
    
    def test_rate_limiting_disabled(self, api, client, sample_event):
        """Test when rate limiting is disabled."""
        api.config.enable_rate_limiting = False
        
        # Make multiple requests
        for _ in range(5):
            response = client.post('/events',
                                  data=json.dumps(sample_event),
                                  content_type='application/json')
            assert response.status_code == 200
    
    def test_rate_limiting_enabled(self, api, client, sample_event):
        """Test rate limiting functionality."""
        api.config.enable_rate_limiting = True
        api.config.max_requests_per_minute = 2
        
        # First two requests should succeed
        for _ in range(2):
            response = client.post('/events',
                                  data=json.dumps(sample_event),
                                  content_type='application/json')
            assert response.status_code == 200
        
        # Third request should be rate limited
        response = client.post('/events',
                              data=json.dumps(sample_event),
                              content_type='application/json')
        assert response.status_code == 429
        
        data = json.loads(response.data)
        assert data['error'] == 'Rate limit exceeded'
    
    def test_process_event(self, api, sample_event):
        """Test internal event processing."""
        # This method doesn't return anything, just test it doesn't crash
        api._process_event(sample_event)
        
        # Test with 'finish' event type for special logging
        finish_event = sample_event.copy()
        finish_event['event_type'] = 'finish'
        api._process_event(finish_event)
    
    def test_metrics_tracking(self, api, client, sample_event, sample_invalid_event):
        """Test metrics are properly tracked."""
        initial_metrics = api.metrics.copy()
        
        # Valid event
        client.post('/events',
                   data=json.dumps(sample_event),
                   content_type='application/json')
        
        assert api.metrics['total_requests'] == initial_metrics['total_requests'] + 1
        assert api.metrics['valid_events'] == initial_metrics['valid_events'] + 1
        
        # Invalid event  
        client.post('/events',
                   data=json.dumps(sample_invalid_event),
                   content_type='application/json')
        
        assert api.metrics['total_requests'] == initial_metrics['total_requests'] + 2
        assert api.metrics['invalid_events'] == initial_metrics['invalid_events'] + 1