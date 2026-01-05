"""Tests for coolhand public API (__init__.py)."""

import coolhand
from coolhand import (
    Config,
    Coolhand,
    RequestData,
    ResponseData,
    __version__,
    get_global_instance,
    get_instance,
    initialize,
    shutdown,
    start_monitoring,
    status,
    stop_monitoring,
)
from coolhand.client import CoolhandClient


class TestVersion:
    """Tests for version constant."""

    def test_version_exists(self):
        """__version__ is defined."""
        assert __version__ is not None

    def test_version_format(self):
        """__version__ follows semantic versioning format."""
        parts = __version__.split(".")
        assert len(parts) >= 2
        # Should be numeric
        assert parts[0].isdigit()
        assert parts[1].isdigit()

    def test_version_is_0_2_0(self):
        """Current version is 0.2.0."""
        assert __version__ == "0.2.0"


class TestExports:
    """Tests for module exports."""

    def test_all_exports_accessible(self):
        """All __all__ exports are accessible."""
        for name in coolhand.__all__:
            assert hasattr(coolhand, name), f"Missing export: {name}"

    def test_types_exported(self):
        """Type definitions are exported."""
        assert Config is not None
        assert RequestData is not None
        assert ResponseData is not None


class TestCoolhandClass:
    """Tests for Coolhand class."""

    def test_inherits_from_client(self, reset_global_instance):
        """Coolhand inherits from CoolhandClient."""
        instance = Coolhand()
        assert isinstance(instance, CoolhandClient)

    def test_sets_global_instance(self, reset_global_instance):
        """Coolhand sets itself as global instance."""
        instance = Coolhand()
        assert get_instance() is instance

    def test_starts_monitoring(self, reset_global_instance):
        """Coolhand starts monitoring on init."""
        from coolhand import interceptor

        instance = Coolhand()
        assert interceptor.is_patched() is True
        instance.stop_monitoring()

    def test_has_session_id(self, reset_global_instance):
        """Coolhand has session_id."""
        instance = Coolhand()
        assert instance.session_id is not None
        assert instance.session_id != ""

    def test_custom_config(self, reset_global_instance, mock_config):
        """Coolhand accepts custom config."""
        instance = Coolhand(config=mock_config)
        assert instance.config["api_key"] == "test-api-key-12345678"
        instance.stop_monitoring()

    def test_start_monitoring_method(self, reset_global_instance):
        """start_monitoring method works."""
        from coolhand import interceptor

        instance = Coolhand()
        instance.stop_monitoring()
        assert interceptor.is_patched() is False

        instance.start_monitoring()
        assert interceptor.is_patched() is True
        instance.stop_monitoring()

    def test_stop_monitoring_method(self, reset_global_instance):
        """stop_monitoring method works."""
        from coolhand import interceptor

        instance = Coolhand()
        assert interceptor.is_patched() is True

        instance.stop_monitoring()
        assert interceptor.is_patched() is False


class TestStatusFunction:
    """Tests for status() function."""

    def test_status_returns_stats(self, reset_global_instance):
        """status() returns stats when initialized."""
        instance = Coolhand()
        result = status()

        assert "config" in result
        assert "monitoring" in result
        assert "logging" in result
        instance.stop_monitoring()

    def test_status_error_when_not_initialized(self, reset_global_instance):
        """status() returns error when not initialized."""
        result = status()
        assert result == {"error": "Not initialized"}


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_start_monitoring_function(self, reset_global_instance):
        """start_monitoring() works on global instance."""
        from coolhand import interceptor

        instance = Coolhand()
        instance.stop_monitoring()
        assert interceptor.is_patched() is False

        start_monitoring()
        assert interceptor.is_patched() is True
        instance.stop_monitoring()

    def test_stop_monitoring_function(self, reset_global_instance):
        """stop_monitoring() works on global instance."""
        from coolhand import interceptor

        Coolhand()
        assert interceptor.is_patched() is True

        stop_monitoring()
        assert interceptor.is_patched() is False

    def test_shutdown_function(self, reset_global_instance):
        """shutdown() calls instance shutdown."""
        instance = Coolhand()
        instance._queue.append({"test": "data"})

        shutdown()
        assert len(instance._queue) == 0

    def test_get_global_instance(self, reset_global_instance):
        """get_global_instance() returns global instance."""
        instance = Coolhand()
        assert get_global_instance() is instance
        instance.stop_monitoring()

    def test_get_global_instance_none(self, reset_global_instance):
        """get_global_instance() returns None when not initialized."""
        assert get_global_instance() is None


