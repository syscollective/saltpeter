#!/usr/bin/env python3
"""
WebSocket and HTTP API server for UI communication using asyncio
Replaces the Tornado-based implementation with asyncio/aiohttp
"""

import asyncio
import json
import copy
from datetime import datetime
from aiohttp import web
from .version import __version__


class UIEndpoint:
    def __init__(self, bind_addr, port, config, running, state, commands, bad_crons, timeline, debug_flag=None):
        self.bind_addr = bind_addr
        self.port = port
        self.config = config
        self.running = running
        self.state = state
        self.commands = commands
        self.bad_crons = bad_crons
        self.timeline = timeline
        self.debug_flag = debug_flag  # Shared debug flag from Manager
        
        self.ws_connections = {}  # Changed to dict to track per-connection state
        self.cfgserial = ''
        self.tmlserial = ''
    
    def debug_print(self, message, flush=True):
        """Print debug message only if debug mode is enabled"""
        # Check shared debug flag first, then fall back to config
        if self.debug_flag and self.debug_flag.value:
            print(message, flush=flush)
        elif 'saltpeter_config' in self.config and self.config['saltpeter_config'].get('debug', False):
            print(message, flush=flush)
    
    async def handle_websocket_http(self, request):
        """Handle WebSocket upgrade requests on HTTP server"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        print('[UI WS] New connection from UI')
        
        # Track connection state
        connection_state = {
            'subscriptions': [],
            'output_positions': {},  # {cron: {machine: last_sent_position}}
            'last_cfg_serial': '',
            'last_tml_serial': ''
        }
        self.ws_connections[id(ws)] = {'ws': ws, 'state': connection_state}
        
        try:
            # Send initial data
            await self.send_data_http(ws, connection_state, cfg_update=True, tml_update=True)
            
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        
                        if 'subscribe' in data:
                            cron_list = data['subscribe']
                            # Support both string and array
                            if isinstance(cron_list, str):
                                cron_list = [cron_list]
                            elif not isinstance(cron_list, list):
                                cron_list = [cron_list]
                            
                            for cron in cron_list:
                                if cron not in connection_state['subscriptions']:
                                    connection_state['subscriptions'].append(cron)
                                    # Initialize output position tracking for this subscription
                                    if cron not in connection_state['output_positions']:
                                        connection_state['output_positions'][cron] = {}
                                    # Don't reset positions - let incremental logic handle it
                                    # New connections start with empty positions (defaults to 0)
                                    # Existing connections keep their positions
                                    print(f'[UI WS] Client subscribed to {cron}')
                            
                            # Send details for subscribed crons immediately
                            await self.send_data_http(ws, connection_state, cfg_update=False, tml_update=False)
                            
                        elif 'unsubscribe' in data:
                            cron_list = data['unsubscribe']
                            # Support both string and array
                            if isinstance(cron_list, str):
                                cron_list = [cron_list]
                            elif not isinstance(cron_list, list):
                                cron_list = [cron_list]
                            
                            for cron in cron_list:
                                if cron in connection_state['subscriptions']:
                                    connection_state['subscriptions'].remove(cron)
                                    print(f'[UI WS] Client unsubscribed from {cron}')
                                # Clean up output position tracking
                                if cron in connection_state['output_positions']:
                                    del connection_state['output_positions'][cron]
                                
                        elif 'ack' in data:
                            # Client acknowledges receipt of output chunk
                            # Note: We don't update output_positions based on acks - server is authoritative
                            # Acks are for flow control/logging only
                            ack_info = data['ack']
                            cron = ack_info.get('cron')
                            machine = ack_info.get('machine')
                            position = ack_info.get('position')
                            
                            if cron and machine and position is not None:
                                print(f'[UI WS] Ack from client for {cron}[{machine}] position={position}')
                                
                        elif 'run' in data:
                            cron = data['run']
                            self.commands.append({'runnow': cron})
                            
                        elif 'killCron' in data:
                            cron = data['killCron']
                            self.commands.append({'killcron': cron})
                            
                        elif 'killMachine' in data:
                            kill_info = data['killMachine']
                            job_name = kill_info.get('cron')
                            machine = kill_info.get('machine')
                            job_instance = kill_info.get('job_instance')
                            
                            # If UI didn't provide job_instance, look it up (backwards compatibility)
                            if not job_instance:
                                for proc_name, proc_info in self.running.items():
                                    if proc_info.get('name') == job_name:
                                        # Check if this machine is in the job's machines list
                                        if 'machines' in proc_info and machine in proc_info['machines']:
                                            job_instance = proc_name
                                            kill_info['job_instance'] = job_instance
                                            break
                            
                            if job_instance:
                                self.commands.append({'killmachine': kill_info})
                            else:
                                print(f"[UI WS] Cannot find job_instance for kill command: cron={job_name}, machine={machine}", flush=True)
                            
                        elif 'getTimeline' in data:
                            timeline_params = data['getTimeline']
                            self.commands.append({'get_timeline': timeline_params})
                            
                    except json.JSONDecodeError as e:
                        print(f'[UI WS] Could not parse message as JSON: {e}')
                    except Exception as e:
                        print(f'[UI WS] Error processing message: {e}')
                        
                elif msg.type == web.WSMsgType.ERROR:
                    print(f'[UI WS] WebSocket error: {ws.exception()}')
                    
        except Exception as e:
            print(f'[UI WS] Error in WebSocket handler: {e}')
        finally:
            if id(ws) in self.ws_connections:
                del self.ws_connections[id(ws)]
            print('[UI WS] Connection closed')
        
        return ws
    
    async def send_data_http(self, ws, connection_state, cfg_update, tml_update):
        """Send data to a specific WebSocket connection (aiohttp version) with incremental output streaming"""
        try:
            if cfg_update:
                await ws.send_str(json.dumps({
                    'type': 'config',
                    'config': dict(self.config),
                    'sp_version': __version__
                }))
                connection_state['last_cfg_serial'] = self.config.get('serial', '')
            
            # Send running state
            srrng = self.running.copy()
            rng_names = []
            for cron in srrng:
                rng_names.append(srrng[cron]['name'])
                if 'started' in srrng[cron]:
                    srrng[cron]['started'] = srrng[cron]['started'].isoformat()
            
            # Send last state
            srst = self.state.copy()
            lastst = {}
            for cron in srst:
                if 'last_run' in srst[cron] and srst[cron]['last_run'] != '':
                    lastst[cron] = {}
                    lastst[cron]['last_run'] = srst[cron]['last_run'].isoformat()
                    
                    # Read success status from state (evaluated once at job end)
                    # Only show final status if job is not currently running
                    if cron not in rng_names:
                        lastst[cron]['result_ok'] = srst[cron].get('last_success', False)
                    else:
                        # Job still running - don't show final status yet
                        lastst[cron]['result_ok'] = True
            
            # Send in new protocol format with type field
            await ws.send_str(json.dumps({
                'type': 'status',
                'running': srrng,
                'last_state': lastst,
                'sp_version': __version__
            }, default=str))
            
            # Send subscribed cron details with incremental output
            subscriptions = connection_state['subscriptions']
            output_positions = connection_state['output_positions']
            
            for cron in self.config['crons']:
                if cron in subscriptions:
                    srcron = copy.deepcopy(self.state[cron])
                    
                    # Debug: log what state contains
                    if 'results' in srcron:
                        self.debug_print(f"[UI DEBUG] Sending cron {cron}: {len(srcron['results'])} machines")
                    else:
                        self.debug_print(f"[UI DEBUG] Sending cron {cron}: NO RESULTS")
                    
                    if 'next_run' in srcron:
                        srcron['next_run'] = srcron['next_run'].isoformat()
                    if 'last_run' in srcron:
                        srcron['last_run'] = srcron['last_run'].isoformat()
                    
                    # Handle incremental output streaming
                    if 'results' in srcron:
                        if cron not in output_positions:
                            output_positions[cron] = {}
                        
                        for machine in srcron['results']:
                            if 'starttime' in srcron['results'][machine] and srcron['results'][machine]['starttime'] != '':
                                srcron['results'][machine]['starttime'] = srcron['results'][machine]['starttime'].isoformat()
                            if 'endtime' in srcron['results'][machine] and srcron['results'][machine]['endtime'] != '':
                                srcron['results'][machine]['endtime'] = srcron['results'][machine]['endtime'].isoformat()
                            
                            # Stream only new output since last position
                            full_output = srcron['results'][machine].get('ret', '')
                            last_position = output_positions[cron].get(machine, 0)
                            
                            if len(full_output) > last_position:
                                # Send incremental chunk
                                new_chunk = full_output[last_position:]
                                chunk_msg = {
                                    'type': 'output_chunk',
                                    'cron': cron,
                                    'machine': machine,
                                    'chunk': new_chunk,
                                    'position': last_position,
                                    'total_length': len(full_output),
                                    'is_complete': srcron['results'][machine].get('endtime', '') != ''
                                }
                                await ws.send_str(json.dumps(chunk_msg))
                                
                                # Update position after sending
                                output_positions[cron][machine] = len(full_output)
                            elif len(full_output) < last_position:
                                # Output was reset (new run) - reset position to 0
                                self.debug_print(f'[OUTPUT DEBUG] Output smaller than position - resetting: {len(full_output)} < {last_position}')
                                output_positions[cron][machine] = 0
                                # Resend from beginning
                                if len(full_output) > 0:
                                    chunk_msg = {
                                        'type': 'output_chunk',
                                        'cron': cron,
                                        'machine': machine,
                                        'chunk': full_output,
                                        'position': 0,
                                        'total_length': len(full_output),
                                        'is_complete': srcron['results'][machine].get('endtime', '') != ''
                                    }
                                    await ws.send_str(json.dumps(chunk_msg))
                                    output_positions[cron][machine] = len(full_output)
                    
                    # Clear ret from details message (output sent via output_chunk)
                    # Need to make a deep copy to avoid modifying the shared state
                    if 'results' in srcron:
                        srcron = srcron.copy()  # Shallow copy of top level
                        srcron['results'] = {}
                        for machine, result in self.state[cron].get('results', {}).items():
                            srcron['results'][machine] = result.copy()  # Copy each result
                            srcron['results'][machine]['ret'] = ''  # Clear output from details
                            if 'ret' in result:
                                srcron['results'][machine]['output_length'] = len(result['ret'])
                    
                    # Send cron details with type field
                    await ws.send_str(json.dumps({
                        'type': 'details',
                        'cron': cron,
                        'data': srcron
                    }, default=str))
            
            if tml_update:
                await ws.send_str(json.dumps({'timeline': self.timeline.copy()}, default=str))
                connection_state['last_tml_serial'] = self.timeline.get('id', '')
                
        except Exception as e:
            print(f'[UI WS] Error sending data to websocket: {e}')
    
    async def broadcast_updates(self):
        """Periodically broadcast updates to all connected clients"""
        while True:
            await asyncio.sleep(2)
            
            cfg_update = False
            if self.cfgserial != self.config.get('serial', ''):
                self.cfgserial = self.config.get('serial', '')
                cfg_update = True
            
            tml_update = False
            if 'id' in self.timeline:
                if self.tmlserial != self.timeline['id']:
                    self.tmlserial = self.timeline['id']
                    tml_update = True
            
            if len(self.ws_connections) > 0:
                # Send updates to all connections
                disconnected = []
                for conn_id, conn_info in list(self.ws_connections.items()):
                    ws = conn_info['ws']
                    connection_state = conn_info['state']
                    try:
                        # Only send cfg/tml updates if changed for this connection
                        send_cfg = cfg_update and connection_state['last_cfg_serial'] != self.config.get('serial', '')
                        send_tml = tml_update and connection_state['last_tml_serial'] != self.timeline.get('id', '')
                        
                        await self.send_data_http(ws, connection_state, send_cfg, send_tml)
                    except Exception as e:
                        print(f'[UI WS] Error broadcasting to websocket: {e}')
                        disconnected.append(conn_id)
                
                # Clean up disconnected clients
                for conn_id in disconnected:
                    if conn_id in self.ws_connections:
                        del self.ws_connections[conn_id]
    
    # HTTP handlers
    async def handle_version(self, request):
        """GET /version"""
        response = {
            'version': '3.5.1',
            'last_build': datetime.now().date().isoformat()
        }
        return web.json_response(response, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
        })
    
    async def handle_config(self, request):
        """GET /config"""
        return web.json_response(dict(self.config), headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
        })
    
    async def handle_running(self, request):
        """GET /running"""
        return web.json_response(dict(self.running), headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
        })
    
    async def handle_timeline(self, request):
        """GET /timeline"""
        return web.json_response(dict(self.timeline), headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
        })
    
    async def handle_options(self, request):
        """Handle OPTIONS requests for CORS"""
        return web.Response(status=204, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
        })
    
    async def start_servers(self):
        """Start both WebSocket and HTTP servers"""
        # Create aiohttp application with WebSocket support
        app = web.Application()
        
        # HTTP routes
        app.router.add_get('/version', self.handle_version)
        app.router.add_get('/config', self.handle_config)
        app.router.add_get('/running', self.handle_running)
        app.router.add_get('/timeline', self.handle_timeline)
        
        # WebSocket route (same as Tornado had)
        app.router.add_get('/ws', self.handle_websocket_http)
        
        # OPTIONS for CORS
        app.router.add_options('/{tail:.*}', self.handle_options)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.bind_addr, self.port)
        await site.start()
        
        print(f"[UI WS] API WebSocket server listening on {self.bind_addr}:{self.port} (HTTP + WebSocket on /ws)")
        
        # Start broadcast task
        broadcast_task = asyncio.create_task(self.broadcast_updates())
        
        # Keep running
        await asyncio.Future()
    
    def run(self):
        """Run the server (blocking)"""
        asyncio.run(self.start_servers())


def start(bind_addr, port, config, running, state, commands, bad_crons, timeline, debug_flag=None):
    """Start the UI endpoint server"""
    server = UIEndpoint(bind_addr, port, config, running, state, commands, bad_crons, timeline, debug_flag)
    server.run()
