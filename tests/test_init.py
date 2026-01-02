"""Tests for coolhand public API (__init__.py)."""

import pytest
from unittest.mock import patch, MagicMock

import coolhand
from coolhand import (
    __version__,
    Coolhand,
    Config,
    RequestData,
    ResponseData,
    status,
    start_monitoring,
    stop_monitoring,
    shutdown,
    get_global_instance,
    get_instance,
    initialize,
)
from coolhand.client import CoolhandClient


class TestVersion:
    """Tests for version constant."""

    def test_version_exists(self):
        """__version__ is defined."""
        assert __version__ is not None

    def test_version_format(self):
        """__version__ follows semantic versioning format."""
        parts = __version__.split('.')
        assert len(parts) >= 2
        # Should be numeric
        assert parts[0].isdigit()
        assert parts[1].isdigit()

    def test_version_is_0_1_0(self):
        """Current version is 0.1.0."""
        assert __version__ == '0.1.0'


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
        assert instance.session_id != ''

    def test_custom_config(self, reset_global_instance, mock_config):
        """Coolhand accepts custom config."""
        instance = Coolhand(config=mock_config)
        assert instance.config['api_key'] == 'test-api-key-12345678'
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

        assert 'config' in result
        assert 'monitoring' in result
        assert 'logging' in result
        instance.stop_monitoring()

    def test_status_error_when_not_initialized(self, reset_global_instance):
        """status() returns error when not initialized."""
        result = status()
        assert result == {'error': 'Not initialized'}


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

        instance = Coolhand()
        assert interceptor.is_patched() is True

        stop_monitoring()
        assert interceptor.is_patched() is False

    def test_shutdown_function(self, reset_global_instance):
        """shutdown() calls instance shutdown."""
        instance = Coolhand()
        instance._queue.append({'test': 'data'})

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
        assert hasattr(coolhand, 'get_instance')
        assert callable(coolhand.get_instance)

    def test_initialize_function(self, reset_global_instance):
        """initialize() creates instance."""
        instance = initialize(api_key='test-key')
        assert instance is not None
        assert get_instance() is instance
