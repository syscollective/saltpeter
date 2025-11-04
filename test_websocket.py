#!/usr/bin/env python3
"""
Test script for WebSocket functionality
Tests the wrapper and WebSocket server integration
"""

import asyncio
import websockets
import json
import sys
from datetime import datetime, timezone

async def test_websocket_connection(url='ws://localhost:8889'):
    """Test basic WebSocket connection and message flow"""
    
    print(f"Testing WebSocket connection to {url}")
    
    try:
        async with websockets.connect(url) as websocket:
            print("✓ Connected to WebSocket server")
            
            # Test 1: Send connect message
            connect_msg = {
                'type': 'connect',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(connect_msg))
            print("✓ Sent connect message")
            
            # Test 2: Send start message
            start_msg = {
                'type': 'start',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'pid': 99999,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(start_msg))
            print("✓ Sent start message")
            
            # Test 3: Send heartbeat
            heartbeat_msg = {
                'type': 'heartbeat',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(heartbeat_msg))
            print("✓ Sent heartbeat message")
            
            # Test 4: Send output
            output_msg = {
                'type': 'output',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'stream': 'stdout',
                'data': 'Test output line 1\n',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(output_msg))
            print("✓ Sent output message")
            
            # Test 5: Send complete message
            complete_msg = {
                'type': 'complete',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'retcode': 0,
                'output': 'Test output line 1\n',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(complete_msg))
            print("✓ Sent complete message")
            
            print("\n✓ All tests passed!")
            
    except websockets.exceptions.ConnectionRefused:
        print(f"✗ Connection refused - is the WebSocket server running on {url}?")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

async def test_error_message(url='ws://localhost:8889'):
    """Test error message handling"""
    
    print(f"\nTesting error message handling")
    
    try:
        async with websockets.connect(url) as websocket:
            error_msg = {
                'type': 'error',
                'job_name': 'test_job',
                'job_instance': 'test_job_12345',
                'machine': 'test-machine',
                'error': 'Test error message',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(error_msg))
            print("✓ Sent error message")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

async def test_multiple_outputs(url='ws://localhost:8889'):
    """Test streaming multiple output messages"""
    
    print(f"\nTesting multiple output messages")
    
    try:
        async with websockets.connect(url) as websocket:
            # Connect
            connect_msg = {
                'type': 'connect',
                'job_name': 'stream_test',
                'job_instance': 'stream_test_12345',
                'machine': 'test-machine',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(connect_msg))
            
            # Start
            start_msg = {
                'type': 'start',
                'job_name': 'stream_test',
                'job_instance': 'stream_test_12345',
                'machine': 'test-machine',
                'pid': 99999,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(start_msg))
            
            # Send multiple outputs
            for i in range(10):
                output_msg = {
                    'type': 'output',
                    'job_name': 'stream_test',
                    'job_instance': 'stream_test_12345',
                    'machine': 'test-machine',
                    'stream': 'stdout',
                    'data': f'Output line {i+1}\n',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                await websocket.send(json.dumps(output_msg))
                await asyncio.sleep(0.1)  # Simulate real-time streaming
            
            print("✓ Sent 10 output messages")
            
            # Complete
            complete_msg = {
                'type': 'complete',
                'job_name': 'stream_test',
                'job_instance': 'stream_test_12345',
                'machine': 'test-machine',
                'retcode': 0,
                'output': ''.join([f'Output line {i+1}\n' for i in range(10)]),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            await websocket.send(json.dumps(complete_msg))
            print("✓ Sent complete message")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

def main():
    """Run all tests"""
    
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = 'ws://localhost:8889'
    
    print("=" * 60)
    print("WebSocket Server Test Suite")
    print("=" * 60)
    
    # Run tests
    asyncio.run(test_websocket_connection(url))
    asyncio.run(test_error_message(url))
    asyncio.run(test_multiple_outputs(url))
    
    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
