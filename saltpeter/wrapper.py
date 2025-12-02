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
    SP_OUTPUT_INTERVAL_MS - Minimum interval between output messages in milliseconds (optional, default: 1000)
    SP_DEBUG_LOG - Path to debug log file for wrapper troubleshooting (optional, e.g., /tmp/sp_wrapper_debug.log)
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

# Handle both package import and direct execution
try:
    from .version import __version__
except ImportError:
    # When run as script, try absolute import
    try:
        from saltpeter.version import __version__
    except ImportError:
        # Fallback if version.py not accessible
        __version__ = 'unknown'

async def run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd=None, user=None, timeout=None):
    """
    Run command in subprocess and stream output via WebSocket
    Subprocess runs independently - WebSocket retries every 2 seconds if disconnected
    Also listens for kill commands from the server
    
    Heartbeats are sent every 5 seconds. Communication is retried until the 
    job's configured timeout is reached, at which point the job is killed.
    """
    
    def create_output_messages(output_data, seq_start):
        """Split large output into chunks and create messages. Returns (messages, next_seq)"""
        messages = []
        seq = seq_start
        
        if len(output_data) <= max_chunk_size:
            messages.append({
                'type': 'output',
                'job_name': job_name,
                'job_instance': job_instance,
                'machine': machine_id,
                'stream': 'stdout',
                'data': output_data,
                'seq': seq,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            seq += 1
        else:
            # Split into chunks
            for i in range(0, len(output_data), max_chunk_size):
                chunk = output_data[i:i+max_chunk_size]
                messages.append({
                    'type': 'output',
                    'job_name': job_name,
                    'job_instance': job_instance,
                    'machine': machine_id,
                    'stream': 'stdout',
                    'data': chunk,
                    'seq': seq,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
                seq += 1
        
        return messages, seq
    
    process = None
    websocket = None
    retry_interval = 2
    heartbeat_interval = 5  # Fixed 5-second heartbeat interval
    
    # Output buffering configuration
    # WebSocket default frame limit is 1MB (1048576 bytes)
    output_interval_ms = int(os.environ.get('SP_OUTPUT_INTERVAL_MS', '1000'))  # Default 1 second
    output_interval = output_interval_ms / 1000.0  # Convert to seconds
    output_max_size = 500 * 1024  # Hardcoded: 500KB to stay well under 1MB frame limit
    max_chunk_size = 500 * 1024  # Hardcoded: split messages larger than 500KB
    
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
        last_send_time = 0
        last_output_send_time = 0  # Track when we last sent output
        last_sync_request_time = 0  # Track when we last requested sync
        
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
            'version': __version__,
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
                        
                        # Send start message if still pending
                        if websocket is not None:
                            for msg in [m for m in pending_messages if m['type'] == 'start']:
                                try:
                                    await websocket.send(json.dumps(msg))
                                    # Remove start from pending after sending (no ACK expected)
                                    pending_messages = [m for m in pending_messages if m['type'] != 'start']
                                except:
                                    websocket = None
                                    break
                        
                        # After reconnect, resend all pending output messages
                        if websocket is not None:
                            unsent = [m for m in pending_messages if m.get('type') == 'output' and m.get('seq', 0) > last_acked_seq]
                            for unsent_msg in unsent:
                                try:
                                    await websocket.send(json.dumps(unsent_msg))
                                    print(f'[WRAPPER DEBUG] Resent pending seq={unsent_msg.get("seq")}', file=sys.stderr, flush=True)
                                except:
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
                            # Process acknowledgement - must be for next expected seq
                            acked_seq = data.get('seq', -1)
                            if acked_seq >= 0 and acked_seq == last_acked_seq + 1:
                                last_acked_seq = acked_seq
                                waiting_for_ack = False
                                print(f'[WRAPPER DEBUG] ACK received: seq={acked_seq}', file=sys.stderr, flush=True)
                                # Remove acknowledged message from pending
                                pending_messages = [m for m in pending_messages if m.get('seq', -1) != acked_seq]
                                # Clear output buffer now that message is confirmed delivered
                                output_buffer = []
                            elif acked_seq > last_acked_seq + 1:
                                # Server ACKed ahead of us - we missed some ACKs, sync up
                                print(f'[WRAPPER DEBUG] ACK gap detected: got {acked_seq}, expected {last_acked_seq + 1}', file=sys.stderr, flush=True)
                                last_acked_seq = acked_seq
                                waiting_for_ack = False
                                pending_messages = [m for m in pending_messages if m.get('seq', -1) > acked_seq]
                                output_buffer = []
                        
                        elif data.get('type') == 'nack':
                            # Server detected issue - log it but continue
                            expected_seq = data.get('expected_seq', 0)
                            print(f'[WRAPPER DEBUG] NACK received: expected={expected_seq}, next={next_seq}', file=sys.stderr, flush=True)
                        
                        elif data.get('type') == 'sync_response':
                            # Server tells us what it last received
                            server_last_seq = data.get('last_seq', -1)
                            print(f'[WRAPPER DEBUG] Sync response: server_last={server_last_seq}, our_last_acked={last_acked_seq}', file=sys.stderr, flush=True)
                            
                            if server_last_seq >= last_acked_seq:
                                # Server is ahead or equal, update our state
                                last_acked_seq = server_last_seq
                                waiting_for_ack = False
                                # Clean up pending messages up to server's position
                                pending_messages = [m for m in pending_messages if m.get('seq', -1) > server_last_seq]
                                output_buffer = []
                            
                            # Resend any pending messages after server's position
                            unsent = [m for m in pending_messages if m.get('type') == 'output' and m.get('seq', 0) > server_last_seq]
                            if unsent:
                                print(f'[WRAPPER DEBUG] Resending {len(unsent)} messages after sync', file=sys.stderr, flush=True)
                                for msg in unsent:
                                    try:
                                        await websocket.send(json.dumps(msg))
                                    except:
                                        websocket = None
                                        break
                        
                        elif data.get('type') == 'sync_response':
                            # Server tells us what it last received
                            server_last_seq = data.get('last_seq', -1)
                            print(f'[WRAPPER DEBUG] Sync response: server_last={server_last_seq}, our_last_acked={last_acked_seq}', file=sys.stderr, flush=True)
                            
                            if server_last_seq >= last_acked_seq:
                                # Server is ahead or equal, update our state
                                last_acked_seq = server_last_seq
                                waiting_for_ack = False
                                # Clean up pending messages up to server's position
                                pending_messages = [m for m in pending_messages if m.get('seq', -1) > server_last_seq]
                            
                            # Resend any pending messages after server's position
                            unsent = [m for m in pending_messages if m.get('type') == 'output' and m.get('seq', 0) > server_last_seq]
                            if unsent:
                                print(f'[WRAPPER DEBUG] Resending {len(unsent)} messages after sync', file=sys.stderr, flush=True)
                                for msg in unsent:
                                    try:
                                        await websocket.send(json.dumps(msg))
                                    except:
                                        websocket = None
                                        break
                        
                        elif data.get('type') == 'kill':
                            killed = True

                            # Flush any buffered output BEFORE closing pipes
                            if output_buffer and websocket is not None:
                                combined_output = ''.join(line for _, line in output_buffer)
                                output_msg = {
                                    'type': 'output',
                                    'job_name': job_name,
                                    'job_instance': job_instance,
                                    'machine': machine_id,
                                    'stream': 'stdout',
                                    'data': combined_output,
                                    'seq': next_seq,
                                    'timestamp': datetime.now(timezone.utc).isoformat()
                                }
                                pending_messages.append(output_msg)
                                next_seq += 1
                                output_buffer = []
                            
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
                            # Read ALL available lines from this stream, not just one
                            while True:
                                line = stream.readline()
                                if not line:
                                    break
                                stream_type = 'stdout' if stream == process.stdout else 'stderr'
                                output_buffer.append((stream_type, line))
                                print(f'[WRAPPER DEBUG] Buffered line: {repr(line[:50])}... (buffer_size={sum(len(l) for _, l in output_buffer)})', file=sys.stderr, flush=True)
                                
                                # Check if more data is immediately available
                                # If not, break to avoid blocking on readline()
                                ready, _, _ = select.select([stream], [], [], 0)
                                if not ready:
                                    break
                    except Exception:
                        pass
                    
                    # If waiting for ACK too long, request sync from server
                    current_time = time.time()
                    if waiting_for_ack and websocket is not None:
                        time_waiting = current_time - last_send_time
                        if time_waiting >= 1.0 and (current_time - last_sync_request_time) >= 1.0:
                            print(f'[WRAPPER DEBUG] Waiting for ACK {time_waiting:.1f}s, requesting sync', file=sys.stderr, flush=True)
                            sync_request = {
                                'type': 'sync_request',
                                'job_name': job_name,
                                'job_instance': job_instance,
                                'machine': machine_id,
                                'last_acked_seq': last_acked_seq,
                                'next_seq': next_seq,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            try:
                                await websocket.send(json.dumps(sync_request))
                                last_sync_request_time = current_time
                            except:
                                websocket = None
                                waiting_for_ack = False
                    
                    # Check if we should send buffered output (time or size based)
                    buffer_size = sum(len(line) for _, line in output_buffer)
                    time_to_send = (current_time - last_output_send_time >= output_interval)
                    size_to_send = (buffer_size >= output_max_size)
                    
                    if output_buffer and (time_to_send or size_to_send) and not waiting_for_ack and websocket is not None:
                        # Combine all buffered output
                        combined_output = ''.join(line for _, line in output_buffer)
                        
                        # Create messages (may be chunked if large)
                        output_messages, next_seq = create_output_messages(combined_output, next_seq)
                        
                        print(f'[WRAPPER DEBUG] Sending buffer: {len(output_messages)} message(s), total_len={len(combined_output)}, reason={"time" if time_to_send else "size"}', file=sys.stderr, flush=True)
                        
                        # Add all messages to pending queue
                        pending_messages.extend(output_messages)
                        
                        # Send first message (others will be sent on reconnect if needed)
                        try:
                            await websocket.send(json.dumps(output_messages[0]))
                            waiting_for_ack = True
                            last_send_time = current_time
                            last_output_send_time = current_time
                            # Don't clear output_buffer until ACKed!
                        except:
                            # Connection lost, mark for reconnect
                            websocket = None
                            waiting_for_ack = False
                    
                    # If waiting for ACK too long, request sync from server
                    if waiting_for_ack and websocket is not None:
                        time_waiting = current_time - last_send_time
                        if time_waiting >= 1.0 and (current_time - last_sync_request_time) >= 1.0:
                            print(f'[WRAPPER DEBUG] Waiting for ACK {time_waiting:.1f}s, requesting sync', file=sys.stderr, flush=True)
                            sync_request = {
                                'type': 'sync_request',
                                'job_name': job_name,
                                'job_instance': job_instance,
                                'machine': machine_id,
                                'last_acked_seq': last_acked_seq,
                                'next_seq': next_seq,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            try:
                                await websocket.send(json.dumps(sync_request))
                                last_sync_request_time = current_time
                            except:
                                websocket = None
                                waiting_for_ack = False
                
                except Exception:
                    # Any error means connection is bad
                    websocket = None
                
                # Send heartbeat at calculated interval - do this regardless of other state
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    if websocket is not None:
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
                    else:
                        # Update heartbeat timer even if disconnected to avoid spam when reconnecting
                        last_heartbeat = current_time
            
            # If process finished, break
            if retcode is not None:
                break
            
            await asyncio.sleep(0.05)
        
        # Process finished - ensure ALL buffered output is sent
        # Force send even if waiting_for_ack (this is the final flush)
        if output_buffer:
            combined_output = ''.join(line for _, line in output_buffer)
            output_messages, next_seq = create_output_messages(combined_output, next_seq)
            pending_messages.extend(output_messages)
            output_buffer = []
        
        # Read any remaining output (skip if killed, pipes are closed)
        stdout_remainder = None
        stderr_remainder = None
        
        if not killed:
            try:
                stdout_remainder, stderr_remainder = process.communicate()
            except Exception:
                pass
        
        # Add all remaining output
        if stdout_remainder or stderr_remainder:
            combined_remainder = (stdout_remainder or '') + (stderr_remainder or '')
            if combined_remainder:
                remainder_messages, next_seq = create_output_messages(combined_remainder, next_seq)
                pending_messages.extend(remainder_messages)
        
        # Determine final return code
        final_retcode = process.returncode
        if killed:
            # Add message to output about being killed
            kill_msg = "\n[Job terminated by user request]\n"
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
        print(f'[WRAPPER DEBUG] Adding completion message: retcode={final_retcode}, pending_count={len(pending_messages)}', file=sys.stderr, flush=True)
        pending_messages.append({
            'type': 'complete',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'retcode': final_retcode,
            'seq': next_seq,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Retry sending completion and pending messages until successful with ACK
        max_completion_retries = 30  # Try for 60 seconds
        print(f'[WRAPPER DEBUG] Starting completion retry loop with {len(pending_messages)} pending messages', file=sys.stderr, flush=True)
        for attempt in range(max_completion_retries):
            try:
                if websocket is None:
                    print(f'[WRAPPER DEBUG] Reconnecting for completion (attempt {attempt+1})', file=sys.stderr, flush=True)
                    websocket = await asyncio.wait_for(
                        websockets.connect(websocket_url),
                        timeout=2
                    )
                
                # Send all pending messages including completion and wait for ACKs
                print(f'[WRAPPER DEBUG] Sending {len(pending_messages)} pending messages', file=sys.stderr, flush=True)
                for msg in pending_messages:
                    await websocket.send(json.dumps(msg))
                    print(f'[WRAPPER DEBUG] Sent {msg["type"]} seq={msg.get("seq", "none")}', file=sys.stderr, flush=True)
                    # Wait for ACK for each message
                    try:
                        ack_msg = await asyncio.wait_for(websocket.recv(), timeout=2)
                        ack_data = json.loads(ack_msg)
                        if ack_data.get('type') == 'nack':
                            # Server wants resend, will retry entire batch
                            print(f'[WRAPPER DEBUG] NACK received during completion', file=sys.stderr, flush=True)
                            raise Exception('NACK received')
                        print(f'[WRAPPER DEBUG] ACK received for {msg["type"]}', file=sys.stderr, flush=True)
                    except asyncio.TimeoutError:
                        # No ACK, will retry
                        print(f'[WRAPPER DEBUG] ACK timeout for {msg["type"]}', file=sys.stderr, flush=True)
                        raise Exception('ACK timeout')
                
                # Success - all messages ACKed, exit retry loop
                print(f'[WRAPPER DEBUG] All completion messages ACKed successfully', file=sys.stderr, flush=True)
                break
                
            except Exception as e:
                print(f'[WRAPPER DEBUG] Completion send failed: {e}', file=sys.stderr, flush=True)
                websocket = None
                if attempt < max_completion_retries - 1:
                    await asyncio.sleep(retry_interval)
        else:
            print(f'[WRAPPER DEBUG] Failed to send completion after {max_completion_retries} attempts', file=sys.stderr, flush=True)
        
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
    # Print version first (always output to stdout for testing/verification)
    print(f"Saltpeter Wrapper version {__version__}")
    
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
    sys.stdin.close()
    
    # Check if debug logging is enabled (default to /tmp/sp_wrapper_debug.log)
    debug_log = os.environ.get('SP_DEBUG_LOG', '/tmp/sp_wrapper_debug.log')
    
    # Redirect stderr to debug log file
    try:
        stderr_fd = os.open(debug_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(stderr_fd, 2)
        if stderr_fd > 2:
            os.close(stderr_fd)
        print(f'[WRAPPER] Debug logging to {debug_log}', file=sys.stderr, flush=True)
    except Exception as e:
        # If debug log fails, fall back to /dev/null
        sys.stderr.close()
    
    # Redirect stdin/stdout to /dev/null
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0)  # stdin
    os.dup2(devnull, 1)  # stdout
    if devnull > 2:
        os.close(devnull)
    
    # Run the command asynchronously
    asyncio.run(run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd, user, timeout))

if __name__ == "__main__":
    main()