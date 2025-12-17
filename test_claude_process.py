#!/usr/bin/env python3
"""Simple test script for ClaudeProcess."""

import asyncio
import sys
from pathlib import Path

from claude_process import ClaudeProcess, SystemInit, AssistantMessage, extract_tool_uses, extract_text


async def test_basic_interaction():
    """Test basic message sending and receiving."""
    print("Testing ClaudeProcess basic interaction...")

    # Use current directory
    cwd = str(Path.cwd())

    # Create process
    process = ClaudeProcess(cwd=cwd)

    # Start subprocess
    if not await process.start():
        print("Failed to start Claude process")
        return False

    # Send a simple message
    await process.send_message("What is 2+2? Just give the answer, nothing else.")

    # Read events
    async for event in process.events():
        if isinstance(event, SystemInit):
            print(f"✓ Session initialized: {event.session_id}")
            print(f"  Model: {event.model}")
            print(f"  Tools: {len(event.tools)} available")

        elif isinstance(event, AssistantMessage):
            # Extract text content using helper
            text = extract_text(event)
            if text:
                print(f"✓ Assistant response: {text[:100]}")

            # Check for tool uses
            tools = extract_tool_uses(event)
            if tools:
                print(f"  Tool uses: {[t.name for t in tools]}")

        # Stop after first assistant message
        if isinstance(event, AssistantMessage):
            break

    # Terminate
    await process.terminate()
    print("✓ Process terminated")

    return True


async def test_resume():
    """Test session resumption."""
    print("\nTesting ClaudeProcess session resumption...")

    cwd = str(Path.cwd())

    # First session
    process1 = ClaudeProcess(cwd=cwd)
    await process1.start()

    # Send message
    await process1.send_message("Remember this number: 42")

    session_id = None

    # Get session ID
    async for event in process1.events():
        if isinstance(event, SystemInit):
            session_id = event.session_id
            print(f"✓ First session ID: {session_id}")
        if isinstance(event, AssistantMessage):
            break

    await process1.terminate()

    if not session_id:
        print("Failed to get session ID")
        return False

    # Resume session
    process2 = ClaudeProcess(cwd=cwd, resume_session_id=session_id)
    await process2.start()

    await process2.send_message("What number did I ask you to remember?")

    async for event in process2.events():
        if isinstance(event, SystemInit):
            print(f"✓ Resumed session ID: {event.session_id}")
            if event.session_id != session_id:
                print(f"  WARNING: Session ID changed!")
        if isinstance(event, AssistantMessage):
            text = extract_text(event)
            if text:
                print(f"✓ Resumed response: {text[:100]}")
                if "42" in text:
                    print("✓ Session context preserved!")
            break

    await process2.terminate()
    return True


async def main():
    """Run all tests."""
    if "--resume" in sys.argv:
        success = await test_resume()
    else:
        success = await test_basic_interaction()

    if success:
        print("\n✓ All tests passed")
    else:
        print("\n✗ Tests failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
