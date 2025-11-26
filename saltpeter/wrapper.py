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
    Subprocess runs independently - WebSocket retries every 2 seconds if disconnected
    Also listens for kill commands from the server
    """
    process = None
    websocket = None
    retry_interval = 2
    
    try:
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
        
        # Start the subprocess FIRST - runs regardless of WebSocket state
        process = subprocess.Popen(command, **proc_kwargs)
        
        last_heartbeat = time.time()
        output_buffer = []
        killed = False
        pending_messages = []
        connection_sent = False
        start_sent = False
        last_retry = 0
        
        # Sequence tracking
        next_seq = 0
        last_acked_seq = -1
        waiting_for_ack = False
        ack_timeout = 5.0  # Seconds to wait for ACK before retry
        last_send_time = 0
        
        # Add initial connection message to pending queue
        pending_messages.append({
            'type': 'connect',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Add start message to pending queue
        pending_messages.append({
            'type': 'start',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'pid': process.pid,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Main loop - runs while process is alive
        while True:
            # Check if process has finished
            retcode = process.poll()
            
            # Try to establish/re-establish WebSocket connection
            if websocket is None:
                current_time = time.time()
                if current_time - last_retry >= retry_interval:
                    try:
                        websocket = await asyncio.wait_for(
                            websockets.connect(websocket_url),
                            timeout=2
                        )
                        last_retry = current_time
                        
                        # Send connect message first (no sequence)
                        for msg in [m for m in pending_messages if m['type'] == 'connect']:
                            try:
                                await websocket.send(json.dumps(msg))
                                # Wait for connect ACK
                                ack_msg = await asyncio.wait_for(websocket.recv(), timeout=2)
                                ack_data = json.loads(ack_msg)
                                if ack_data.get('type') == 'ack' and ack_data.get('ack_type') == 'connect':
                                    # Remove connect message from pending
                                    pending_messages = [m for m in pending_messages if m['type'] != 'connect']
                            except:
                                # If send/ack fails, connection is bad
                                websocket = None
                                break
                            
                    except Exception:
                        # Connection failed, will retry after interval
                        websocket = None
                        last_retry = current_time
            
            # If connected, handle communication
            if websocket is not None:
                try:
                    # Check for incoming messages from server (non-blocking)
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                        data = json.loads(message)
                        
                        if data.get('type') == 'ack':
                            # Process acknowledgement
                            acked_seq = data.get('seq', -1)
                            if acked_seq >= 0 and acked_seq == last_acked_seq + 1:
                                last_acked_seq = acked_seq
                                waiting_for_ack = False
                                # Remove acknowledged message from pending
                                if pending_messages and pending_messages[0].get('seq') == acked_seq:
                                    pending_messages.pop(0)
                        
                        elif data.get('type') == 'nack':
                            # Server detected out-of-order, resync sequence
                            expected_seq = data.get('expected_seq', 0)
                            if expected_seq <= next_seq:
                                # Reset to expected sequence
                                next_seq = expected_seq
                                last_acked_seq = expected_seq - 1
                                waiting_for_ack = False
                        
                        elif data.get('type') == 'kill':
                            killed = True

                            # Terminate the process
                            if process and process.poll() is None:
                                # Close the pipes before terminating to avoid blocking on communicate()
                                try:
                                    if process.stdout:
                                        process.stdout.close()
                                    if process.stderr:
                                        process.stderr.close()
                                except:
                                    pass

                                process.terminate()

                                # Give it 5 seconds to terminate gracefully
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait()

                            break
                            
                    except asyncio.TimeoutError:
                        # No message received, continue normally
                        pass
                    except json.JSONDecodeError:
                        pass
                    
                    # Read available output
                    try:
                        # Non-blocking read from stdout/stderr
                        import select
                        readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
                        
                        for stream in readable:
                            line = stream.readline()
                            if line:
                                stream_type = 'stdout' if stream == process.stdout else 'stderr'
                                output_buffer.append(line)
                                
                                # Send output chunk with sequence (backpressure: only if not waiting for ACK)
                                if not waiting_for_ack:
                                    output_msg = {
                                        'type': 'output',
                                        'job_name': job_name,
                                        'job_instance': job_instance,
                                        'machine': machine_id,
                                        'stream': stream_type,
                                        'data': line,
                                        'seq': next_seq,
                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                    }
                                    try:
                                        await websocket.send(json.dumps(output_msg))
                                        waiting_for_ack = True
                                        last_send_time = time.time()
                                        pending_messages.append(output_msg)
                                        next_seq += 1
                                    except:
                                        # Connection lost, mark for reconnect
                                        websocket = None
                                        waiting_for_ack = False
                                        next_seq -= 1  # Retry this sequence
                    except Exception:
                        pass
                    
                    # Check for ACK timeout and resend if needed
                    if waiting_for_ack and time.time() - last_send_time > ack_timeout:
                        # No ACK received, assume connection issue
                        websocket = None
                        waiting_for_ack = False
                        next_seq -= 1  # Retry this sequence
                    
                    # Send heartbeat every 5 seconds
                    if websocket is not None:
                        current_time = time.time()
                        if current_time - last_heartbeat >= 5:
                            heartbeat_msg = {
                                'type': 'heartbeat',
                                'job_name': job_name,
                                'job_instance': job_instance,
                                'machine': machine_id,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            try:
                                await websocket.send(json.dumps(heartbeat_msg))
                                last_heartbeat = current_time
                            except:
                                # Connection lost
                                websocket = None
                    
                except Exception:
                    # Any error means connection is bad
                    websocket = None
            
            # If process finished, break
            if retcode is not None:
                break
            
            await asyncio.sleep(0.05)
        
        # Process finished - read any remaining output (skip if killed, pipes are closed)
        stdout_remainder = None
        stderr_remainder = None
        
        if not killed:
            try:
                stdout_remainder, stderr_remainder = process.communicate()
            except Exception:
                pass
        
        if stdout_remainder:
            for line in stdout_remainder.splitlines(keepends=True):
                output_buffer.append(line)
                pending_messages.append({
                    'type': 'output',
                    'job_name': job_name,
                    'job_instance': job_instance,
                    'machine': machine_id,
                    'stream': 'stdout',
                    'data': line,
                    'seq': next_seq,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                next_seq += 1
        
        if stderr_remainder:
            for line in stderr_remainder.splitlines(keepends=True):
                output_buffer.append(line)
                pending_messages.append({
                    'type': 'output',
                    'job_name': job_name,
                    'job_instance': job_instance,
                    'machine': machine_id,
                    'stream': 'stderr',
                    'data': line,
                    'seq': next_seq,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                next_seq += 1
        
        # Determine final return code
        final_retcode = process.returncode
        if killed:
            # Add message to output about being killed
            kill_msg = "\n[Job terminated by user request]\n"
            output_buffer.append(kill_msg)
            pending_messages.append({
                'type': 'output',
                'job_name': job_name,
                'job_instance': job_instance,
                'machine': machine_id,
                'stream': 'stderr',
                'data': kill_msg,
                'seq': next_seq,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            next_seq += 1
            # Use special return code for killed jobs
            if final_retcode is None or final_retcode >= 0:
                final_retcode = 143  # Standard SIGTERM exit code
        
        # Add completion message to pending queue
        pending_messages.append({
            'type': 'complete',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'retcode': final_retcode,
            'output': ''.join(output_buffer),
            'seq': next_seq,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Retry sending completion and pending messages until successful with ACK
        max_completion_retries = 30  # Try for 60 seconds
        for attempt in range(max_completion_retries):
            try:
                if websocket is None:
                    websocket = await asyncio.wait_for(
                        websockets.connect(websocket_url),
                        timeout=2
                    )
                
                # Send all pending messages including completion and wait for ACKs
                for msg in pending_messages:
                    await websocket.send(json.dumps(msg))
                    # Wait for ACK for each message
                    try:
                        ack_msg = await asyncio.wait_for(websocket.recv(), timeout=2)
                        ack_data = json.loads(ack_msg)
                        if ack_data.get('type') == 'nack':
                            # Server wants resend, will retry entire batch
                            raise Exception('NACK received')
                    except asyncio.TimeoutError:
                        # No ACK, will retry
                        raise Exception('ACK timeout')
                
                # Success - all messages ACKed, exit retry loop
                break
                
            except Exception:
                websocket = None
                if attempt < max_completion_retries - 1:
                    await asyncio.sleep(retry_interval)
        
    except Exception as e:
        # Make sure process is terminated if it's still running
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                try:
                    process.kill()
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
        sys.stdout.flush()  # Ensure output is sent before exit
        sys.exit(0)
    
    # Child process - become session leader to detach from parent
    os.setsid()
    
    # Double fork to prevent zombie processes and fully detach
    pid2 = os.fork()
    if pid2 > 0:
        # First child exits
        sys.exit(0)
    
    # Second child (grandchild) - fully detached daemon
    # Close standard file descriptors to detach from Salt
    sys.stdout.close()
    sys.stderr.close()
    sys.stdin.close()
    
    # Redirect to /dev/null to prevent any issues
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0)  # stdin
    os.dup2(devnull, 1)  # stdout
    os.dup2(devnull, 2)  # stderr
    if devnull > 2:
        os.close(devnull)
    
    # Run the command asynchronously
    asyncio.run(run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd, user, timeout))

if __name__ == "__main__":
    main()
