#!/usr/bin/env python3
"""
WebSocket server for receiving job updates from wrapper scripts
"""

import asyncio
import websockets
import json
import time
from datetime import datetime, timezone
import multiprocessing

class WebSocketJobServer:
    def __init__(self, host='0.0.0.0', port=8889, state=None, running=None, log_func=None, commands=None, state_update_queues=None, debug_flag=None):
        self.host = host
        self.port = port
        self.state = state
        self.running = running
        self.log_func = log_func
        self.commands = commands  # Shared command queue from main
        self.state_update_queues = state_update_queues  # job_instance → Queue for state updates
        self.connections = {}  # Track active connections by job_instance + machine
        self.command_check_task = None  # Background task for checking kill commands
        self.kill_machine_timeouts = {}  # Track machine kills with grace period: {(job_name, machine): timestamp}
        self.debug_flag = debug_flag  # Shared debug flag from Manager
    
    def debug_print(self, message, flush=True):
        """Print debug message only if debug mode is enabled"""
        if self.debug_flag and self.debug_flag.value:
            print(message, flush=flush)
    
    def send_state_update(self, job_instance, update_msg):
        """Send state update to job process via queue (non-blocking)"""
        if self.state_update_queues and job_instance in self.state_update_queues:
            try:
                self.state_update_queues[job_instance].put_nowait(update_msg)
                return True
            except Exception as e:
                print(f"[MACHINES WS][{job_instance}] Failed to queue state update: {e}", flush=True)
                return False
        else:
            # Queue doesn't exist yet - race condition during startup
            msg_type = update_msg.get('type', 'unknown')
            machine = update_msg.get('machine', 'unknown')
            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Queue not ready, dropping {msg_type} message", flush=True)
            return False
    
    async def handle_client(self, websocket):
        """
        Handle incoming WebSocket connections
        
        Note: In websockets 10.0+, the path argument was removed.
        This handler works with both old and new versions.
        """
        client_id = None
        remote_address = None
        try:
            # Get remote address for debugging
            try:
                remote_address = websocket.remote_address if hasattr(websocket, 'remote_address') else 'unknown'
            except:
                remote_address = 'unknown'
                
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')
                    job_name = data.get('job_name')
                    job_instance = data.get('job_instance')
                    machine = data.get('machine')
                    timestamp_str = data.get('timestamp')
                    
                    if timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str)
                    else:
                        timestamp = datetime.now(timezone.utc)
                    
                    client_id = f"{job_instance}:{machine}"
                    
                    if msg_type == 'connect':
                        self.connections[client_id] = {
                            'websocket': websocket,
                            'job_name': job_name,
                            'job_instance': job_instance,
                            'machine': machine,
                            'last_seen': timestamp,
                            'output_buffer': [],
                            'next_expected_seq': 0,  # Next sequence number we expect
                            'last_acked_seq': -1,    # Last sequence we acknowledged
                            'pending_acks': []       # Sequences pending acknowledgement
                        }
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Client connected", flush=True)
                        # Send connection acknowledgement
                        await websocket.send(json.dumps({
                            'type': 'ack',
                            'ack_type': 'connect',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }))
                        
                    elif msg_type == 'start':
                        if client_id in self.connections:
                            self.connections[client_id]['pid'] = data.get('pid')
                            self.connections[client_id]['started'] = timestamp
                            self.connections[client_id]['version'] = data.get('version')
                        
                        # Validate that this job instance is running
                        if job_instance not in self.running:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Unknown job instance (not in running)", flush=True)
                            continue
                        
                        # Validate machine is in the expected machines list for this instance
                        if 'machines' in self.running[job_instance] and machine not in self.running[job_instance]['machines']:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Not in expected machines list", flush=True)
                            continue
                            
                        # Send state update to job process via queue
                        self.send_state_update(job_instance, {
                            'type': 'start',
                            'machine': machine,
                            'timestamp': timestamp,
                            'wrapper_version': data.get('version', 'unknown')
                        })
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Started (PID: {data.get('pid')}, Version: {data.get('version', 'unknown')})", flush=True)
                        
                        # Send acknowledgement for start message
                        await websocket.send(json.dumps({
                            'type': 'ack',
                            'ack_type': 'start',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }))
                        
                    elif msg_type == 'heartbeat':
                        if client_id in self.connections:
                            self.connections[client_id]['last_seen'] = timestamp
                        
                        # Send state update to job process via queue
                        self.send_state_update(job_instance, {
                            'type': 'heartbeat',
                            'machine': machine,
                            'timestamp': timestamp
                        })
                        
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Heartbeat at {timestamp}", flush=True)
                        
                    elif msg_type == 'output':
                        stream = data.get('stream', 'stdout')
                        output_data = data.get('data', '')
                        seq = data.get('seq', None)  # Sequence number
                        
                        # Validate that this job instance is running
                        if job_instance not in self.running:
                            continue
                        
                        if client_id in self.connections:
                            conn = self.connections[client_id]
                            
                            # Check sequence number if provided
                            if seq is not None:
                                expected_seq = conn['next_expected_seq']
                                
                                if seq < expected_seq:
                                    # Duplicate message - already processed, ACK and skip
                                    self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Duplicate output seq {seq} (expected {expected_seq})", flush=True)
                                    await websocket.send(json.dumps({
                                        'type': 'ack',
                                        'ack_type': 'output',
                                        'seq': seq,
                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                    }))
                                    continue  # Skip all processing for duplicates
                                    
                                elif seq > expected_seq:
                                    # Out of order - request resend
                                    self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Out of order output seq {seq} (expected {expected_seq})", flush=True)
                                    await websocket.send(json.dumps({
                                        'type': 'nack',
                                        'nack_type': 'out_of_order',
                                        'expected_seq': expected_seq,
                                        'received_seq': seq,
                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                    }))
                                    continue
                                
                                # Correct sequence - process it
                                conn['next_expected_seq'] = seq + 1
                            
                            conn['output_buffer'].append(output_data)
                            conn['last_seen'] = timestamp
                            
                            # Send acknowledgement IMMEDIATELY before state updates
                            ack_msg = {
                                'type': 'ack',
                                'ack_type': 'output',
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            if seq is not None:
                                ack_msg['seq'] = seq
                                conn['last_acked_seq'] = seq
                            
                            # Send ACK first to minimize latency under load
                            try:
                                await websocket.send(json.dumps(ack_msg))
                            except Exception as e:
                                self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Failed to send output ACK: {e}", flush=True)
                        
                        # Send output to job process via queue
                        # Validate job_instance is in running dict (started by main.py)
                        if job_instance not in self.running:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Unknown job instance for output", flush=True)
                            continue
                        
                        # Validate machine is in the expected machines list for this instance
                        if 'machines' in self.running[job_instance] and machine not in self.running[job_instance]['machines']:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Not in expected machines list for output", flush=True)
                            continue
                        
                        # Send state update to job process via queue
                        self.send_state_update(job_instance, {
                            'type': 'output',
                            'machine': machine,
                            'data': output_data,
                            'seq': seq,
                            'timestamp': timestamp
                        })
                    
                    elif msg_type == 'sync_request':
                        # Client requests sync - tell them what we last received
                        client_last_acked = data.get('last_acked_seq', -1)
                        client_next_seq = data.get('next_seq', 0)
                        
                        server_last_seq = -1
                        if client_id in self.connections:
                            server_last_seq = self.connections[client_id].get('last_acked_seq', -1)
                        
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Sync request: client_acked={client_last_acked}, client_next={client_next_seq}, server_last={server_last_seq}", flush=True)
                        
                        sync_response = {
                            'type': 'sync_response',
                            'last_seq': server_last_seq,
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        
                        await websocket.send(json.dumps(sync_response))
                        
                    elif msg_type == 'complete':
                        retcode = data.get('retcode', -1)
                        seq = data.get('seq', None)
                        
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Received completion: retcode={retcode}, seq={seq}", flush=True)
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Validation: in_running={job_instance in self.running}, in_state={job_name in self.state}", flush=True)
                        
                        # Log if job instance not in running dict (may have timed out or been killed)
                        # but continue to process completion anyway - we want to record actual result
                        if job_instance not in self.running:
                            print(f"[MACHINES WS][{job_instance}][{machine}] NOTE - Job instance not in running dict (may have timed out), but recording completion anyway", flush=True)
                        
                        # Send completion to job process via queue
                        queue_ok = self.send_state_update(job_instance, {
                            'type': 'complete',
                            'machine': machine,
                            'retcode': retcode,
                            'timestamp': timestamp,
                            'seq': seq
                        })
                        
                        # If queue doesn't exist, this is an orphaned job - tell wrapper to stop
                        if not queue_ok:
                            print(f"[MACHINES WS][{job_instance}][{machine}] Job queue missing (job stopped/restarted), sending error to wrapper", flush=True)
                            error_msg = {
                                'type': 'error',
                                'code': 'JOB_NOT_FOUND',
                                'message': 'Job no longer exists on server (stopped or restarted)'
                            }
                            await websocket.send(json.dumps(error_msg))
                            break
                        
                        # Wait for job process to update state before ACK (with timeout)
                        state_updated = False
                        attempt = 0
                        max_attempts = 600  # 30 seconds max (600 * 50ms)
                        while not state_updated and attempt < max_attempts:
                            await asyncio.sleep(0.05)  # 50ms
                            attempt += 1
                            
                            # Check if state was updated
                            if job_name in self.state and 'results' in self.state[job_name]:
                                result = self.state[job_name]['results'].get(machine, {})
                                if result.get('endtime') and result.get('endtime') != '':
                                    state_updated = True
                                    self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] State update confirmed after {attempt*50}ms", flush=True)
                                    break
                            
                            # Log warning every 5 seconds
                            if attempt % 100 == 0:
                                print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Still waiting for state update after {attempt*50}ms", flush=True)
                        
                        if not state_updated:
                            print(f"[MACHINES WS][{job_instance}][{machine}] ERROR - State update timeout after 30s, closing connection", flush=True)
                            break
                        
                        # Send acknowledgement after state is confirmed updated
                        ack_msg = {
                            'type': 'ack',
                            'ack_type': 'complete',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        if seq is not None:
                            ack_msg['seq'] = seq
                        
                        try:
                            await websocket.send(json.dumps(ack_msg))
                            self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Completion ACK sent", flush=True)
                        except Exception as e:
                            # Connection may be closing - but state is already updated, so this is OK
                            self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Failed to send completion ACK (state already updated): {e}", flush=True)
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Completed (exit code: {retcode})", flush=True)
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                        
                    elif msg_type == 'killed':
                        # Wrapper acknowledges it was killed
                        self.debug_print(f"[MACHINES WS][{job_instance}][{machine}] Acknowledged kill signal", flush=True)
                        
                    elif msg_type == 'error':
                        error_msg = data.get('error', 'Unknown error')
                        print(f"[MACHINES WS][{job_instance}][{machine}] Error: {error_msg}", flush=True)
                        
                        # Send error to job process via queue
                        self.send_state_update(job_instance, {
                            'type': 'error',
                            'machine': machine,
                            'error': error_msg,
                            'timestamp': timestamp
                        })
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                    
                except json.JSONDecodeError as e:
                    if client_id:
                        # Extract job_instance and machine from client_id
                        parts = client_id.split(':', 1)
                        if len(parts) == 2:
                            print(f"[MACHINES WS][{parts[0]}][{parts[1]}] Invalid JSON received: {e}", flush=True)
                        else:
                            print(f"[MACHINES WS][{client_id}] Invalid JSON received: {e}", flush=True)
                    else:
                        print(f"[MACHINES WS] Invalid JSON received: {e}", flush=True)
                except Exception as e:
                    if client_id:
                        parts = client_id.split(':', 1)
                        if len(parts) == 2:
                            print(f"[MACHINES WS][{parts[0]}][{parts[1]}] Error processing message: {e}", flush=True)
                        else:
                            print(f"[MACHINES WS][{client_id}] Error processing message: {e}", flush=True)
                    else:
                        print(f"[MACHINES WS] Error processing message: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    
        except websockets.exceptions.ConnectionClosed:
            if client_id:
                parts = client_id.split(':', 1)
                if len(parts) == 2:
                    print(f"[MACHINES WS][{parts[0]}][{parts[1]}] Connection closed", flush=True)
                else:
                    print(f"[MACHINES WS][{client_id}] Connection closed", flush=True)
            else:
                print(f"[MACHINES WS] Connection closed before identification (from {remote_address})", flush=True)
        except Exception as e:
            if client_id:
                parts = client_id.split(':', 1)
                if len(parts) == 2:
                    print(f"[MACHINES WS][{parts[0]}][{parts[1]}] Error in client handler: {e}", flush=True)
                else:
                    print(f"[MACHINES WS][{client_id}] Error in client handler: {e}", flush=True)
            else:
                print(f"[MACHINES WS] Error in client handler: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            # Clean up connection on disconnect
            if client_id and client_id in self.connections:
                parts = client_id.split(':', 1)
                if len(parts) == 2:
                    print(f"[MACHINES WS][{parts[0]}][{parts[1]}] Cleaning up connection", flush=True)
                else:
                    print(f"[MACHINES WS][{client_id}] Cleaning up connection", flush=True)
                del self.connections[client_id]
    
    async def check_commands(self):
        """Background task to check for kill commands and send them to wrappers"""
        while True:
            try:
                if self.commands:
                    # Check for kill commands
                    for cmd in list(self.commands):
                        if 'killcron' in cmd:
                            job_name = cmd['killcron']
                            print(f"[MACHINES WS] Kill command received for job {job_name}", flush=True)
                            
                            # Set stop_signal in running dict to prevent further batches/processing
                            for proc_name, proc_info in list(self.running.items()):
                                if proc_info.get('name') == job_name:
                                    self.debug_print(f"[MACHINES WS] Setting stop_signal for {proc_name}", flush=True)
                                    tmprunning = dict(proc_info)
                                    tmprunning['stop_signal'] = True
                                    self.running[proc_name] = tmprunning
                            
                            # Convert to individual killmachine commands for all machines in the job
                            machines_to_kill = {}  # machine -> job_instance mapping
                            
                            # Get machines from active connections
                            for client_id, conn_info in list(self.connections.items()):
                                if conn_info['job_name'] == job_name:
                                    machines_to_kill[conn_info['machine']] = conn_info['job_instance']
                            
                            # Get machines from state that are still running (no endtime)
                            if job_name in self.state and 'results' in self.state[job_name]:
                                for machine, result in self.state[job_name]['results'].items():
                                    # Only kill if not yet completed
                                    if not result.get('endtime') or result.get('endtime') == '':
                                        # Try to get job_instance from connections, or use job_name as fallback
                                        if machine not in machines_to_kill:
                                            job_inst = None
                                            for client_id, conn_info in self.connections.items():
                                                if conn_info['job_name'] == job_name and conn_info['machine'] == machine:
                                                    job_inst = conn_info['job_instance']
                                                    break
                                            if job_inst:
                                                machines_to_kill[machine] = job_inst
                            
                            # Create killmachine command for each machine
                            for machine, job_inst in machines_to_kill.items():
                                self.commands.append({
                                    'killmachine': {'job_instance': job_inst, 'machine': machine, 'cron': job_name}
                                })
                            
                            self.debug_print(f"[MACHINES WS] Created {len(machines_to_kill)} killmachine commands for job {job_name}", flush=True)
                            
                            # Remove the killcron command (replaced by killmachine commands)
                            self.commands.remove(cmd)
                        
                        elif 'killmachine' in cmd:
                            kill_info = cmd['killmachine']
                            job_instance = kill_info.get('job_instance')
                            machine_name = kill_info.get('machine')
                            job_name = kill_info.get('cron')
                            
                            if not job_instance or not machine_name:
                                print(f"[MACHINES WS] Invalid killmachine command - missing job_instance or machine: {kill_info}", flush=True)
                                self.commands.remove(cmd)
                                continue
                            
                            # Track kill time for retry and grace period (only if not already tracked)
                            if (job_instance, machine_name) not in self.kill_machine_timeouts:
                                self.kill_machine_timeouts[(job_instance, machine_name)] = {
                                    'start_time': time.time(),
                                    'job_name': job_name
                                }
                                print(f"[{job_instance}][{machine_name}] Kill command received", flush=True)
                
                # Unified retry logic for all machine kills
                current_time = time.time()
                for (job_instance, machine_name), kill_info in list(self.kill_machine_timeouts.items()):
                    start_time = kill_info['start_time']
                    job_name = kill_info.get('job_name')
                    time_elapsed = current_time - start_time
                    
                    # Check if machine has completed
                    machine_completed = False
                    if job_name and job_name in self.state and 'results' in self.state[job_name]:
                        result = self.state[job_name]['results'].get(machine_name, {})
                        if result.get('endtime') and result.get('endtime') != '':
                            machine_completed = True
                    
                    if machine_completed:
                        # Machine completed, remove from tracking and command queue
                        for cmd in list(self.commands):
                            if 'killmachine' in cmd:
                                kill_info = cmd['killmachine']
                                if kill_info.get('job_instance') == job_instance and kill_info.get('machine') == machine_name:
                                    self.commands.remove(cmd)
                                    break
                        del self.kill_machine_timeouts[(job_instance, machine_name)]
                        continue
                    
                    # Keep retrying to send kill until grace period ends
                    if time_elapsed < 30:
                        kill_sent = False
                        client_id = f"{job_instance}:{machine_name}"
                        
                        if client_id in self.connections:
                            conn_info = self.connections[client_id]
                            try:
                                await conn_info['websocket'].send(json.dumps({
                                    'type': 'kill',
                                    'job_name': job_name,
                                    'job_instance': job_instance,
                                    'machine': machine_name,
                                    'timestamp': datetime.now(timezone.utc).isoformat()
                                }))
                                kill_sent = True
                                # Only log every few seconds to avoid spam
                                if int(time_elapsed) % 5 == 0:
                                    self.debug_print(f"[{job_instance}][{machine_name}] Kill message sent (elapsed: {time_elapsed:.1f}s)", flush=True)
                            except Exception as e:
                                if int(time_elapsed) % 5 == 0:
                                    self.debug_print(f"[{job_instance}][{machine_name}] Failed to send kill: {e}", flush=True)
                        
                        if not kill_sent and int(time_elapsed) % 5 == 0:
                            self.debug_print(f"[{job_instance}][{machine_name}] No active connection found (elapsed: {time_elapsed:.1f}s)", flush=True)
                    
                    # Grace period expired
                    elif time_elapsed >= 30:
                        print(f"[{job_instance}][{machine_name}] Kill grace period expired, forcefully completing", flush=True)
                        
                        # Queue force kill message for single-writer to handle (use stored job_instance)
                        if job_instance and self.send_state_update(job_instance, {
                            'type': 'force_kill',
                            'machine': machine_name,
                            'job_name': job_name
                        }):
                            self.debug_print(f"[{job_instance}][{machine_name}] Queued force kill after 30s grace period", flush=True)
                        else:
                            print(f"[MACHINES WS] Cannot queue force kill for {machine_name} - no job_instance stored for kill command", flush=True)
                        
                        # Remove from tracking and command queue
                        for cmd in list(self.commands):
                            if 'killmachine' in cmd:
                                kill_info = cmd['killmachine']
                                if kill_info.get('job_instance') == job_instance and kill_info.get('machine') == machine_name:
                                    self.commands.remove(cmd)
                                    break
                        del self.kill_machine_timeouts[(job_instance, machine_name)]
                
                await asyncio.sleep(0.5)  # Check every 500ms
            except Exception as e:
                print(f"[MACHINES WS] Error in command checker: {e}", flush=True)
                await asyncio.sleep(1)
    
    async def start_server(self):
        """Start the WebSocket server"""
        # Configure for high-throughput scenarios
        # max_size=None: unlimited message size for large outputs
        # max_queue=1024: allow more queued messages during high load
        # write_limit=2**20: 1MB write buffer (default is 64KB)
        async with websockets.serve(
            self.handle_client, 
            self.host, 
            self.port,
            max_size=None,
            max_queue=1024,
            write_limit=2**20
        ):
            print(f"[MACHINES WS] Server started on ws://{self.host}:{self.port}", flush=True)
            
            # Start command checking task if we have a command queue
            if self.commands is not None:
                self.command_check_task = asyncio.create_task(self.check_commands())
                self.debug_print(f"[MACHINES WS] Command checker started", flush=True)
            
            await asyncio.Future()  # Run forever
    
    def run(self):
        """Run the WebSocket server (blocking)"""
        asyncio.run(self.start_server())

def start_websocket_server(host, port, state, running, log_func, commands=None, state_update_queues=None, debug_flag=None):
    """Start WebSocket server in a separate process"""
    server = WebSocketJobServer(host=host, port=port, state=state, running=running, 
                                log_func=log_func, commands=commands, 
                                state_update_queues=state_update_queues, debug_flag=debug_flag)
    server.run()
