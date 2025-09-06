# Thamanya Stack API Documentation

## Overview

The Thamanya Stack provides a real-time data processing pipeline for engagement analytics with the following APIs and endpoints.

## External API

### Base URL
```
http://localhost:8088 (development)
https://your-domain.com (production)
```

### Authentication
Currently uses API key authentication (configurable in production).

---

## Endpoints

### Health Check

**GET** `/health`

Returns the health status of the API service.

#### Response
```json
{
  "status": "healthy",
  "timestamp": 1672531200.0,
  "metrics": {
    "total_requests": 1250,
    "valid_events": 1180,
    "invalid_events": 70,
    "rate_limited": 0
  }
}
```

---

### Metrics

**GET** `/metrics`

Returns Prometheus-formatted metrics for monitoring.

#### Response (text/plain)
```
external_api_total_requests 1250
external_api_valid_events 1180
external_api_invalid_events 70
external_api_rate_limited 0
```

---

### Submit Event

**POST** `/events`

Submits an engagement event for processing.

#### Request Body
```json
{
  "id": 12345,
  "content_id": "content-uuid-123",
  "user_id": "user-uuid-456", 
  "event_type": "play",
  "event_ts": "2024-01-01T12:00:00Z",
  "device": "web-chrome",
  "content_type": "video",
  "length_seconds": 120,
  "engagement_seconds": 60.5,
  "engagement_pct": 50.42,
  "raw_payload": "{\"custom\": \"data\"}"
}
```

#### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | integer | Yes | Unique event identifier |
| `content_id` | string | Yes | UUID of the content item |
| `user_id` | string | Yes | UUID of the user |
| `event_type` | string | Yes | Type of event: `play`, `pause`, `finish`, `click` |
| `event_ts` | string | Yes | ISO 8601 timestamp |
| `device` | string | No | Device type: `ios`, `android`, `web-chrome`, `web-safari` |
| `content_type` | string | No | Content type: `podcast`, `newsletter`, `video` |
| `length_seconds` | integer | No | Total content length in seconds |
| `engagement_seconds` | float | No | Time user engaged with content |
| `engagement_pct` | float | No | Engagement percentage (0-100) |
| `raw_payload` | string | No | Additional JSON data |

#### Success Response (200)
```json
{
  "status": "success",
  "event_id": 12345,
  "message": "Event processed successfully"
}
```

#### Error Responses

**400 Bad Request**
```json
{
  "error": "Event validation failed",
  "details": [
    {
      "type": "missing",
      "loc": ["content_id"],
      "msg": "field required"
    }
  ]
}
```

**429 Rate Limited**
```json
{
  "error": "Rate limit exceeded",
  "max_requests_per_minute": 1000
}
```

---

## Data Pipeline Endpoints

### ClickHouse Query API

**GET** `http://localhost:8123/`

Direct ClickHouse HTTP interface for querying processed data.

#### Example Query
```bash
curl "http://localhost:8123/?query=SELECT%20count()%20FROM%20thm.enriched_events"
```

### Airflow Web UI

**GET** `http://localhost:8080/`

Airflow dashboard for managing data pipeline workflows.

- Username: `admin`
- Password: Set via `AIRFLOW_PASSWORD` environment variable

### Kafka Connect REST API

**GET** `http://localhost:8083/connectors`

List all Kafka Connect connectors.

**POST** `http://localhost:8083/connectors`

Create a new connector.

---

## Monitoring Endpoints

### Prometheus

**GET** `http://localhost:9090/`

Prometheus web interface for metrics and alerting.

### Grafana

**GET** `http://localhost:3000/`

Grafana dashboards for data visualization.

- Username: `admin`
- Password: Set via `GRAFANA_ADMIN_PASSWORD` environment variable

---

## Event Types

### Event Type Scoring

Events are scored for engagement analytics:

| Event Type | Score | Description |
|------------|-------|-------------|
| `play` | 1.0 | User started playing content |
| `finish` | 1.0 | User finished content |
| `click` | 0.2 | User clicked on content |
| `pause` | 0.0 | User paused content |

---

## Rate Limiting

The API implements rate limiting to prevent abuse:

- **Default**: 1000 requests per minute per IP
- **Configurable**: Set `MAX_REQUESTS_PER_MINUTE` environment variable
- **Response**: HTTP 429 when limit exceeded

---

## Error Handling

All endpoints return structured error responses with:

- HTTP status codes
- Error messages
- Validation details (when applicable)
- Request correlation IDs (in logs)

---

## Content Types

Supported content types:
- `podcast` - Audio content
- `newsletter` - Text/email content  
- `video` - Video content

---

## Device Types

Supported device identifiers:
- `ios` - iOS mobile app
- `android` - Android mobile app
- `web-chrome` - Chrome browser
- `web-safari` - Safari browser

---

## Real-time Features

### Redis Leaderboards

Top content is tracked in Redis with rolling windows:

```bash
# Get top 10 content in last 10 minutes
redis-cli ZREVRANGE thm:top:10m 0 9 WITHSCORES
```

### Kafka Topics

- `pg.public.engagement_events` - Raw events from database CDC
- `thm.enriched.events` - Processed events with content metadata

---

## Development vs Production

### Development
- Simplified authentication
- Verbose logging
- No TLS encryption
- Single-node deployments

### Production  
- Full authentication required
- Structured logging
- TLS encryption enabled
- Multi-node clusters with redundancy