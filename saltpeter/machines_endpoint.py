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
        self.kill_timeouts = {}  # Track kill commands with grace period: {job_name: timestamp}
        
    async def handle_client(self, websocket):
        """
        Handle incoming WebSocket connections
        
        Note: In websockets 10.0+, the path argument was removed.
        This handler works with both old and new versions.
        """
        client_id = None
        try:
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
                        print(f"WebSocket: Client connected - {client_id}", flush=True)
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
                            print(f"WebSocket: WARNING - Received start for unknown job instance {job_instance}", flush=True)
                            continue
                        
                        # Validate machine is in the expected machines list for this instance
                        if 'machines' in self.running[job_instance] and machine not in self.running[job_instance]['machines']:
                            print(f"WebSocket: WARNING - Machine {machine} not in expected list for {job_instance}", flush=True)
                            continue
                            
                        # Update state
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                tmpstate = self.state[job_name].copy()
                                if 'results' not in tmpstate:
                                    tmpstate['results'] = {}
                                if machine not in tmpstate['results']:
                                    tmpstate['results'][machine] = {}
                                tmpstate['results'][machine]['starttime'] = timestamp
                                tmpstate['results'][machine]['ret'] = ''
                                tmpstate['results'][machine]['retcode'] = ''
                                tmpstate['results'][machine]['endtime'] = ''
                                tmpstate['results'][machine]['wrapper_version'] = data.get('version', 'unknown')
                                self.state[job_name] = tmpstate
                        
                        print(f"WebSocket: Job started - {client_id} (PID: {data.get('pid')}, Version: {data.get('version', 'unknown')})", flush=True)
                        
                    elif msg_type == 'heartbeat':
                        if client_id in self.connections:
                            self.connections[client_id]['last_seen'] = timestamp
                        
                        # Update state with last heartbeat time so monitoring can detect timeouts
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                tmpstate = self.state[job_name].copy()
                                if 'results' not in tmpstate:
                                    tmpstate['results'] = {}
                                if machine not in tmpstate['results']:
                                    tmpstate['results'][machine] = {}
                                tmpstate['results'][machine]['last_heartbeat'] = timestamp
                                self.state[job_name] = tmpstate
                        
                        print(f"WebSocket: Heartbeat from {client_id} at {timestamp}", flush=True)
                        
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
                                    print(f"WebSocket: Duplicate output seq {seq} from {client_id} (expected {expected_seq})", flush=True)
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
                                    print(f"WebSocket: Out of order output seq {seq} from {client_id} (expected {expected_seq})", flush=True)
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
                            
                            # Send acknowledgement
                            ack_msg = {
                                'type': 'ack',
                                'ack_type': 'output',
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            if seq is not None:
                                ack_msg['seq'] = seq
                                conn['last_acked_seq'] = seq
                            
                            await websocket.send(json.dumps(ack_msg))
                        
                        # Update state with accumulated output
                        # Validate job_instance is in running dict (started by main.py)
                        if job_instance not in self.running:
                            print(f"WebSocket: WARNING - Received output for unknown job instance {job_instance}", flush=True)
                            continue
                        
                        # Validate machine is in the expected machines list for this instance
                        if 'machines' in self.running[job_instance] and machine not in self.running[job_instance]['machines']:
                            print(f"WebSocket: WARNING - Machine {machine} not in expected list for {job_instance}", flush=True)
                            continue
                        
                        if job_name in self.state:
                            if self.statelocks and job_name in self.statelocks:
                                with self.statelocks[job_name]:
                                    tmpstate = self.state[job_name].copy()
                                    if 'results' not in tmpstate:
                                        tmpstate['results'] = {}
                                    if machine not in tmpstate['results']:
                                        tmpstate['results'][machine] = {'ret': '', 'retcode': '', 'starttime': timestamp, 'endtime': ''}
                                    
                                    # Append output to existing output
                                    current_output = tmpstate['results'][machine].get('ret', '')
                                    tmpstate['results'][machine]['ret'] = current_output + output_data
                                    # Store last sequence for recovery
                                    if seq is not None:
                                        tmpstate['results'][machine]['last_output_seq'] = seq
                                    self.state[job_name] = tmpstate
                    
                    elif msg_type == 'sync_request':
                        # Client requests sync - tell them what we last received
                        client_last_acked = data.get('last_acked_seq', -1)
                        client_next_seq = data.get('next_seq', 0)
                        
                        server_last_seq = -1
                        if client_id in self.connections:
                            server_last_seq = self.connections[client_id].get('last_acked_seq', -1)
                        
                        print(f"WebSocket: Sync request from {client_id}: client_acked={client_last_acked}, client_next={client_next_seq}, server_last={server_last_seq}", flush=True)
                        
                        sync_response = {
                            'type': 'sync_response',
                            'last_seq': server_last_seq,
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        
                        await websocket.send(json.dumps(sync_response))
                        
                    elif msg_type == 'complete':
                        retcode = data.get('retcode', -1)
                        seq = data.get('seq', None)
                        
                        print(f"WebSocket: Received complete message from {client_id}, retcode={retcode}, seq={seq}", flush=True)
                        
                        # Send acknowledgement
                        ack_msg = {
                            'type': 'ack',
                            'ack_type': 'complete',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        if seq is not None:
                            ack_msg['seq'] = seq
                        
                        await websocket.send(json.dumps(ack_msg))
                        
                        # Validate that this job instance is actually running
                        if job_instance not in self.running:
                            print(f"WebSocket: WARNING - Received completion for unknown job instance {job_instance}", flush=True)
                            # Clean up connection anyway
                            if client_id in self.connections:
                                del self.connections[client_id]
                            continue
                        
                        # Validate that this machine is in the running list for this instance
                        if 'machines' not in self.running[job_instance] or machine not in self.running[job_instance]['machines']:
                            print(f"WebSocket: WARNING - Machine {machine} not in running list for instance {job_instance} (expected: {self.running[job_instance].get('machines', [])})", flush=True)
                            # Clean up connection anyway
                            if client_id in self.connections:
                                del self.connections[client_id]
                            continue
                        
                        # Get group info from running dict or state
                        group = 'unknown'
                        if job_instance in self.running:
                            # Try to get group from state
                            if job_name in self.state:
                                group = self.state[job_name].get('group', 'unknown')
                        
                        # Update state with final result
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                tmpstate = self.state[job_name].copy()
                                if 'results' not in tmpstate:
                                    tmpstate['results'] = {}
                                
                                # Get existing data if we have it (output was accumulated during 'output' messages)
                                starttime = timestamp
                                output = ''
                                wrapper_version = None
                                last_heartbeat = None
                                if machine in tmpstate['results']:
                                    starttime = tmpstate['results'][machine].get('starttime', timestamp)
                                    output = tmpstate['results'][machine].get('ret', '')
                                    wrapper_version = tmpstate['results'][machine].get('wrapper_version')
                                    last_heartbeat = tmpstate['results'][machine].get('last_heartbeat')
                                
                                # Update with final status, preserving wrapper_version and last_heartbeat
                                tmpstate['results'][machine] = {
                                    'ret': output,
                                    'retcode': retcode,
                                    'starttime': starttime,
                                    'endtime': timestamp
                                }
                                if wrapper_version:
                                    tmpstate['results'][machine]['wrapper_version'] = wrapper_version
                                if last_heartbeat:
                                    tmpstate['results'][machine]['last_heartbeat'] = last_heartbeat
                                self.state[job_name] = tmpstate
                                print(f"WebSocket: Updated state for {job_name}[{machine}] with endtime={timestamp}, retcode={retcode}", flush=True)
                        else:
                            print(f"WebSocket: WARNING - Cannot update state for {job_name}", flush=True)
                        
                        # Get output for logging (use what's in state or buffer)
                        log_output = ''
                        if job_name in self.state and 'results' in self.state[job_name] and machine in self.state[job_name]['results']:
                            log_output = self.state[job_name]['results'][machine].get('ret', '')
                        # Get output for logging (use what's in state or buffer)
                        log_output = ''
                        if job_name in self.state and 'results' in self.state[job_name] and machine in self.state[job_name]['results']:
                            log_output = self.state[job_name]['results'][machine].get('ret', '')
                        
                        # Remove machine from running list
                        if job_instance in self.running:
                            tmprunning = dict(self.running[job_instance])
                            if 'machines' in tmprunning and machine in tmprunning['machines']:
                                tmprunning['machines'].remove(machine)
                                self.running[job_instance] = tmprunning
                                
                                # If no more machines running, remove the job instance
                                if not tmprunning['machines']:
                                    del self.running[job_instance]
                                    print(f"WebSocket: Job instance {job_instance} completed - all machines finished", flush=True)
                        
                        # Log the result
                        if self.log_func:
                            self.log_func(
                                what='machine_result',
                                cron=job_name,
                                group=group,
                                instance=job_instance,
                                machine=machine,
                                code=retcode,
                                out=log_output,
                                time=timestamp
                            )
                        
                        print(f"WebSocket: Job completed - {client_id} (exit code: {retcode})", flush=True)
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                        
                    elif msg_type == 'killed':
                        # Wrapper acknowledges it was killed
                        print(f"WebSocket: Wrapper {client_id} acknowledged kill signal", flush=True)
                        
                    elif msg_type == 'error':
                        error_msg = data.get('error', 'Unknown error')
                        print(f"WebSocket: Error from {client_id}: {error_msg}", flush=True)
                        
                        # Log error
                        if self.log_func:
                            self.log_func(
                                what='machine_result',
                                cron=job_name,
                                group='unknown',
                                instance=job_instance,
                                machine=machine,
                                code=255,
                                out=f"Wrapper error: {error_msg}",
                                time=timestamp
                            )
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                    
                except json.JSONDecodeError as e:
                    print(f"WebSocket: Invalid JSON received: {e}", flush=True)
                except Exception as e:
                    print(f"WebSocket: Error processing message: {e}", flush=True)
                    
        except websockets.exceptions.ConnectionClosed:
            print(f"WebSocket: Connection closed - {client_id}", flush=True)
        except Exception as e:
            print(f"WebSocket: Error in client handler: {e}", flush=True)
        finally:
            # Clean up connection on disconnect
            if client_id and client_id in self.connections:
                print(f"WebSocket: Cleaning up connection - {client_id}", flush=True)
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
                            print(f"WebSocket: Kill command received for job {job_name}", flush=True)
                            
                            # Track kill time for grace period enforcement
                            self.kill_timeouts[job_name] = time.time()
                            
                            # Find all connections for this job and send kill signal
                            killed_count = 0
                            for client_id, conn_info in list(self.connections.items()):
                                if conn_info['job_name'] == job_name:
                                    try:
                                        await conn_info['websocket'].send(json.dumps({
                                            'type': 'kill',
                                            'job_name': job_name,
                                            'job_instance': conn_info['job_instance'],
                                            'machine': conn_info['machine'],
                                            'timestamp': datetime.now(timezone.utc).isoformat()
                                        }))
                                        killed_count += 1
                                        print(f"WebSocket: Sent kill signal to {client_id}", flush=True)
                                    except Exception as e:
                                        print(f"WebSocket: Error sending kill to {client_id}: {e}", flush=True)
                            
                            if killed_count > 0:
                                print(f"WebSocket: Sent kill signal to {killed_count} wrapper(s) for job {job_name}", flush=True)
                            else:
                                print(f"WebSocket: No active connections found for job {job_name}", flush=True)
                            
                            # Remove command from queue
                            self.commands.remove(cmd)
                        
                        elif 'killmachine' in cmd:
                            kill_info = cmd['killmachine']
                            job_name = kill_info.get('cron')
                            machine_name = kill_info.get('machine')
                            
                            if not job_name or not machine_name:
                                print(f"WebSocket: Invalid killmachine command - missing cron or machine: {kill_info}", flush=True)
                                self.commands.remove(cmd)
                                continue
                            
                            print(f"WebSocket: Kill command received for job {job_name} on machine {machine_name}", flush=True)
                            
                            # Find the specific connection for this job and machine
                            killed = False
                            for client_id, conn_info in list(self.connections.items()):
                                if conn_info['job_name'] == job_name and conn_info['machine'] == machine_name:
                                    try:
                                        await conn_info['websocket'].send(json.dumps({
                                            'type': 'kill',
                                            'job_name': job_name,
                                            'job_instance': conn_info['job_instance'],
                                            'machine': conn_info['machine'],
                                            'timestamp': datetime.now(timezone.utc).isoformat()
                                        }))
                                        killed = True
                                        print(f"WebSocket: Sent kill signal to {client_id}", flush=True)
                                    except Exception as e:
                                        print(f"WebSocket: Error sending kill to {client_id}: {e}", flush=True)
                                    break  # Only kill the first matching connection
                            
                            if killed:
                                print(f"WebSocket: Sent kill signal to job {job_name} on machine {machine_name}", flush=True)
                            else:
                                print(f"WebSocket: No active connection found for job {job_name} on machine {machine_name}", flush=True)
                            
                            # Remove command from queue
                            self.commands.remove(cmd)
                
                # Check for kill grace period timeouts (10 seconds)
                current_time = time.time()
                for job_name, kill_time in list(self.kill_timeouts.items()):
                    if current_time - kill_time >= 10:
                        # Grace period expired - forcefully complete any remaining targets
                        print(f"WebSocket: Kill grace period expired for {job_name}, forcefully completing", flush=True)
                        
                        # Find all running instances of this job and mark them as killed
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                tmpstate = self.state[job_name].copy()
                                if 'results' in tmpstate:
                                    now = datetime.now(timezone.utc)
                                    for machine, result in tmpstate['results'].items():
                                        # Only update if not already completed
                                        if not result.get('endtime') or result.get('endtime') == '':
                                            print(f"WebSocket: Forcefully completing {job_name} on {machine}", flush=True)
                                            result['endtime'] = now
                                            result['retcode'] = 143  # SIGTERM exit code
                                            if 'ret' not in result:
                                                result['ret'] = ''
                                            result['ret'] += "\n[Job terminated by user request - grace period expired]\n"
                                    self.state[job_name] = tmpstate
                        
                        # Remove from kill_timeouts
                        del self.kill_timeouts[job_name]
                        
                        # Clean up any lingering connections for this job
                        for client_id in list(self.connections.keys()):
                            if self.connections[client_id]['job_name'] == job_name:
                                del self.connections[client_id]
                
                await asyncio.sleep(0.5)  # Check every 500ms
            except Exception as e:
                print(f"WebSocket: Error in command checker: {e}", flush=True)
                await asyncio.sleep(1)
    
    async def start_server(self):
        """Start the WebSocket server"""
        async with websockets.serve(self.handle_client, self.host, self.port):
            print(f"WebSocket server started on ws://{self.host}:{self.port}", flush=True)
            
            # Start command checking task if we have a command queue
            if self.commands is not None:
                self.command_check_task = asyncio.create_task(self.check_commands())
                print(f"WebSocket: Command checker started", flush=True)
            
            await asyncio.Future()  # Run forever
    
    def run(self):
        """Run the WebSocket server (blocking)"""
        asyncio.run(self.start_server())

def start_websocket_server(host, port, state, running, statelocks, log_func, commands=None):
    """Start WebSocket server in a separate process"""
    server = WebSocketJobServer(host=host, port=port, state=state, running=running, 
                                statelocks=statelocks, log_func=log_func, commands=commands)
    server.run()
