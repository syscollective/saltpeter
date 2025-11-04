#!/usr/bin/env python3
"""
WebSocket server for receiving job updates from wrapper scripts
"""

import asyncio
import websockets
import json
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
                            'output_buffer': []
                        }
                        print(f"WebSocket: Client connected - {client_id}", flush=True)
                        
                    elif msg_type == 'start':
                        if client_id in self.connections:
                            self.connections[client_id]['pid'] = data.get('pid')
                            self.connections[client_id]['started'] = timestamp
                        
                        # Validate that this job instance is running
                        if job_instance not in self.running:
                            print(f"WebSocket: WARNING - Received start for unknown job instance {job_instance}", flush=True)
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
                                self.state[job_name] = tmpstate
                        
                        print(f"WebSocket: Job started - {client_id} (PID: {data.get('pid')})", flush=True)
                        
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
                        
                        # Validate that this job instance is running
                        if job_instance not in self.running:
                            continue
                        
                        if client_id in self.connections:
                            self.connections[client_id]['output_buffer'].append(output_data)
                            self.connections[client_id]['last_seen'] = timestamp
                        
                        # Update state with accumulated output
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
                                    self.state[job_name] = tmpstate
                        
                    elif msg_type == 'complete':
                        retcode = data.get('retcode', -1)
                        
                        print(f"WebSocket: Received complete message from {client_id}, retcode={retcode}", flush=True)
                        
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
                                if machine in tmpstate['results']:
                                    starttime = tmpstate['results'][machine].get('starttime', timestamp)
                                    output = tmpstate['results'][machine].get('ret', '')
                                
                                # Update with final status
                                tmpstate['results'][machine] = {
                                    'ret': output,
                                    'retcode': retcode,
                                    'starttime': starttime,
                                    'endtime': timestamp
                                }
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
