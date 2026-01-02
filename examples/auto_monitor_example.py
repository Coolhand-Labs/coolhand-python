#!/usr/bin/env python3
"""
Auto-monitoring example for Coolhand Python SDK.

This example demonstrates:
1. Automatic initialization based on environment
2. Zero-configuration monitoring
3. Real HTTP library integration
4. Different setup modes (notebook, production, development)
"""

import os
import sys
import time

# Add the src directory to the Python path for running examples directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import coolhand - this triggers auto-initialization if conditions are met
import coolhand


def simulate_openai_request():
    """Simulate an OpenAI API request using requests library."""
    print("Making simulated OpenAI API request...")

    # In a real scenario, you would use the requests library like this:
    # import requests
    # response = requests.post(
    #     "https://api.openai.com/v1/chat/completions",
    #     headers={"Authorization": f"Bearer {openai_api_key}"},
    #     json={
    #         "model": "gpt-4",
    #         "messages": [{"role": "user", "content": "Hello!"}]
    #     }
    # )

    # For this example, we'll simulate the request manually
    coolhand.log_request(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-proj-example...",
            "Content-Type": "application/json",
        },
        body={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello!"}],
            "max_tokens": 100,
        },
        response_status=200,
        response_body={
            "choices": [{"message": {"role": "assistant", "content": "Hello! How can I help you?"}}],
            "usage": {"total_tokens": 20}
        },
        duration=1.1
    )

    print("✓ Request completed and automatically logged")


def simulate_anthropic_request():
    """Simulate an Anthropic API request."""
    print("Making simulated Anthropic API request...")

    # Simulate Claude API request
    coolhand.log_request(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": "sk-ant-example...",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        body={
            "model": "claude-3-sonnet-20240229",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Explain quantum computing"}],
        },
        response_status=200,
        response_body={
            "content": [{"text": "Quantum computing is a revolutionary computing paradigm..."}],
            "usage": {"input_tokens": 10, "output_tokens": 25}
        },
        duration=0.9
    )

    print("✓ Request completed and automatically logged")


def demonstrate_auto_initialization():
    """Demonstrate different auto-initialization scenarios."""
    print("Auto-Initialization Example")
    print("=" * 50)

    # Check if Coolhand was auto-initialized
    instance = coolhand.get_global_instance()

    if instance:
        print("✓ Coolhand was automatically initialized!")
        print(f"  Session ID: {instance.get_session_id()}")
        print(f"  Monitoring enabled: {instance.is_monitoring()}")
        print(f"  Config source: Environment variables and auto-detection")

        # Show current stats
        stats = instance.get_stats()
        print(f"  Has API key: {stats['config']['has_api_key']}")
        print(f"  Base URL: {stats['config']['base_url']}")

    else:
        print("⚠ Coolhand was not automatically initialized")
        print("This could be because:")
        print("  - COOLHAND_API_KEY is not set")
        print("  - COOLHAND_ENABLED is set to 'false'")
        print("  - Auto-initialization conditions were not met")
        print()

        # Force initialization for the demo
        print("Force initializing for demonstration...")
        instance = coolhand.force_initialize({
            "api_key": "demo-api-key",
            "enabled": True,
            "log_level": "INFO"
        })
        print("✓ Coolhand force-initialized")

    return instance


def demonstrate_zero_config_usage():
    """Show how to use Coolhand with zero configuration."""
    print("\nZero-Configuration Usage")
    print("-" * 30)

    # With auto-initialization, you can immediately start using feedback
    # without any setup code!

    print("Using Coolhand with zero configuration...")

    # Just use the global functions directly
    coolhand.thumbs_up("This auto-monitoring is awesome!")
    print("✓ Submitted thumbs up feedback")

    coolhand.rate(9, "Love the automatic setup")
    print("✓ Submitted rating")

    # Make some API requests (they'll be automatically captured)
    simulate_openai_request()
    simulate_anthropic_request()

    print("✓ All requests automatically monitored and logged")


def demonstrate_setup_modes():
    """Demonstrate different setup modes for different environments."""
    print("\nDifferent Setup Modes")
    print("-" * 25)

    # Save current instance
    current_instance = coolhand.get_global_instance()

    try:
        # 1. Notebook setup
        print("\n1. Notebook Setup (interactive, more verbose):")
        notebook_instance = coolhand.setup_for_notebook()
        print(f"   ✓ Log level: {notebook_instance.config['log_level']}")
        print(f"   ✓ Enabled: {notebook_instance.config['enabled']}")

        # 2. Production setup
        print("\n2. Production Setup (minimal logging, robust):")
        prod_instance = coolhand.setup_for_production()
        print(f"   ✓ Log level: {prod_instance.config['log_level']}")
        print(f"   ✓ Enabled: {prod_instance.config['enabled']}")

        # 3. Development setup
        print("\n3. Development Setup (debug mode, detailed logging):")
        dev_instance = coolhand.setup_for_development()
        print(f"   ✓ Log level: {dev_instance.config['log_level']}")
        print(f"   ✓ Enabled: {dev_instance.config['enabled']}")

    finally:
        # Restore original instance
        if current_instance:
            coolhand.set_global_instance(current_instance)