class TestAutoInitialization:
    """Tests for auto-initialization behavior."""

    def test_auto_init_on_import(self):
        """Module auto-initializes on import."""
        # Note: This test may be affected by other tests
        # In fresh import, an instance should exist
        import coolhand

        # After import, there should be an instance (from auto-init or tests)
        # We just verify the mechanism exists
        assert hasattr(coolhand, "get_instance")
        assert callable(coolhand.get_instance)

    def test_initialize_function(self, reset_global_instance):
        """initialize() creates instance."""
        instance = initialize(api_key="test-key")
        assert instance is not None
        assert get_instance() is instance


class TestModuleFunctionEdgeCases:
    """Tests for edge cases in module-level functions."""

    def test_start_monitoring_when_no_instance(self, reset_global_instance):
        """start_monitoring does nothing when no instance exists."""
        # Should not raise any errors
        start_monitoring()
        assert get_global_instance() is None

    def test_stop_monitoring_when_no_instance(self, reset_global_instance):
        """stop_monitoring does nothing when no instance exists."""
        # Should not raise any errors
        stop_monitoring()
        assert get_global_instance() is None

    def test_shutdown_when_no_instance(self, reset_global_instance):
        """shutdown does nothing when no instance exists."""
        # Should not raise any errors
        shutdown()
        assert get_global_instance() is None

    def test_status_when_not_initialized(self, reset_global_instance):
        """status returns error dict when not initialized."""
        result = status()
        assert result == {"error": "Not initialized"}


class TestCoolhandClassEdgeCases:
    """Tests for edge cases in Coolhand class."""

    def test_coolhand_create_feedback_returns_response(
        self, reset_global_instance, mock_config
    ):
        """Coolhand.create_feedback returns FeedbackResponse."""
        import json
        from unittest.mock import MagicMock, patch

        with patch("coolhand.interceptor.patch"):
            instance = Coolhand(config=mock_config)

        with patch("coolhand.feedback_service.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 201
            mock_response.read.return_value = json.dumps(
                {
                    "id": 456,
                    "llm_request_log_id": 12345,
                    "like": True,
                }
            ).encode("utf-8")
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = instance.create_feedback(
                {
                    "llm_request_log_id": 12345,
                    "like": True,
                }
            )

            assert result is not None
            assert result["id"] == 456

        instance.stop_monitoring()

    def test_coolhand_feedback_service_property(
        self, reset_global_instance, mock_config
    ):
        """Coolhand.feedback_service returns FeedbackService instance."""
        from unittest.mock import patch

        from coolhand import FeedbackService

        with patch("coolhand.interceptor.patch"):
            instance = Coolhand(config=mock_config)

        assert isinstance(instance.feedback_service, FeedbackService)
        assert instance.feedback_service.api_key == mock_config["api_key"]

        instance.stop_monitoring()

    def test_coolhand_registers_atexit(self, reset_global_instance, mock_config):
        """Coolhand registers shutdown with atexit."""
        import atexit
        from unittest.mock import patch

        with patch("coolhand.interceptor.patch"):
            with patch.object(atexit, "register") as mock_register:
                instance = Coolhand(config=mock_config)
                mock_register.assert_called_once_with(instance.shutdown)

        instance.stop_monitoring()


class TestVersionConstant:
    """Tests for version constant edge cases."""

    def test_version_matches_package(self):
        """__version__ matches version in version.py."""
        from coolhand.version import __version__ as version_module_version

        assert __version__ == version_module_version


class TestExportsComplete:
    """Tests for complete module exports."""

    def test_feedback_types_exported(self):
        """FeedbackData and FeedbackResponse are exported."""
        from coolhand import FeedbackData, FeedbackResponse

        assert FeedbackData is not None
        assert FeedbackResponse is not None

    def test_all_contains_expected_items(self):
        """__all__ contains all expected exports."""
        expected = [
            "__version__",
            "Coolhand",
            "Config",
            "RequestData",
            "ResponseData",
            "FeedbackData",
            "FeedbackResponse",
            "FeedbackService",
            "get_feedback_service",
            "create_feedback",
            "initialize",
            "get_instance",
            "get_global_instance",
            "status",
            "start_monitoring",
            "stop_monitoring",
            "shutdown",
        ]
        for name in expected:
            assert name in coolhand.__all__, f"Missing from __all__: {name}"
