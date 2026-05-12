"""Tests for base_url configuration."""

import json
from unittest.mock import MagicMock, patch

import pytest

from coolhand.client import (
    BASE_URL,
    CoolhandClient,
    _get_default_config,
    validate_base_url,
)
from coolhand.feedback_service import FeedbackService


class TestValidateBaseUrl:
    def test_accepts_https_url(self):
        assert validate_base_url("https://example.com") == "https://example.com"

    def test_strips_trailing_slash_https(self):
        assert validate_base_url("https://example.com/") == "https://example.com"

    def test_strips_multiple_trailing_slashes(self):
        assert validate_base_url("https://example.com///") == "https://example.com"

    def test_accepts_localhost_http(self):
        assert validate_base_url("http://localhost:3000") == "http://localhost:3000"

    def test_accepts_127_0_0_1_http(self):
        assert validate_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"

    def test_strips_trailing_slash_localhost(self):
        assert validate_base_url("http://localhost:3000/") == "http://localhost:3000"

    def test_rejects_plain_http_non_localhost(self):
        with pytest.raises(ValueError, match="must start with 'https://'"):
            validate_base_url("http://example.com")

    def test_rejects_http_remote_ip(self):
        with pytest.raises(ValueError):
            validate_base_url("http://192.168.1.1:8080")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError):
            validate_base_url("ftp://example.com")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValueError):
            validate_base_url("example.com")

    def test_rejects_localhost_lookalike_hostname(self):
        # http://localhost.attacker.com passes startswith("http://localhost")
        # but must be rejected because the hostname is not exactly "localhost"
        with pytest.raises(ValueError):
            validate_base_url("http://localhost.attacker.com")

    def test_rejects_127_0_0_1_lookalike_hostname(self):
        # Same attack vector for 127.0.0.1
        with pytest.raises(ValueError):
            validate_base_url("http://127.0.0.1.evil.com")


class TestGetDefaultConfigBaseUrl:
    def test_base_url_absent_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        config = _get_default_config()
        assert "base_url" not in config

    def test_base_url_from_env_var(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://self-hosted.example.com")
        config = _get_default_config()
        assert config["base_url"] == "https://self-hosted.example.com"

    def test_base_url_env_var_normalizes_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://self-hosted.example.com/")
        config = _get_default_config()
        assert config["base_url"] == "https://self-hosted.example.com"

    def test_invalid_base_url_env_var_raises(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "http://not-localhost.com")
        with pytest.raises(ValueError, match="must start with 'https://'"):
            _get_default_config()


class TestCoolhandClientBaseUrl:
    def test_default_base_url_not_in_config(self, reset_global_instance, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient()
        assert client.config.get("base_url") is None

    def test_base_url_from_constructor_kwarg(self, reset_global_instance, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(base_url="https://custom.example.com")
        assert client.config["base_url"] == "https://custom.example.com"

    def test_base_url_from_constructor_config_dict(
        self, reset_global_instance, monkeypatch
    ):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(config={"base_url": "https://custom.example.com"})
        assert client.config["base_url"] == "https://custom.example.com"

    def test_base_url_trailing_slash_normalized_in_constructor(
        self, reset_global_instance, monkeypatch
    ):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(base_url="https://custom.example.com/")
        assert client.config["base_url"] == "https://custom.example.com"

    def test_invalid_base_url_raises_in_constructor(
        self, reset_global_instance, monkeypatch
    ):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        with pytest.raises(ValueError):
            CoolhandClient(base_url="http://remote.example.com")

    def test_base_url_from_env_var(self, reset_global_instance, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        client = CoolhandClient()
        assert client.config["base_url"] == "https://env.example.com"

    def test_constructor_base_url_overrides_env_var(
        self, reset_global_instance, monkeypatch
    ):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        client = CoolhandClient(base_url="https://constructor.example.com")
        assert client.config["base_url"] == "https://constructor.example.com"

    def test_localhost_base_url_allowed(self, reset_global_instance, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(base_url="http://localhost:3000")
        assert client.config["base_url"] == "http://localhost:3000"

    def test_flush_uses_custom_base_url(self, reset_global_instance, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(
            api_key="test-key-12345678",
            base_url="https://custom.example.com",
            auto_submit=False,
        )
        client._queue.append({"id": "x", "method": "post", "url": "test"})

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("coolhand.client.urlopen", return_value=mock_resp) as mock_urlopen:
            client.flush()

        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.full_url.startswith("https://custom.example.com")

    def test_flush_falls_back_to_default_base_url(
        self, reset_global_instance, monkeypatch
    ):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        client = CoolhandClient(api_key="test-key-12345678", auto_submit=False)
        client._queue.append({"id": "x", "method": "post", "url": "test"})

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("coolhand.client.urlopen", return_value=mock_resp) as mock_urlopen:
            client.flush()

        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.full_url.startswith(BASE_URL)


class TestFeedbackServiceBaseUrl:
    def test_default_base_url_not_in_config(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService()
        assert service.config.get("base_url") is None

    def test_base_url_from_constructor_kwarg(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService(base_url="https://custom.example.com")
        assert service.config["base_url"] == "https://custom.example.com"

    def test_base_url_from_config_dict(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService(config={"base_url": "https://custom.example.com"})
        assert service.config["base_url"] == "https://custom.example.com"

    def test_base_url_trailing_slash_normalized(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService(base_url="https://custom.example.com/")
        assert service.config["base_url"] == "https://custom.example.com"

    def test_invalid_base_url_raises(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        with pytest.raises(ValueError):
            FeedbackService(base_url="http://remote.example.com")

    def test_base_url_from_env_var(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        service = FeedbackService()
        assert service.config["base_url"] == "https://env.example.com"

    def test_constructor_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        service = FeedbackService(base_url="https://constructor.example.com")
        assert service.config["base_url"] == "https://constructor.example.com"

    def test_create_feedback_uses_custom_base_url(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService(
            api_key="test-key-12345678",
            base_url="https://feedback.example.com",
        )

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = json.dumps({"id": 1, "like": True}).encode(
            "utf-8"
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "coolhand.feedback_service.urlopen", return_value=mock_resp
        ) as mock_urlopen:
            service.create_feedback({"llm_request_log_id": 1, "like": True})

        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.full_url.startswith("https://feedback.example.com")

    def test_create_feedback_falls_back_to_default_base_url(self, monkeypatch):
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        service = FeedbackService(api_key="test-key-12345678")

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = json.dumps({"id": 1, "like": True}).encode(
            "utf-8"
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "coolhand.feedback_service.urlopen", return_value=mock_resp
        ) as mock_urlopen:
            service.create_feedback({"llm_request_log_id": 1, "like": True})

        request_obj = mock_urlopen.call_args[0][0]
        assert "coolhandlabs.com" in request_obj.full_url