def demonstrate_environment_detection():
    """Show how auto-initialization detects different environments."""
    print("\nEnvironment Detection")
    print("-" * 20)

    print("Auto-initialization activates when it detects:")

    # Check for API keys
    ai_api_keys = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HUGGINGFACE_API_KEY",
        "COHERE_API_KEY",
        "GOOGLE_API_KEY",
    ]

    found_keys = [key for key in ai_api_keys if os.getenv(key)]
    if found_keys:
        print(f"✓ AI API keys found: {', '.join(found_keys)}")
    else:
        print("○ No AI API keys detected")

    # Check for AI/ML packages
    ai_packages = [
        "openai",
        "anthropic",
        "transformers",
        "langchain",
        "llama_index",
    ]

    loaded_packages = [pkg for pkg in ai_packages if pkg in sys.modules]
    if loaded_packages:
        print(f"✓ AI/ML packages loaded: {', '.join(loaded_packages)}")
    else:
        print("○ No AI/ML packages detected")

    # Check for interactive environment
    try:
        from IPython import get_ipython
        if get_ipython():
            print("✓ Jupyter notebook environment detected")
        else:
            print("○ Not in Jupyter notebook")
    except ImportError:
        if hasattr(sys, 'ps1'):
            print("✓ Interactive Python session detected")
        else:
            print("○ Not in interactive session")

    print("\nTo control auto-initialization:")
    print("  - Set COOLHAND_AUTO_INIT=false to disable")
    print("  - Set COOLHAND_ENABLED=true to force enable")
    print("  - Set COOLHAND_API_KEY to enable with your API key")


def demonstrate_monitoring_context():
    """Show how to use monitoring contexts for temporary control."""
    print("\nMonitoring Context Manager")
    print("-" * 25)

    instance = coolhand.get_global_instance()

    # Temporarily disable monitoring
    print("Current monitoring status:", instance.is_monitoring())

    print("\nUsing context manager to temporarily disable monitoring:")
    with coolhand.MonitoringContext(enabled=False):
        print("  Inside context - monitoring disabled:", coolhand.is_monitoring_enabled())
        # Any HTTP requests here would not be monitored

    print("Outside context - monitoring restored:", coolhand.is_monitoring_enabled())


def main():
    """Main example function."""
    print("Coolhand Python SDK - Auto-Monitor Example")
    print("=" * 55)

    # 1. Demonstrate auto-initialization
    instance = demonstrate_auto_initialization()

    # 2. Show zero-config usage
    demonstrate_zero_config_usage()

    # 3. Show different setup modes
    demonstrate_setup_modes()

    # 4. Show environment detection
    demonstrate_environment_detection()

    # 5. Show monitoring context
    demonstrate_monitoring_context()

    # 6. Collect some feedback
    print("\nCollecting User Feedback")
    print("-" * 23)

    # Simulate user feedback after AI interactions
    coolhand.thumbs_up("The auto-monitoring just works!")
    coolhand.rate(10, "Perfect zero-config experience")
    coolhand.submit_feedback(
        rating=9,
        comment="Love how it automatically detects my AI usage",
        metadata={
            "feature": "auto_initialization",
            "environment": "demo"
        }
    )

    print("✓ Collected feedback on auto-monitoring experience")

    # 7. Final status
    print("\nFinal Status")
    print("-" * 12)

    final_stats = instance.get_stats()
    print(f"Session ID: {instance.get_session_id()}")
    print(f"Total interactions logged: {final_stats['logging'].get('interaction_count', 0)}")
    print(f"Monitoring active: {final_stats['monitoring']['enabled']}")

    # 8. Cleanup
    print("\nCleaning up...")
    success = instance.flush()
    print(f"Data flushed: {success}")

    print("\n" + "=" * 55)
    print("Auto-monitor example completed!")
    print("\nKey takeaways:")
    print("1. Just 'import coolhand' can be enough to get started")
    print("2. Auto-initialization detects AI/ML environments")
    print("3. Zero configuration needed in many cases")
    print("4. Use setup_for_*() functions for specific environments")
    print("5. Environment variables control auto-initialization")


if __name__ == "__main__":
    # Set up demonstration environment
    print("Setting up demonstration environment...\n")

    # Simulate having an AI API key (triggers auto-init)
    if not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = "sk-demo-key-for-auto-init-example"

    # Ensure Coolhand is enabled for demo
    os.environ["COOLHAND_ENABLED"] = "true"

    # Set demo API key if not provided
    if not os.getenv("COOLHAND_API_KEY"):
        os.environ["COOLHAND_API_KEY"] = "demo-api-key-for-example"

    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExample interrupted by user")
        # Clean up
        instance = coolhand.get_global_instance()
        if instance:
            instance.shutdown()
    except Exception as e:
        print(f"\nExample failed with error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure clean shutdown
        coolhand.shutdown()