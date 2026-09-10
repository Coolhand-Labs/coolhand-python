#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/basic_usage.py
"""
Basic usage example for Coolhand Python SDK.

This example demonstrates:
1. Manual initialization and configuration
2. Checking monitoring status
3. Manual request logging
4. Submitting feedback
5. Flushing/shutting down cleanly
"""

import os
import sys

# Add the src directory to the Python path for running examples directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import coolhand  # noqa: E402


def main():
    """Main example function."""
    print("Coolhand Python SDK - Basic Usage Example")
    print("=" * 50)

    # 1. Initialize Coolhand with explicit configuration
    print("\n1. Initializing Coolhand...")

    config = {
        "api_key": "your-coolhand-api-key",  # Replace with your actual API key
        "silent": False,  # Enable verbose logging output
        "base_url": "https://coolhandlabs.com",
    }

    ch = coolhand.Coolhand(config)
    print(f"✓ Coolhand initialized (Session: {ch.session_id})")

    # 2. Check status
    print("\n2. Checking Coolhand status...")
    stats = ch.get_stats()
    print(f"✓ Monitoring enabled: {stats['monitoring']['enabled']}")
    print(f"✓ Has API key: {stats['config']['has_api_key']}")
    print(f"✓ Base URL: {ch.config['base_url']}")

    # 3. Manual request logging (when you want to log requests explicitly,
    # rather than relying on the automatic httpx/requests interception)
    print("\n3. Manual request logging...")

    ch.log_interaction(
        request={
            "method": "POST",
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer sk-example-key",
                "Content-Type": "application/json",
            },
            "body": {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "max_tokens": 100,
            },
        },
        response={
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The capital of France is Paris.",
                        }
                    }
                ],
                "usage": {"total_tokens": 25},
            },
            "duration": 1.2,
        },
    )
    print("✓ Logged OpenAI API request manually")

    # 4. Submit feedback
    print("\n4. Submitting feedback...")

    # Option A: Sentiment-only feedback (not linked to a specific log)
    ch.create_feedback(
        {"sentiment": "like", "explanation": "Great response, very accurate!"}
    )
    print("✓ Submitted positive feedback")

    # Option B: Negative feedback with a human correction
    ch.create_feedback(
        {
            "sentiment": "dislike",
            "original_output": "The capital of France is London.",
            "revised_output": "The capital of France is Paris.",
            "explanation": "Factually wrong.",
        }
    )
    print("✓ Submitted feedback with a correction")

    # 5. Working with the global instance
    print("\n5. Using global convenience functions...")

    # You can also use the module-level function for common operations
    coolhand.create_feedback(
        {"sentiment": "dislike", "explanation": "This response was not helpful"}
    )
    print("✓ Used global create_feedback function")

    # 6. Automatic monitoring example
    print("\n6. Automatic monitoring...")
    print("Note: When you import coolhand and make HTTP requests with libraries")
    print("like requests or httpx, they will be automatically monitored!")

    # Example: If you had requests installed and made a call like this:
    # import requests
    # response = requests.post("https://api.openai.com/v1/chat/completions", ...)
    # It would be automatically captured and logged!

    print("\nSimulating an automatic request capture...")

    # This simulates what the internal httpx/requests interceptor does when it
    # captures a real request — it's the same log_interaction() call used above.
    ch.log_interaction(
        request={
            "method": "GET",
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {"x-api-key": "sk-ant-example"},
            "body": {"model": "claude-3-sonnet-20240229", "messages": []},
        },
        response={
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": {"content": [{"text": "Hello! How can I help you today?"}]},
            "duration": 0.8,
        },
    )
    print("✓ Simulated automatic request capture for Anthropic API")

    # 7. Flush pending data. shutdown() (not flush()) is the delivery barrier:
    # it waits for the background worker to actually deliver everything queued,
    # not just for it to be handed off.
    print("\n7. Flushing data...")
    ch.shutdown()
    print("✓ Flushed and waited for delivery of all pending logs and feedback")

    # 8. A short-lived, separately-configured instance
    print("\n8. Separate instance example...")

    # Coolhand doesn't implement the context manager protocol — call
    # shutdown() explicitly when a short-lived instance is done.
    temp_ch = coolhand.Coolhand({"api_key": "temp-key", "silent": True})
    temp_ch.create_feedback(
        {"sentiment": "like", "explanation": "Using a separate instance!"}
    )
    temp_ch.shutdown()
    print("✓ Used and shut down a separate Coolhand instance")

    # 9. Stats / debug information
    print("\n9. Session stats...")
    final_stats = ch.get_stats()
    print(
        f"✓ Interactions logged this session: {final_stats['logging']['interaction_count']}"
    )
    print(f"✓ Delivery failures: {final_stats['logging']['delivery_failure_count']}")

    print("\n" + "=" * 50)
    print("Basic usage example completed!")
    print("\nNext steps:")
    print("1. Set your real COOLHAND_API_KEY environment variable")
    print("2. Install HTTP libraries like 'requests' or 'httpx'")
    print("3. Import coolhand in your AI application")
    print("4. Make API calls - they'll be automatically monitored!")
    print("5. Use coolhand.create_feedback(...) to collect user feedback")

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
