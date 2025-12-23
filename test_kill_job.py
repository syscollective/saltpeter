#!/usr/bin/env python3
"""
Test script to verify bidirectional WebSocket communication
This simulates the API sending a kill command
"""

import time
import requests
import json

# Configuration
API_HOST = "localhost"
API_PORT = 8888
JOB_NAME = "test_job"  # Replace with actual job name you want to kill

def send_kill_command():
    """Send a kill command via the API WebSocket interface"""
    try:
        # Connect to API WebSocket
        import websocket
        
        ws_url = f"ws://{API_HOST}:{API_PORT}/ws"
        print(f"Connecting to {ws_url}")
        
        ws = websocket.create_connection(ws_url)
        print("Connected to API WebSocket")
        
        # Wait a moment to receive initial state
        time.sleep(1)
        
        # Send kill command
        kill_message = json.dumps({
            'killCron': JOB_NAME
        })
        
        print(f"Sending kill command for job: {JOB_NAME}")
        ws.send(kill_message)
        
        print("Kill command sent successfully")
        print("Check the Saltpeter logs to see if the job was terminated")
        
        # Wait a bit to see response
        time.sleep(2)
        
        ws.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        JOB_NAME = sys.argv[1]
    
    print(f"Testing kill functionality for job: {JOB_NAME}")
    send_kill_command()
