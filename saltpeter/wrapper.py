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
    SP_WRAPPER_LOGLEVEL - Wrapper logging level: 'normal', 'debug', 'off' (optional, default: 'normal')
    SP_WRAPPER_LOGDIR - Directory for wrapper log files (optional, default: '/var/log/sp_wrapper')
"""

import asyncio
import websockets
import subprocess
import sys
import os
import json
import time
import socket
import signal
from datetime import datetime, timezone

# Monkey-patch hashlib.sha1 for FIPS compatibility
# FIPS mode blocks hashlib.sha1() but allows hashlib.new('sha1', usedforsecurity=False)
# WebSocket library uses sha1 for non-security purposes (handshake key generation)
import hashlib
_original_sha1 = hashlib.sha1
def _fips_compatible_sha1(data=b''):
    """SHA1 wrapper that works in FIPS mode"""
    try:
        return _original_sha1(data)
    except (AttributeError, ValueError):
        # FIPS mode - use non-security SHA1
        return hashlib.new('sha1', data, usedforsecurity=False)

hashlib.sha1 = _fips_compatible_sha1

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

async def run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd=None, user=None, timeout=None, loglevel='normal', logdir='/var/log/sp_wrapper', lockfile_path=None, lockfile_acquired=False):
    """
    Run command in subprocess and stream output via WebSocket
    Subprocess runs independently - WebSocket retries every 2 seconds if disconnected
    Also listens for kill commands from the server
    
    Heartbeats are sent every 5 seconds. Communication is retried until the 
    job's configured timeout is reached, at which point the job is killed.
    
    lockfile_path: Path to lockfile (for deletion in finally if acquired)
    lockfile_acquired: If True, lockfile will be deleted in finally block
    """
    
    # Setup wrapper logging
    log_file = None
    log_errors = []  # Collect any logging setup errors to send to server
    
    if loglevel in ('normal', 'debug'):
        try:
            # Create log directory (mkdir -p)
            os.makedirs(logdir, mode=0o755, exist_ok=True)
            log_path = os.path.join(logdir, f"{job_name}.log")
            # Open in append mode
            log_file = open(log_path, 'a')
        except Exception as e:
            log_errors.append(f"Failed to setup wrapper logging to {logdir}/{job_name}.log: {e}")
    
    # Create logging function with timestamp and prefix
    log_prefix = f"{job_instance}"
    def log(msg, level='normal'):
        """Log message with timestamp and job prefix
        level: 'normal' (always logged if not 'off'), 'debug' (only if loglevel='debug')
        """
        if loglevel == 'off':
            return
        if level == 'debug' and loglevel != 'debug':
            return
        
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"{timestamp} [{log_prefix}] {msg}\n"
        
        if log_file:
            try:
                log_file.write(log_line)
                log_file.flush()
            except:
                pass  # Ignore write errors, don't break execution
    
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
    
    # Background task for receiving WebSocket messages
    # This ensures kill/control messages are never missed
    async def websocket_receiver(ws, msg_queue):
        """Continuously receive messages and put them in queue"""
        try:
            while True:
                message = await ws.recv()
                await msg_queue.put(message)
        except Exception:
            # Connection closed or error - just exit
            pass
    
    def close_websocket():
        """Helper to close websocket and cancel receiver task"""
        nonlocal websocket, recv_task
        websocket = None
        if recv_task is not None:
            recv_task.cancel()
            recv_task = None
    
    try:
        # Prepare subprocess arguments - combine stdout and stderr
        proc_kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.STDOUT,  # Merge stderr into stdout for correct ordering
            'shell': True,
            'text': False,  # Binary mode to preserve \r
            'bufsize': 0,  # Unbuffered
            'preexec_fn': os.setsid  # Create new session - allows killing process group
        }
        
        if cwd:
            proc_kwargs['cwd'] = cwd
        
        if user and os.geteuid() == 0:  # Only if running as root
            import pwd
            pw_record = pwd.getpwnam(user)
            # Chain preexec functions: setsid + setuid
            proc_kwargs['preexec_fn'] = lambda: (os.setsid(), os.setuid(pw_record.pw_uid))
        
        # Start the subprocess
        process = subprocess.Popen(command, **proc_kwargs)
        
        # Track job start time for timeout enforcement
        job_start_time = time.time()
        
        last_heartbeat = time.time()
        output_buffer = []  # Unsent output (cleared after converting to messages)
        full_output = []  # Complete output for local logging
        killed = False
        killed_by_timeout = False
        pending_messages = []
        connection_sent = False
        start_sent = False
        last_retry = 0
        
        # Sequence tracking
        next_seq = 0
        last_acked_seq = -1
        waiting_for_ack = False
        last_send_time = 0
        last_output_send_time = time.time()  # Track when we last sent output (start from now)
        last_sync_request_time = 0  # Track when we last requested sync
        
        # Incoming message queue for kill/control messages
        # This ensures we never miss kill messages regardless of what main loop is doing
        incoming_messages = asyncio.Queue()
        recv_task = None  # Background task for receiving WebSocket messages
        
        # Buffer-to-sequence mapping for retransmission support
        # Add initial connection message to pending queue
        pending_messages.append({
            'type': 'connect',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Log start
        log(f'Starting job: {command}', level='normal')
        
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
        
        # Prepend any log setup errors to output buffer
        if log_errors:
            for error in log_errors:
                output_buffer.append(f"[WRAPPER LOG ERROR] {error}\n")
        
        # Main loop - runs while process is alive
        while True:
            # Check if process has finished
            retcode = process.poll()
            
            # Check if timeout has been exceeded
            if timeout is not None and not killed:
                elapsed = time.time() - job_start_time
                if elapsed > timeout:
                    log(f'Job timeout exceeded ({elapsed:.1f}s > {timeout}s), terminating process', level='normal')
                    killed = True
                    killed_by_timeout = True
                    
                    # Flush any buffered output before terminating
                    if output_buffer and websocket is not None:
                        combined_output = ''.join(output_buffer)
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
                        try:
                            if process.stdout:
                                process.stdout.close()
                            if process.stderr:
                                process.stderr.close()
                        except:
                            pass
                        
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    
                    break
            
            # Try to establish/re-establish WebSocket connection
            if websocket is None:
                current_time = time.time()
                if current_time - last_retry >= retry_interval:
                    try:
                        websocket = await asyncio.wait_for(
                            websockets.connect(websocket_url),
                            timeout=2
                        )
                        # Start background receiver task
                        if recv_task is not None:
                            recv_task.cancel()
                        recv_task = asyncio.create_task(websocket_receiver(websocket, incoming_messages))
                    except AttributeError as e:
                        # Python hashlib missing sha1 - fatal error, cannot use WebSocket
                        if 'sha1' in str(e):
                            log(f'ERROR: Python installation missing hashlib.sha1 - WebSocket unavailable')
                            log(f'ERROR: Continuing without server communication - job will appear to hang')
                            close_websocket()
                            # Don't retry - this is fatal
                            last_retry = current_time + 999999  # Prevent future retries
                            continue
                        raise
                    except Exception as e:
                        # Connection failed, will retry after interval
                        log(f'Connection failed: {type(e).__name__}: {e}')
                        close_websocket()
                        last_retry = current_time
                        continue
                    
                    try:
                        last_retry = current_time
                        
                        # Send connect message first (no sequence)
                        for msg in [m for m in pending_messages if m['type'] == 'connect']:
                            try:
                                await websocket.send(json.dumps(msg))
                                # Wait for connect ACK from queue (background receiver is running)
                                ack_msg = await asyncio.wait_for(incoming_messages.get(), timeout=2)
                                ack_data = json.loads(ack_msg)
                                if ack_data.get('type') == 'ack' and ack_data.get('ack_type') == 'connect':
                                    # Remove connect message from pending
                                    pending_messages = [m for m in pending_messages if m['type'] != 'connect']
                            except:
                                # If send/ack fails, connection is bad
                                close_websocket()
                                break
                        
                        # Send start message if still pending and wait for ACK
                        if websocket is not None:
                            for msg in [m for m in pending_messages if m['type'] == 'start']:
                                try:
                                    await websocket.send(json.dumps(msg))
                                    # Wait for start ACK from queue (background receiver is running)
                                    ack_msg = await asyncio.wait_for(incoming_messages.get(), timeout=2)
                                    ack_data = json.loads(ack_msg)
                                    if ack_data.get('type') == 'ack' and ack_data.get('ack_type') == 'start':
                                        # Remove start message from pending after ACK
                                        pending_messages = [m for m in pending_messages if m['type'] != 'start']
                                except:
                                    # If send/ack fails, connection is bad
                                    close_websocket()
                                    break
                        
                        # After reconnect, resend all pending output messages
                        if websocket is not None:
                            unsent = [m for m in pending_messages if m.get('type') == 'output' and m.get('seq', 0) > last_acked_seq]
                            for unsent_msg in unsent:
                                try:
                                    await websocket.send(json.dumps(unsent_msg))
                                    log(f'Resent pending seq={unsent_msg.get("seq")}')
                                except:
                                    close_websocket()
                                    break
                            
                    except Exception:
                        # Connection failed, will retry after interval
                        close_websocket()
                        last_retry = current_time
            
            # If connected, handle communication
            if websocket is not None:
                try:
                    # Check for incoming messages from queue (populated by background receiver)
                    # This ensures we never miss kill messages regardless of what else is happening
                    try:
                        message = await asyncio.wait_for(incoming_messages.get(), timeout=0.01)
                        data = json.loads(message)
                        log(f'Received message: type={data.get("type")}', level='debug')
                        
                        if data.get('type') == 'ack':
                            # Process acknowledgement
                            acked_seq = data.get('seq', -1)
                            if acked_seq >= 0:
                                if acked_seq > last_acked_seq:
                                    # Update last ACKed position
                                    log(f'ACK received: seq={acked_seq} (last was {last_acked_seq})')
                                    last_acked_seq = acked_seq
                                    waiting_for_ack = False
                                    # Remove all acknowledged output messages (up to and including acked_seq)
                                    pending_messages = [m for m in pending_messages if not (m.get('type') == 'output' and m.get('seq', -1) <= acked_seq)]
                                    log(f'Cleared pending messages up to seq {acked_seq}')
                                elif acked_seq == last_acked_seq:
                                    # Duplicate ACK (already processed)
                                    log(f'Duplicate ACK: seq={acked_seq}')
                                    waiting_for_ack = False
                                else:
                                    # Old ACK for already-processed message
                                    log(f'Old ACK: seq={acked_seq} (last was {last_acked_seq})')
                                    # Still process it to clear waiting state
                                    waiting_for_ack = False
                        
                        elif data.get('type') == 'nack':
                            # Server detected issue - log it but continue
                            expected_seq = data.get('expected_seq', 0)
                            log(f'NACK received: expected={expected_seq}, next={next_seq}')
                        
                        elif data.get('type') == 'sync_response':
                            # Server tells us what it last received
                            server_last_seq = data.get('last_seq', -1)
                            log(f'Sync response: server_last={server_last_seq}, our_last_acked={last_acked_seq}')
                            
                            # server_last=-1 means job doesn't exist on server (stopped/orphaned)
                            # Don't try to resend - server won't accept it
                            if server_last_seq == -1:
                                log(f'Job no longer exists on server, stopping retries')
                                waiting_for_ack = False
                                # Clear pending output messages - server won't accept them
                                pending_messages = [m for m in pending_messages if m.get('type') != 'output']
                            elif server_last_seq >= last_acked_seq:
                                # Server is ahead or equal, update our state
                                last_acked_seq = server_last_seq
                                waiting_for_ack = False
                                # Remove ACKed output messages from pending
                                pending_messages = [m for m in pending_messages if not (m.get('type') == 'output' and m.get('seq', -1) <= server_last_seq)]
                            
                            # Don't resend immediately - let normal send logic handle it
                            # This avoids flooding the connection with retries
                        
                        elif data.get('type') == 'kill':
                            log(f'KILL message received from server', level='normal')
                            killed = True

                            # Flush any buffered output BEFORE terminating
                            if output_buffer and websocket is not None:
                                combined_output = ''.join(output_buffer)
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
                                # Clear buffer after kill flush
                                output_buffer = []
                            
                            # Terminate the process group (kills shell + all children)
                            if process and process.poll() is None:
                                log(f'Sending SIGTERM to process group', level='normal')
                                try:
                                    # Kill the entire process group (negative PID)
                                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                                except ProcessLookupError:
                                    # Process already dead
                                    pass

                                # Give it 5 seconds to terminate gracefully, sending heartbeats
                                kill_start = time.time()
                                while time.time() - kill_start < 5:
                                    if process.poll() is not None:
                                        log(f'Process group terminated gracefully', level='normal')
                                        break
                                    # Send heartbeat to keep server happy
                                    if websocket and time.time() - last_heartbeat >= 1:
                                        try:
                                            await websocket.send(json.dumps({
                                                'type': 'heartbeat',
                                                'job_name': job_name,
                                                'job_instance': job_instance,
                                                'machine': machine_id,
                                                'timestamp': datetime.now(timezone.utc).isoformat()
                                            }))
                                            last_heartbeat = time.time()
                                        except:
                                            pass
                                    await asyncio.sleep(0.5)
                                
                                # If still alive, escalate to SIGKILL
                                if process.poll() is None:
                                    log(f'Process did not terminate, sending SIGKILL repeatedly', level='normal')
                                    # Retry SIGKILL every 5 seconds until process dies
                                    max_kill_attempts = 10  # 50 seconds total
                                    for attempt in range(max_kill_attempts):
                                        try:
                                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                                        except ProcessLookupError:
                                            break
                                        
                                        # Wait 5s, sending heartbeats
                                        attempt_start = time.time()
                                        while time.time() - attempt_start < 5:
                                            if process.poll() is not None:
                                                log(f'Process group killed after {attempt+1} SIGKILL attempts', level='normal')
                                                break
                                            # Send heartbeat
                                            if websocket and time.time() - last_heartbeat >= 1:
                                                try:
                                                    await websocket.send(json.dumps({
                                                        'type': 'heartbeat',
                                                        'job_name': job_name,
                                                        'job_instance': job_instance,
                                                        'machine': machine_id,
                                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                                    }))
                                                    last_heartbeat = time.time()
                                                except:
                                                    pass
                                            await asyncio.sleep(0.5)
                                        
                                        if process.poll() is not None:
                                            break
                                        
                                        if attempt < max_kill_attempts - 1:
                                            log(f'Process still alive after SIGKILL attempt {attempt+1}, retrying...', level='normal')
                                        else:
                                            log(f'WARNING: Process still alive after {max_kill_attempts} SIGKILL attempts', level='normal')

                            break
                            
                    except asyncio.TimeoutError:
                        # No message received, continue normally
                        pass
                    except json.JSONDecodeError:
                        pass
                    
                    # Read available output from stdout only (stderr merged into it)
                    try:
                        import select
                        ready, _, _ = select.select([process.stdout], [], [], 0)
                        if ready:
                            # Read binary data without blocking
                            chunk = process.stdout.read(8192)
                            if chunk:
                                # Try UTF-8 first, fall back to latin-1 (accepts all bytes)
                                try:
                                    text = chunk.decode('utf-8')
                                except UnicodeDecodeError:
                                    text = chunk.decode('latin-1')
                                output_buffer.append(text)
                                full_output.append(text)  # Keep for local logging
                    except Exception as e:
                        log(f'Error reading output: {e}')
                    
                    # If waiting for ACK too long, request sync from server
                    current_time = time.time()
                    if waiting_for_ack and websocket is not None:
                        time_waiting = current_time - last_send_time
                        if time_waiting >= 1.0 and (current_time - last_sync_request_time) >= 1.0:
                            log(f'Waiting for ACK {time_waiting:.1f}s, requesting sync')
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
                                close_websocket()
                                waiting_for_ack = False
                    
                    # Check if we should send buffered output (time or size based)
                    buffer_size = sum(len(line) for line in output_buffer)
                    time_to_send = (current_time - last_output_send_time >= output_interval)
                    size_to_send = (buffer_size >= output_max_size)
                    
                    if output_buffer and (time_to_send or size_to_send) and not waiting_for_ack and websocket is not None:
                        # Combine all buffered output
                        combined_output = ''.join(output_buffer)
                        
                        if combined_output:
                            # Create messages (may be chunked if large)
                            output_messages, next_seq = create_output_messages(combined_output, next_seq)
                            
                            log(f'Sending buffer: {len(output_messages)} message(s), total_len={len(combined_output)}, reason={"time" if time_to_send else "size"}', level='debug')
                            
                            # Add all messages to pending queue
                            pending_messages.extend(output_messages)
                            # Clear buffer now that it's been converted to messages
                            output_buffer = []
                            
                            # Send first message and wait for ACK before sending rest
                            try:
                                await websocket.send(json.dumps(output_messages[0]))
                                waiting_for_ack = True
                                last_send_time = current_time
                                last_output_send_time = current_time
                                # Don't clear buffer - it will be cleared when server ACKs
                            except:
                                # Connection lost, mark for reconnect
                                close_websocket()
                                waiting_for_ack = False
                    
                    # Send any pending output messages (from multi-chunk or reconnect)
                    # Only send if not waiting for ACK (send one at a time)
                    if not waiting_for_ack and websocket is not None:
                        unsent = [m for m in pending_messages if m.get('type') == 'output' and m.get('seq', 0) > last_acked_seq]
                        if unsent:
                            # Send next unsent message in sequence
                            next_msg = unsent[0]
                            try:
                                await websocket.send(json.dumps(next_msg))
                                waiting_for_ack = True
                                last_send_time = current_time
                                log(f'Sent pending output seq={next_msg.get("seq")}')
                            except:
                                close_websocket()
                                waiting_for_ack = False
                
                except Exception:
                    # Any error means connection is bad
                    close_websocket()
                
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
                            close_websocket()
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
            combined_output = ''.join(output_buffer)
            output_messages, next_seq = create_output_messages(combined_output, next_seq)
            pending_messages.extend(output_messages)
            # Clear buffer after final flush (process complete, won't need retransmission)
            output_buffer = []
        
        # Read any remaining output
        try:
            remaining = process.stdout.read()
            if remaining:
                try:
                    text = remaining.decode('utf-8')
                except UnicodeDecodeError:
                    text = remaining.decode('latin-1')
                remainder_messages, next_seq = create_output_messages(text, next_seq)
                pending_messages.extend(remainder_messages)
        except Exception:
            pass
        
        # Determine final return code
        final_retcode = process.returncode
        if killed:
            # Add message to output about being killed
            if killed_by_timeout:
                kill_msg = f"\n[Job exceeded timeout of {timeout} seconds]\n"
                # Use 124 for timeout (same as GNU timeout command)
                if final_retcode is None or final_retcode >= 0:
                    final_retcode = 124
            else:
                kill_msg = "\n[Job terminated by user request]\n"
                # Use 143 for SIGTERM (user kill)
                if final_retcode is None or final_retcode >= 0:
                    final_retcode = 143
            
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
        
        # Log completion
        status = 'SUCCESS' if final_retcode == 0 else f'FAILED (exit code: {final_retcode})'
        if killed_by_timeout:
            status = f'TIMEOUT (killed after {timeout}s, exit code: {final_retcode})'
        elif killed:
            status = f'KILLED (exit code: {final_retcode})'
        log(f'Job completed: {status}', level='normal')
        
        # Add completion message to pending queue
        log(f'Adding completion message: retcode={final_retcode}, pending_count={len(pending_messages)}', level='debug')
        pending_messages.append({
            'type': 'complete',
            'job_name': job_name,
            'job_instance': job_instance,
            'machine': machine_id,
            'retcode': final_retcode,
            'seq': next_seq,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # All messages in pending should now have proper ACK handling
        # (connect, start, output, complete all get ACK'd by server)
        
        # Filter out messages that were already ACKed during job execution
        # This prevents unnecessary retransmission of messages that succeeded
        pending_messages = [msg for msg in pending_messages if not (msg.get('type') == 'output' and msg.get('seq', -1) <= last_acked_seq)]
        log(f'Filtered pending messages: {len(pending_messages)} need to be sent (already ACKed messages removed)', level='debug')
        
        # Retry sending completion and pending messages until successful with ACK
        max_completion_retries = 30  # Try for 60 seconds
        log(f'Starting completion retry loop with {len(pending_messages)} pending messages')
        acked_indices = []
        for attempt in range(max_completion_retries):
            try:
                if websocket is None:
                    log(f'Reconnecting for completion (attempt {attempt+1})')
                    try:
                        websocket = await asyncio.wait_for(
                            websockets.connect(websocket_url),
                            timeout=2
                        )
                        # Restart background receiver task for completion phase
                        if recv_task is not None:
                            recv_task.cancel()
                        recv_task = asyncio.create_task(websocket_receiver(websocket, incoming_messages))
                    except AttributeError as e:
                        # Python hashlib missing sha1 - fatal error, cannot use WebSocket
                        if 'sha1' in str(e):
                            log(f'ERROR: Python installation missing hashlib.sha1 - WebSocket unavailable')
                            log(f'ERROR: Job completed with retcode={final_retcode} but cannot report to server')
                            return final_retcode
                        raise
                    except Exception as e:
                        log(f'Connection failed: {type(e).__name__}: {e}')
                        raise
                
                # Send all pending messages first (pipeline them)
                log(f'Sending {len(pending_messages)} pending messages')
                for idx, msg in enumerate(pending_messages):
                    await websocket.send(json.dumps(msg))
                    log(f'Sent {msg["type"]} seq={msg.get("seq", "none")}')
                
                # Now wait for ACKs for all messages (with longer timeout for batch)
                # Build expected ACK set
                expected_acks = set()
                for msg in pending_messages:
                    if msg['type'] in ['connect', 'start', 'complete']:
                        expected_acks.add(('type', msg['type']))
                    elif msg['type'] == 'output':
                        expected_acks.add(('seq', msg.get('seq')))
                
                received_acks = set()
                ack_timeout = 5  # 5 seconds for batch ACKs
                start_time = time.time()
                
                try:
                    while received_acks != expected_acks:
                        elapsed = time.time() - start_time
                        remaining = ack_timeout - elapsed
                        if remaining <= 0:
                            log(f'ACK timeout: received {len(received_acks)}/{len(expected_acks)} ACKs')
                            raise Exception('ACK timeout')
                        
                        ack_msg = await asyncio.wait_for(incoming_messages.get(), timeout=remaining)
                        ack_data = json.loads(ack_msg)
                        
                        if ack_data.get('type') == 'error':
                            error_code = ack_data.get('code', 'UNKNOWN')
                            error_msg = ack_data.get('message', 'Unknown error')
                            log(f'Server error received: {error_code} - {error_msg}')
                            log(f'Job completed with exit code {final_retcode} but rejected by server')
                            log(f'Full output follows:')
                            log(f'--- OUTPUT START ---\n' + ''.join(full_output))
                            log(f'--- OUTPUT END ---')
                            return final_retcode
                        
                        if ack_data.get('type') == 'nack':
                            log(f'NACK received during completion')
                            raise Exception('NACK received')
                        
                        if ack_data.get('type') == 'ack':
                            ack_type = ack_data.get('ack_type')
                            ack_seq = ack_data.get('seq')
                            
                            if ack_type in ['connect', 'start', 'complete']:
                                ack_key = ('type', ack_type)
                                if ack_key in expected_acks:
                                    received_acks.add(ack_key)
                                    log(f'ACK received for {ack_type}')
                            elif ack_seq is not None:
                                ack_key = ('seq', ack_seq)
                                if ack_key in expected_acks:
                                    received_acks.add(ack_key)
                                    log(f'ACK received for output seq={ack_seq}')
                
                    # All ACKs received - mark all messages as ACKed
                    log(f'All ACKs received ({len(received_acks)}/{len(expected_acks)})')
                    acked_indices = list(range(len(pending_messages)))
                    
                except asyncio.TimeoutError:
                    log(f'ACK timeout waiting for responses')
                    raise Exception('ACK timeout')
                
                # Success - all messages ACKed, exit retry loop
                log(f'All completion messages ACKed successfully')
                break
                
            except Exception as e:
                log(f'Completion send failed: {e}')
                websocket = None
                # Remove ACKed messages from pending to avoid retransmission
                if acked_indices:
                    pending_messages = [msg for idx, msg in enumerate(pending_messages) if idx not in acked_indices]
                    log(f'Removed {len(acked_indices)} ACKed messages, {len(pending_messages)} remaining')
                    acked_indices = []
                if attempt < max_completion_retries - 1:
                    await asyncio.sleep(retry_interval)
        else:
            # Failed to report completion - log error and dump full output
            log(f'CRITICAL: Failed to send completion after {max_completion_retries} attempts')
            log(f'Job completed with exit code {final_retcode} but server unreachable')
            log(f'Full output follows:')
            log(f'--- OUTPUT START ---\n' + ''.join(full_output))
            log(f'--- OUTPUT END ---')
        
    except Exception as e:
        log(f'Unexpected error in wrapper: {e}', level='normal')
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
    finally:
        # Delete lockfile if we acquired it
        if lockfile_path and lockfile_acquired:
            try:
                os.unlink(lockfile_path)
            except:
                pass
        
        # Close log file
        if log_file:
            try:
                log_file.close()
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
    # Redirect stdin/stdout/stderr to /dev/null to fully detach
    devnull_fd = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull_fd, 0)  # stdin
    os.dup2(devnull_fd, 1)  # stdout
    os.dup2(devnull_fd, 2)  # stderr - redirect instead of closing to avoid Python cleanup errors
    if devnull_fd > 2:
        os.close(devnull_fd)
    
    # Get wrapper logging configuration
    loglevel = os.environ.get('SP_WRAPPER_LOGLEVEL', 'normal')
    logdir = os.environ.get('SP_WRAPPER_LOGDIR', '/var/log/sp_wrapper')
    
    # Get overlap configuration
    allow_overlap_str = os.environ.get('SP_ALLOW_OVERLAP', 'true').lower()
    allow_overlap = allow_overlap_str in ('true', '1', 'yes')
    lockfile_path = os.environ.get('SP_LOCKFILE')  # Optional custom lockfile path
    
    # Handle lockfile for overlap prevention (AFTER double fork, using correct PID)
    lockfile_acquired = False
    if not allow_overlap:
        if lockfile_path is None:
            # Default: /tmp/sp_wrapper_{job_name}.lock
            # Use bare /tmp (world-writable) to work across users
            lockfile_path = f'/tmp/sp_wrapper_{job_name}.lock'
        
        # Create lock directory if custom path specified and doesn't exist
        lockdir = os.path.dirname(lockfile_path)
        if lockdir and lockdir != '/tmp':
            os.makedirs(lockdir, mode=0o777, exist_ok=True)
        
        overlap_error = None
        # Check if lockfile exists
        if os.path.exists(lockfile_path):
            # Read PID from existing file
            try:
                with open(lockfile_path, 'r') as f:
                    pid_str = f.read().strip()
                
                if pid_str:
                    old_pid = int(pid_str)
                    # Check if process is still running
                    os.kill(old_pid, 0)
                    # Process exists - overlap not allowed
                    overlap_error = f"Job {job_name} already running (PID {old_pid}), overlap not allowed"
            except (ValueError, OSError):
                # PID invalid or process doesn't exist - write our PID
                pass
        
        # Write our PID if no conflict (create new or overwrite stale)
        if not overlap_error:
            try:
                with open(lockfile_path, 'w') as f:
                    f.write(f"{os.getpid()}\n")
                lockfile_acquired = True
            except Exception as e:
                # Failed to write - report error
                overlap_error = f"Failed to write lockfile {lockfile_path}: {e}"
        
        # If overlap detected, replace command with error message
        if overlap_error:
            command = f"echo 'LOCKFILE ERROR: {overlap_error}' && exit 254"
    
    # Run the command asynchronously
    asyncio.run(run_command_and_stream(websocket_url, job_name, job_instance, machine_id, command, cwd, user, timeout, loglevel, logdir, lockfile_path, lockfile_acquired))

if __name__ == "__main__":
    main()
