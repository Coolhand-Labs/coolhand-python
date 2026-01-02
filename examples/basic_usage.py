#!/usr/bin/env python3
"""
Basic usage example for Coolhand Python SDK.

This example demonstrates:
1. Manual initialization and configuration
2. Making monitored API requests
3. Submitting feedback
4. Manual request logging
"""

import os
import sys
import time

# Add the src directory to the Python path for running examples directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import coolhand


def main():
    """Main example function."""
    print("Coolhand Python SDK - Basic Usage Example")
    print("=" * 50)

    # 1. Initialize Coolhand with configuration
    print("\n1. Initializing Coolhand...")

    # Option A: Initialize with explicit configuration
    config = {
        "api_key": "your-coolhand-api-key",  # Replace with your actual API key
        "enabled": True,
        "log_level": "INFO",
        "base_url": "https://api.coolhand.dev",
    }

    # Initialize Coolhand
    ch = coolhand.Coolhand(config)
    print(f"✓ Coolhand initialized (Session: {ch.get_session_id()})")

    # 2. Check status
    print("\n2. Checking Coolhand status...")
    stats = ch.get_stats()
    print(f"✓ Monitoring enabled: {stats['monitoring']['enabled']}")
    print(f"✓ Has API key: {stats['config']['has_api_key']}")
    print(f"✓ Base URL: {stats['config']['base_url']}")

    # 3. Manual request logging (when you want to log requests explicitly)
    print("\n3. Manual request logging...")

    # Simulate an API request to OpenAI
    ch.log_request(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-example-key",
            "Content-Type": "application/json",
        },
        body={
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "What is the capital of France?"}
            ],
            "max_tokens": 100,
        },
        response_status=200,
        response_headers={
            "Content-Type": "application/json",
        },
        response_body={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The capital of France is Paris."
                    }
                }
            ],
            "usage": {"total_tokens": 25}
        },
        duration=1.2,
    )
    print("✓ Logged OpenAI API request manually")

    # 4. Submit feedback
    print("\n4. Submitting feedback...")

    # Option A: Thumbs up/down
    ch.thumbs_up("Great response, very accurate!")
    print("✓ Submitted thumbs up feedback")

    # Option B: Rating with comment
    ch.submit_feedback(
        rating=9,
        comment="Excellent response quality and speed",
        metadata={"interaction_type": "completion", "model": "gpt-4"}
    )
    print("✓ Submitted detailed feedback")

    # 5. Working with the global instance
    print("\n5. Using global convenience functions...")

    # You can also use global functions for common operations
    coolhand.thumbs_down("This response was not helpful")
    print("✓ Used global thumbs_down function")

    coolhand.rate(7, "Good but could be better")
    print("✓ Used global rate function")

    # 6. Automatic monitoring example
    print("\n6. Automatic monitoring...")
    print("Note: When you import coolhand and make HTTP requests with libraries")
    print("like requests or httpx, they will be automatically monitored!")

    # Example: If you had requests installed and made a call like this:
    # import requests
    # response = requests.post("https://api.openai.com/v1/chat/completions", ...)
    # It would be automatically captured and logged!

    print("\nSimulating an automatic request capture...")

    # Simulate what happens when the global monitor captures a request
    class MockResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b'{"result": "success"}'

    # This simulates the internal handler that processes captured requests
    request_data = ch.logging_service.create_request_data(
        method="GET",
        url="https://api.anthropic.com/v1/messages",
        headers={"x-api-key": "sk-ant-example"},
        body={"model": "claude-3-sonnet-20240229", "messages": []}
    )

    response_data = ch.logging_service.create_response_data(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body={"content": [{"text": "Hello! How can I help you today?"}]},
        duration=0.8
    )

    ch.logging_service.log_interaction(request_data, response_data)
    print("✓ Simulated automatic request capture for Anthropic API")

    # 7. Configuration updates
    print("\n7. Runtime configuration updates...")

    # Update session ID
    ch.set_session_id("custom-session-12345")
    print(f"✓ Updated session ID to: {ch.get_session_id()}")

    # Update other config
    ch.update_config(log_level="DEBUG")
    print("✓ Updated log level to DEBUG")

    # 8. Flush pending data
    print("\n8. Flushing data...")
    success = ch.flush()
    if success:
        print("✓ Successfully flushed all pending logs and feedback")
    else:
        print("⚠ Some data may not have been flushed (check network/API key)")

    # 9. Context manager usage
    print("\n9. Context manager example...")

    # Using Coolhand as a context manager ensures proper cleanup
    with coolhand.Coolhand({"api_key": "temp-key", "enabled": True}) as temp_ch:
        temp_ch.thumbs_up("Using context manager!")
        print("✓ Used Coolhand in context manager")
    # Automatically shuts down when exiting the context

    # 10. Debug information (if debug mode is enabled)
    print("\n10. Debug information...")
    debug_data = ch.get_debug_data()
    if debug_data:
        print(f"✓ Debug mode active - captured {len(debug_data['interaction_history'])} interactions")
    else:
        print("✓ Debug mode not active (set log_level='DEBUG' to enable)")

    print("\n" + "=" * 50)
    print("Basic usage example completed!")
    print("\nNext steps:")
    print("1. Set your real COOLHAND_API_KEY environment variable")
    print("2. Install HTTP libraries like 'requests' or 'httpx'")
    print("3. Import coolhand in your AI application")
    print("4. Make API calls - they'll be automatically monitored!")
    print("5. Use coolhand.thumbs_up(), coolhand.rate(), etc. for feedback")

    # Cleanup
    ch.shutdown()


if __name__ == "__main__":
    # Set up environment for the example
    if not os.getenv("COOLHAND_API_KEY"):
        print("Note: Set COOLHAND_API_KEY environment variable to use real API")
        print("This example will run with a mock API key.\n")
        os.environ["COOLHAND_API_KEY"] = "example-api-key-for-demo"

    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExample interrupted by user")
    except Exception as e:
        print(f"\nExample failed with error: {e}")
        import traceback
        traceback.print_exc()