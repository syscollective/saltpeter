#!/usr/bin/env python3
"""
Saltpeter Wrapper Script
This script is executed by Salt and immediately returns success.
It runs the actual command in a subprocess and communicates with
Saltpeter via WebSocket, sending heartbeats, streaming output,
and reporting the final exit code.
"""

import asyncio
import websockets
import subprocess
import sys
import os
import json
import time
from datetime import datetime, timezone

async def run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd=None, user=None):
    """
    Run command in subprocess and stream output via WebSocket
    """
    try:
        async with websockets.connect(websocket_url) as websocket:
            # Send initial connection message
            await websocket.send(json.dumps({
                'type': 'connect',
                'job_name': job_name,
                'job_instance': job_instance,
                'machine': machine_id,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }))
            
            # Prepare subprocess arguments
            proc_kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.PIPE,
                'shell': True,
                'text': True,
                'bufsize': 1  # Line buffered
            }
            
            if cwd:
                proc_kwargs['cwd'] = cwd
            
            if user and os.geteuid() == 0:  # Only if running as root
                import pwd
                pw_record = pwd.getpwnam(user)
                proc_kwargs['preexec_fn'] = lambda: os.setuid(pw_record.pw_uid)
            
            # Start the subprocess
            process = subprocess.Popen(command, **proc_kwargs)
            
            # Send start message
            await websocket.send(json.dumps({
                'type': 'start',
                'job_name': job_name,
                'job_instance': job_instance,
                'machine': machine_id,
                'pid': process.pid,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }))
            
            last_heartbeat = time.time()
            output_buffer = []
            
            # Stream output and send heartbeats
            while True:
                # Check if process has finished
                retcode = process.poll()
                
                # Read available output
                try:
                    # Non-blocking read from stdout
                    import select
                    readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
                    
                    for stream in readable:
                        line = stream.readline()
                        if line:
                            stream_type = 'stdout' if stream == process.stdout else 'stderr'
                            output_buffer.append(line)
                            
                            # Send output chunk
                            await websocket.send(json.dumps({
                                'type': 'output',
                                'job_name': job_name,
                                'job_instance': job_instance,
                                'machine': machine_id,
                                'stream': stream_type,
                                'data': line,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }))
                except Exception as e:
                    print(f"Error reading output: {e}", file=sys.stderr)
                
                # Send heartbeat every 5 seconds
                current_time = time.time()
                if current_time - last_heartbeat >= 5:
                    await websocket.send(json.dumps({
                        'type': 'heartbeat',
                        'job_name': job_name,
                        'job_instance': job_instance,
                        'machine': machine_id,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }))
                    last_heartbeat = current_time
                
                # If process finished, break
                if retcode is not None:
                    break
                
                await asyncio.sleep(0.1)
            
            # Read any remaining output
            stdout_remainder, stderr_remainder = process.communicate()
            
            if stdout_remainder:
                for line in stdout_remainder.splitlines(keepends=True):
                    output_buffer.append(line)
                    await websocket.send(json.dumps({
                        'type': 'output',
                        'job_name': job_name,
                        'job_instance': job_instance,
                        'machine': machine_id,
                        'stream': 'stdout',
                        'data': line,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }))
            
            if stderr_remainder:
                for line in stderr_remainder.splitlines(keepends=True):
                    output_buffer.append(line)
                    await websocket.send(json.dumps({
                        'type': 'output',
                        'job_name': job_name,
                        'job_instance': job_instance,
                        'machine': machine_id,
                        'stream': 'stderr',
                        'data': line,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }))
            
            # Send completion message
            await websocket.send(json.dumps({
                'type': 'complete',
                'job_name': job_name,
                'job_instance': job_instance,
                'machine': machine_id,
                'retcode': retcode,
                'output': ''.join(output_buffer),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }))
            
    except Exception as e:
        print(f"Error in WebSocket communication: {e}", file=sys.stderr)
        # Try to send error message
        try:
            async with websockets.connect(websocket_url) as websocket:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'job_name': job_name,
                    'job_instance': job_instance,
                    'machine': machine_id,
                    'error': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }))
        except:
            pass

def main():
    if len(sys.argv) < 6:
        print("Usage: wrapper.py <websocket_url> <job_name> <job_instance> <machine_id> <command> [cwd] [user]")
        sys.exit(1)
    
    websocket_url = sys.argv[1]
    job_name = sys.argv[2]
    job_instance = sys.argv[3]
    machine_id = sys.argv[4]
    command = sys.argv[5]
    cwd = sys.argv[6] if len(sys.argv) > 6 else None
    user = sys.argv[7] if len(sys.argv) > 7 else None
    
    # Run the command asynchronously
    asyncio.run(run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd, user))
    
    # Return success immediately to Salt
    print("Wrapper started successfully")
    sys.exit(0)

if __name__ == "__main__":
    main()
