#!/usr/bin/env python3
"""
WebSocket server for receiving job updates from wrapper scripts
"""

import asyncio
import websockets
import json
import time
import copy
from datetime import datetime, timezone
import multiprocessing

class WebSocketJobServer:
    def __init__(self, host='0.0.0.0', port=8889, state=None, running=None, statelocks=None, log_func=None, commands=None):
        self.host = host
        self.port = port
        self.state = state
        self.running = running
        self.statelocks = statelocks
        self.log_func = log_func
        self.commands = commands  # Shared command queue from main
        self.connections = {}  # Track active connections by job_instance + machine
        self.command_check_task = None  # Background task for checking kill commands
        self.kill_machine_timeouts = {}  # Track machine kills with grace period: {(job_name, machine): timestamp}
    
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
                        print(f"[MACHINES WS][{job_instance}][{machine}] Client connected", flush=True)
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
                            
                        # Update state
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                job_state = self.state[job_name]
                                if 'results' not in job_state:
                                    job_state['results'] = {}
                                if machine not in job_state['results']:
                                    job_state['results'][machine] = {}
                                job_state['results'][machine]['starttime'] = timestamp
                                job_state['results'][machine]['ret'] = ''
                                job_state['results'][machine]['retcode'] = ''
                                job_state['results'][machine]['endtime'] = ''
                                job_state['results'][machine]['wrapper_version'] = data.get('version', 'unknown')
                                self.state[job_name] = job_state
                        
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
                        
                        # Update state with last heartbeat time so monitoring can detect timeouts
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                job_state = self.state[job_name]
                                if 'results' not in job_state:
                                    job_state['results'] = {}
                                if machine not in job_state['results']:
                                    job_state['results'][machine] = {}
                                job_state['results'][machine]['last_heartbeat'] = timestamp
                                self.state[job_name] = job_state
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Heartbeat at {timestamp}", flush=True)
                        
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
                                    # Duplicate message - already processed
                                    print(f"[MACHINES WS][{job_instance}][{machine}] Duplicate output seq {seq} (expected {expected_seq})", flush=True)
                                    # Send ack anyway
                                    await websocket.send(json.dumps({
                                        'type': 'ack',
                                        'ack_type': 'output',
                                        'seq': seq,
                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                    }))
                                    continue
                                    
                                elif seq > expected_seq:
                                    # Out of order - request resend
                                    print(f"[MACHINES WS][{job_instance}][{machine}] Out of order output seq {seq} (expected {expected_seq})", flush=True)
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
                                print(f"[MACHINES WS][{job_instance}][{machine}] Failed to send output ACK: {e}", flush=True)
                        
                        # Update state with accumulated output
                        # Validate job_instance is in running dict (started by main.py)
                        if job_instance not in self.running:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Unknown job instance for output", flush=True)
                            continue
                        
                        # Validate machine is in the expected machines list for this instance
                        if 'machines' in self.running[job_instance] and machine not in self.running[job_instance]['machines']:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Not in expected machines list for output", flush=True)
                            continue
                        
                        if job_name in self.state:
                            if self.statelocks and job_name in self.statelocks:
                                with self.statelocks[job_name]:
                                    job_state = self.state[job_name]
                                    if 'results' not in job_state:
                                        job_state['results'] = {}
                                    if machine not in job_state['results']:
                                        job_state['results'][machine] = {'ret': '', 'retcode': '', 'starttime': timestamp, 'endtime': ''}
                                    
                                    # Append output to existing output
                                    current_output = job_state['results'][machine].get('ret', '')
                                    job_state['results'][machine]['ret'] = current_output + output_data
                                    # Store last sequence for recovery
                                    if seq is not None:
                                        job_state['results'][machine]['last_output_seq'] = seq
                                    self.state[job_name] = job_state
                    
                    elif msg_type == 'sync_request':
                        # Client requests sync - tell them what we last received
                        client_last_acked = data.get('last_acked_seq', -1)
                        client_next_seq = data.get('next_seq', 0)
                        
                        server_last_seq = -1
                        if client_id in self.connections:
                            server_last_seq = self.connections[client_id].get('last_acked_seq', -1)
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Sync request: client_acked={client_last_acked}, client_next={client_next_seq}, server_last={server_last_seq}", flush=True)
                        
                        sync_response = {
                            'type': 'sync_response',
                            'last_seq': server_last_seq,
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        
                        await websocket.send(json.dumps(sync_response))
                        
                    elif msg_type == 'complete':
                        retcode = data.get('retcode', -1)
                        seq = data.get('seq', None)
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Received completion: retcode={retcode}, seq={seq}", flush=True)
                        print(f"[MACHINES WS][{job_instance}][{machine}] Validation: in_running={job_instance in self.running}, in_state={job_name in self.state}", flush=True)
                        
                        # Log if job instance not in running dict (may have timed out or been killed)
                        # but continue to process completion anyway - we want to record actual result
                        if job_instance not in self.running:
                            print(f"[MACHINES WS][{job_instance}][{machine}] NOTE - Job instance not in running dict (may have timed out), but recording completion anyway", flush=True)
                        
                        # Get group info from running dict or state
                        group = 'unknown'
                        if job_instance in self.running:
                            # Try to get group from state
                            if job_name in self.state:
                                group = self.state[job_name].get('group', 'unknown')
                        elif job_name in self.state:
                            # Job not in running, but get group from state
                            group = self.state[job_name].get('group', 'unknown')
                        
                        # Update state with final result
                        print(f"[MACHINES WS][{job_instance}][{machine}] State checks: in_state={job_name in self.state}, has_locks={self.statelocks is not None and job_name in self.statelocks}", flush=True)
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            print(f"[MACHINES WS][{job_instance}][{machine}] Acquiring state lock", flush=True)
                            with self.statelocks[job_name]:
                                # Optimized: Only modify the specific machine's result, not copy entire job state
                                # This avoids expensive deepcopy of all machines' accumulated output
                                job_state = self.state[job_name]
                                
                                # Ensure results dict exists
                                if 'results' not in job_state:
                                    job_state['results'] = {}
                                
                                # Get existing data if we have it (output was accumulated during 'output' messages)
                                starttime = timestamp
                                output = ''
                                wrapper_version = None
                                last_heartbeat = None
                                if machine in job_state['results']:
                                    starttime = job_state['results'][machine].get('starttime', timestamp)
                                    output = job_state['results'][machine].get('ret', '')
                                    wrapper_version = job_state['results'][machine].get('wrapper_version')
                                    last_heartbeat = job_state['results'][machine].get('last_heartbeat')
                                
                                print(f"[MACHINES WS][{job_instance}][{machine}] Setting endtime={timestamp}", flush=True)
                                # Update with final status, preserving wrapper_version and last_heartbeat
                                job_state['results'][machine] = {
                                    'ret': output,
                                    'retcode': retcode,
                                    'starttime': starttime,
                                    'endtime': timestamp
                                }
                                if wrapper_version:
                                    job_state['results'][machine]['wrapper_version'] = wrapper_version
                                if last_heartbeat:
                                    job_state['results'][machine]['last_heartbeat'] = last_heartbeat
                                
                                # Trigger Manager dict update by reassigning
                                self.state[job_name] = job_state
                                print(f"[MACHINES WS][{job_instance}][{machine}] State updated: endtime={timestamp}, retcode={retcode}", flush=True)
                        else:
                            print(f"[MACHINES WS][{job_instance}][{machine}] WARNING - Cannot update state (checks failed)", flush=True)
                        
                        # Send acknowledgement AFTER state is updated - wrapper can close immediately after receiving ACK
                        ack_msg = {
                            'type': 'ack',
                            'ack_type': 'complete',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        if seq is not None:
                            ack_msg['seq'] = seq
                        
                        try:
                            await websocket.send(json.dumps(ack_msg))
                            print(f"[MACHINES WS][{job_instance}][{machine}] Completion ACK sent", flush=True)
                        except Exception as e:
                            # Connection may be closing - but state is already updated, so this is OK
                            print(f"[MACHINES WS][{job_instance}][{machine}] Failed to send completion ACK (state already updated): {e}", flush=True)
                        
                        print(f"[MACHINES WS][{job_instance}][{machine}] Completed (exit code: {retcode})", flush=True)
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                        
                    elif msg_type == 'killed':
                        # Wrapper acknowledges it was killed
                        print(f"[MACHINES WS][{job_instance}][{machine}] Acknowledged kill signal", flush=True)
                        
                    elif msg_type == 'error':
                        error_msg = data.get('error', 'Unknown error')
                        print(f"[MACHINES WS][{job_instance}][{machine}] Error: {error_msg}", flush=True)
                        
                        # Update state with error - processresults_websocket will log it
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                job_state = self.state[job_name]
                                if 'results' not in job_state:
                                    job_state['results'] = {}
                                
                                job_state['results'][machine] = {
                                    'ret': f"Wrapper error: {error_msg}",
                                    'retcode': 255,
                                    'starttime': timestamp,
                                    'endtime': timestamp
                                }
                                self.state[job_name] = job_state
                        
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
                                    print(f"[MACHINES WS] Setting stop_signal for {proc_name}", flush=True)
                                    tmprunning = dict(proc_info)
                                    tmprunning['stop_signal'] = True
                                    self.running[proc_name] = tmprunning
                            
                            # Convert to individual killmachine commands for all machines in the job
                            machines_to_kill = set()
                            
                            # Get machines from active connections
                            for client_id, conn_info in list(self.connections.items()):
                                if conn_info['job_name'] == job_name:
                                    machines_to_kill.add(conn_info['machine'])
                            
                            # Get machines from state that are still running (no endtime)
                            if job_name in self.state and 'results' in self.state[job_name]:
                                for machine, result in self.state[job_name]['results'].items():
                                    # Only kill if not yet completed
                                    if not result.get('endtime') or result.get('endtime') == '':
                                        machines_to_kill.add(machine)
                            
                            # Create killmachine command for each machine
                            for machine in machines_to_kill:
                                self.commands.append({
                                    'killmachine': {'cron': job_name, 'machine': machine}
                                })
                            
                            print(f"[MACHINES WS] Created {len(machines_to_kill)} killmachine commands for job {job_name}", flush=True)
                            
                            # Remove the killcron command (replaced by killmachine commands)
                            self.commands.remove(cmd)
                        
                        elif 'killmachine' in cmd:
                            kill_info = cmd['killmachine']
                            job_name = kill_info.get('cron')
                            machine_name = kill_info.get('machine')
                            
                            if not job_name or not machine_name:
                                print(f"[MACHINES WS] Invalid killmachine command - missing cron or machine: {kill_info}", flush=True)
                                self.commands.remove(cmd)
                                continue
                            
                            # Track kill time for retry and grace period (only if not already tracked)
                            if (job_name, machine_name) not in self.kill_machine_timeouts:
                                self.kill_machine_timeouts[(job_name, machine_name)] = time.time()
                                print(f"[MACHINES WS] Kill command received for {job_name} on {machine_name}", flush=True)
                
                # Unified retry logic for all machine kills
                current_time = time.time()
                for (job_name, machine_name), kill_time in list(self.kill_machine_timeouts.items()):
                    time_elapsed = current_time - kill_time
                    
                    # Check if machine has completed
                    machine_completed = False
                    if job_name in self.state and 'results' in self.state[job_name]:
                        result = self.state[job_name]['results'].get(machine_name, {})
                        if result.get('endtime') and result.get('endtime') != '':
                            machine_completed = True
                    
                    if machine_completed:
                        # Machine completed, remove from tracking and command queue
                        for cmd in list(self.commands):
                            if 'killmachine' in cmd:
                                kill_info = cmd['killmachine']
                                if kill_info.get('cron') == job_name and kill_info.get('machine') == machine_name:
                                    self.commands.remove(cmd)
                                    break
                        del self.kill_machine_timeouts[(job_name, machine_name)]
                        continue
                    
                    # Keep retrying to send kill until grace period ends
                    if time_elapsed < 30:
                        kill_sent = False
                        for client_id, conn_info in list(self.connections.items()):
                            if conn_info['job_name'] == job_name and conn_info['machine'] == machine_name:
                                try:
                                    await conn_info['websocket'].send(json.dumps({
                                        'type': 'kill',
                                        'job_name': job_name,
                                        'job_instance': conn_info['job_instance'],
                                        'machine': machine_name,
                                        'timestamp': datetime.now(timezone.utc).isoformat()
                                    }))
                                    kill_sent = True
                                    # Only log every few seconds to avoid spam
                                    if int(time_elapsed) % 5 == 0:
                                        print(f"[MACHINES WS] Kill message sent to {client_id} (elapsed: {time_elapsed:.1f}s)", flush=True)
                                except Exception as e:
                                    if int(time_elapsed) % 5 == 0:
                                        print(f"[MACHINES WS] Failed to send kill to {client_id}: {e}", flush=True)
                                break
                        
                        if not kill_sent and int(time_elapsed) % 5 == 0:
                            print(f"[MACHINES WS] No active connection found for {job_name}:{machine_name} (elapsed: {time_elapsed:.1f}s)", flush=True)
                    
                    # Grace period expired
                    elif time_elapsed >= 30:
                        print(f"[MACHINES WS] Kill grace period expired for {job_name} on {machine_name}, forcefully completing", flush=True)
                        
                        # Forcefully mark machine as killed
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                job_state = self.state[job_name]
                                if 'results' in job_state and machine_name in job_state['results']:
                                    result = job_state['results'][machine_name]
                                    if not result.get('endtime') or result.get('endtime') == '':
                                        result['endtime'] = datetime.now(timezone.utc)
                                        result['retcode'] = 143
                                        if 'ret' not in result:
                                            result['ret'] = ''
                                        result['ret'] += "\n[Job terminated by user request - grace period expired after 30s]\n"
                                self.state[job_name] = job_state
                        
                        # Remove from tracking and command queue
                        for cmd in list(self.commands):
                            if 'killmachine' in cmd:
                                kill_info = cmd['killmachine']
                                if kill_info.get('cron') == job_name and kill_info.get('machine') == machine_name:
                                    self.commands.remove(cmd)
                                    break
                        del self.kill_machine_timeouts[(job_name, machine_name)]
                
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
                print(f"[MACHINES WS] Command checker started", flush=True)
            
            await asyncio.Future()  # Run forever
    
    def run(self):
        """Run the WebSocket server (blocking)"""
        asyncio.run(self.start_server())

def start_websocket_server(host, port, state, running, statelocks, log_func, commands=None):
    """Start WebSocket server in a separate process"""
    server = WebSocketJobServer(host=host, port=port, state=state, running=running, 
                                statelocks=statelocks, log_func=log_func, commands=commands)
    server.run()
