import pytest
from unittest.mock import MagicMock, patch
from src.rate_limiter import TokenBucket, ProviderLimit, PROVIDER_LIMITS
from src.cost_tracker import CostTracker
from src.router import Router
from src.config import LLM_CONFIGS, LLMConfig, Provider

@pytest.fixture
def mock_llm_responses():
    """Mock LLM responses to avoid real API calls."""
    with patch('src.llm_client.LLMClient') as MockClient:
        mock_client = MockClient.return_value
        mock_client.send_request.return_value = {"response": "mocked response"}
        yield mock_client

@pytest.fixture
def sample_task_data():
    """Provide sample task data for testing."""
    return {
        "task_id": "sample_task_1",
        "content": "Sample task content",
        "complexity": "medium"
    }

@pytest.fixture
def rate_limiter():
    """Provide a rate limiter with test-friendly limits."""
    test_limits = {
        Provider.ANTHROPIC: ProviderLimit(requests_per_minute=10, burst_size=2),
        Provider.OPENAI: ProviderLimit(requests_per_minute=10, burst_size=2),
    }
    return {provider: TokenBucket(limit) for provider, limit in test_limits.items()}

@pytest.fixture
def cost_tracker():
    """Provide a cost tracker and reset it between tests."""
    tracker = CostTracker()
    yield tracker
    tracker._records.clear()

@pytest.fixture
def router():
    """Provide a router instance for testing."""
    return Router()