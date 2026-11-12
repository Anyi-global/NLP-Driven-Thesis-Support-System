from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.nlp.gemini import LLMUnavailable, generate_text


def test_generate_text_closes_client_after_success():
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(text='{"ok":true}')

    with patch("app.nlp.gemini._client", return_value=client):
        result = generate_text("prompt", {"GEMINI_MODEL": "test-model"})

    assert result == '{"ok":true}'
    client.close.assert_called_once_with()


def test_generate_text_closes_client_after_model_error():
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError("404 NOT_FOUND")

    with patch("app.nlp.gemini._client", return_value=client):
        with pytest.raises(LLMUnavailable, match="test-model"):
            generate_text("prompt", {"GEMINI_MODEL": "test-model"})

    client.close.assert_called_once_with()
