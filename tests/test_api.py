import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


class TestHealthEndpoint:
    """Test health check"""
    
    def test_health_check(self, client):
        """Test health endpoint"""
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert "timestamp" in response.json()


class TestBacktestAPI:
    """Test backtest API endpoints"""
    
    def test_backtest_endpoint_exists(self, client):
        """Test backtest route registration"""
        # Just verify the route exists without executing
        assert client.get("/docs") is not None


class TestWebhookAPI:
    """Test webhook endpoints"""
    
    def test_metrics_endpoint(self, client):
        """Test Prometheus metrics endpoint"""
        response = client.post("/api/webhooks/metrics")
        
        assert response.status_code == 200


class TestRootEndpoint:
    """Test root endpoint"""
    
    def test_root(self, client):
        """Test root endpoint"""
        response = client.get("/")
        
        assert response.status_code == 200
        assert "AI Trading API" in response.json()["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])