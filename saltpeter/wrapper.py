#!/usr/bin/env python3
"""
Saltpeter Wrapper Script
This script is executed by Salt and immediately returns success.
It runs the actual command in a subprocess and communicates with
Saltpeter via WebSocket, sending heartbeats, streaming output,
and reporting the final exit code.

Environment Variables:
    SP_WEBSOCKET_URL - WebSocket server URL (required)
    SP_JOB_NAME - Name of the cron job (required)
    SP_JOB_INSTANCE - Unique instance identifier (required)
    SP_MACHINE_ID - Machine hostname/identifier (optional, defaults to hostname)
    SP_COMMAND - Command to execute (required)
    SP_CWD - Working directory (optional)
    SP_USER - User to run command as (optional)
    SP_TIMEOUT - Command timeout in seconds (optional)
"""

import asyncio
import websockets
import subprocess
import sys
import os
import json
import time
import socket
from datetime import datetime, timezone

async def run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd=None, user=None, timeout=None):
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
    # Read configuration from environment variables
    websocket_url = os.environ.get('SP_WEBSOCKET_URL')
    job_name = os.environ.get('SP_JOB_NAME')
    job_instance = os.environ.get('SP_JOB_INSTANCE')
    machine_id = os.environ.get('SP_MACHINE_ID') or socket.getfqdn()
    command = os.environ.get('SP_COMMAND')
    cwd = os.environ.get('SP_CWD')
    user = os.environ.get('SP_USER')
    timeout_str = os.environ.get('SP_TIMEOUT')
    
    # Validate required parameters
    if not websocket_url:
        print("Error: SP_WEBSOCKET_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    if not job_name:
        print("Error: SP_JOB_NAME environment variable not set", file=sys.stderr)
        sys.exit(1)
    if not job_instance:
        print("Error: SP_JOB_INSTANCE environment variable not set", file=sys.stderr)
        sys.exit(1)
    if not command:
        print("Error: SP_COMMAND environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    # Parse timeout
    timeout = None
    if timeout_str:
        try:
            timeout = int(timeout_str)
        except ValueError:
            print(f"Warning: Invalid timeout value '{timeout_str}', ignoring", file=sys.stderr)
    
    # Fork to background so Salt sees immediate success
    pid = os.fork()
    if pid > 0:
        # Parent process - return success to Salt immediately
        print("Wrapper started successfully")
        sys.exit(0)
    
    # Child process - run the command asynchronously
    # Detach from parent
    os.setsid()
    
    # Close standard file descriptors to detach from Salt
    sys.stdout.close()
    sys.stderr.close()
    sys.stdin.close()
    
    # Run the command asynchronously
    asyncio.run(run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd, user, timeout))

if __name__ == "__main__":
    main()
