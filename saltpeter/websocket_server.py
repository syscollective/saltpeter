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
    def __init__(self, host='0.0.0.0', port=8889, state=None, running=None, statelocks=None, log_func=None):
        self.host = host
        self.port = port
        self.state = state
        self.running = running
        self.statelocks = statelocks
        self.log_func = log_func
        self.connections = {}  # Track active connections by job_instance + machine
        
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
                        print(f"WebSocket: Heartbeat from {client_id}", flush=True)
                        
                    elif msg_type == 'output':
                        stream = data.get('stream', 'stdout')
                        output_data = data.get('data', '')
                        
                        if client_id in self.connections:
                            self.connections[client_id]['output_buffer'].append(output_data)
                            self.connections[client_id]['last_seen'] = timestamp
                        
                        # Optionally log output in real-time
                        print(f"WebSocket: Output from {machine} ({stream}): {output_data.strip()}", flush=True)
                        
                    elif msg_type == 'complete':
                        retcode = data.get('retcode', -1)
                        output = data.get('output', '')
                        
                        # Get group info from running dict
                        group = 'unknown'
                        if job_instance in self.running:
                            # Extract group from state or find it
                            if job_name in self.state:
                                for cron_name, cron_state in self.state.items():
                                    if cron_name == job_name and 'group' in cron_state:
                                        group = cron_state.get('group', 'unknown')
                        
                        # Update state with final result
                        if job_name in self.state and self.statelocks and job_name in self.statelocks:
                            with self.statelocks[job_name]:
                                tmpstate = self.state[job_name].copy()
                                if 'results' not in tmpstate:
                                    tmpstate['results'] = {}
                                
                                starttime = tmpstate['results'][machine].get('starttime', timestamp) if machine in tmpstate['results'] else timestamp
                                
                                tmpstate['results'][machine] = {
                                    'ret': output,
                                    'retcode': retcode,
                                    'starttime': starttime,
                                    'endtime': timestamp
                                }
                                self.state[job_name] = tmpstate
                        
                        # Remove machine from running list
                        if job_instance in self.running:
                            tmprunning = dict(self.running[job_instance])
                            if 'machines' in tmprunning and machine in tmprunning['machines']:
                                tmprunning['machines'].remove(machine)
                                self.running[job_instance] = tmprunning
                        
                        # Log the result
                        if self.log_func:
                            self.log_func(
                                what='machine_result',
                                cron=job_name,
                                group=group,
                                instance=job_instance,
                                machine=machine,
                                code=retcode,
                                out=output,
                                time=timestamp
                            )
                        
                        print(f"WebSocket: Job completed - {client_id} (exit code: {retcode})", flush=True)
                        
                        # Clean up connection
                        if client_id in self.connections:
                            del self.connections[client_id]
                        
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
    
    async def start_server(self):
        """Start the WebSocket server"""
        async with websockets.serve(self.handle_client, self.host, self.port):
            print(f"WebSocket server started on ws://{self.host}:{self.port}", flush=True)
            await asyncio.Future()  # Run forever
    
    def run(self):
        """Run the WebSocket server (blocking)"""
        asyncio.run(self.start_server())

def start_websocket_server(host, port, state, running, statelocks, log_func):
    """Start WebSocket server in a separate process"""
    server = WebSocketJobServer(host=host, port=port, state=state, running=running, 
                                statelocks=statelocks, log_func=log_func)
    server.run()
