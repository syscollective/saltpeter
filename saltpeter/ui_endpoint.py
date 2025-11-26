#!/usr/bin/env python3
"""
WebSocket and HTTP API server for UI communication using asyncio
Replaces the Tornado-based implementation with asyncio/aiohttp
"""

import asyncio
import json
from datetime import datetime
from aiohttp import web
from .version import __version__


class UIEndpoint:
    def __init__(self, port, config, running, state, commands, bad_crons, timeline):
        self.port = port
        self.config = config
        self.running = running
        self.state = state
        self.commands = commands
        self.bad_crons = bad_crons
        self.timeline = timeline
        
        self.ws_connections = {}  # Changed to dict to track per-connection state
        self.cfgserial = ''
        self.tmlserial = ''
    
    async def handle_websocket_http(self, request):
        """Handle WebSocket upgrade requests on HTTP server"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        print('New WS connection from UI')
        
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
                            cron = data['subscribe']
                            connection_state['subscriptions'].append(cron)
                            # Initialize output position tracking for this subscription
                            if cron not in connection_state['output_positions']:
                                connection_state['output_positions'][cron] = {}
                            await self.send_data_http(ws, connection_state, cfg_update=False, tml_update=False)
                            
                        elif 'unsubscribe' in data:
                            cron = data['unsubscribe']
                            if cron in connection_state['subscriptions']:
                                connection_state['subscriptions'].remove(cron)
                            # Clean up output position tracking
                            if cron in connection_state['output_positions']:
                                del connection_state['output_positions'][cron]
                                
                        elif 'ack' in data:
                            # Client acknowledges receipt of output chunk
                            ack_info = data['ack']
                            cron = ack_info.get('cron')
                            machine = ack_info.get('machine')
                            position = ack_info.get('position')
                            
                            if cron and machine and position is not None:
                                if cron in connection_state['output_positions']:
                                    connection_state['output_positions'][cron][machine] = position
                                    print(f'UI WS: Ack from client for {cron}[{machine}] position={position}')
                                
                        elif 'run' in data:
                            cron = data['run']
                            self.commands.append({'runnow': cron})
                            
                        elif 'killCron' in data:
                            cron = data['killCron']
                            self.commands.append({'killcron': cron})
                            
                        elif 'killMachine' in data:
                            kill_info = data['killMachine']
                            self.commands.append({'killmachine': kill_info})
                            
                        elif 'getTimeline' in data:
                            timeline_params = data['getTimeline']
                            self.commands.append({'get_timeline': timeline_params})
                            
                    except json.JSONDecodeError as e:
                        print(f'Could not parse UI message as JSON: {e}')
                    except Exception as e:
                        print(f'Error processing UI message: {e}')
                        
                elif msg.type == web.WSMsgType.ERROR:
                    print(f'UI WebSocket error: {ws.exception()}')
                    
        except Exception as e:
            print(f'Error in UI WebSocket handler: {e}')
        finally:
            if id(ws) in self.ws_connections:
                del self.ws_connections[id(ws)]
            print('UI WS connection closed')
        
        return ws
    
    async def send_data_http(self, ws, connection_state, cfg_update, tml_update):
        """Send data to a specific WebSocket connection (aiohttp version) with incremental output streaming"""
        try:
            if cfg_update:
                await ws.send_str(json.dumps({
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
                    if 'results' in srst[cron] and len(srst[cron]['results']) > 0:
                        lastst[cron]['result_ok'] = True
                        false_result_number = 0
                        for tgt_key in srst[cron]['results']:
                            tgt = srst[cron]['results'][tgt_key]
                            if cron not in rng_names:
                                if 'retcode' not in tgt or (tgt['retcode'] != 0 and tgt['retcode'] != "0"):
                                    false_result_number += 1
                        if false_result_number == len(srst[cron]['results']):
                            lastst[cron]['result_ok'] = False
                    else:
                        lastst[cron]['result_ok'] = False
            
            await ws.send_str(json.dumps({
                'running': srrng,
                'last_state': lastst
            }, default=str))
            
            # Send subscribed cron details with incremental output
            subscriptions = connection_state['subscriptions']
            output_positions = connection_state['output_positions']
            
            for cron in self.config['crons']:
                if cron in subscriptions:
                    srcron = self.state[cron].copy()
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
                                
                                # Update position after sending (client should ack)
                                # We optimistically update but client can nack if needed
                                output_positions[cron][machine] = len(full_output)
                            
                            # For the main status update, send summary without full output
                            srcron['results'][machine]['ret'] = ''  # Don't send full output in main message
                            srcron['results'][machine]['output_length'] = len(full_output)
                    
                    await ws.send_str(json.dumps({cron: srcron}, default=str))
            
            if tml_update:
                await ws.send_str(json.dumps({'timeline': self.timeline.copy()}, default=str))
                connection_state['last_tml_serial'] = self.timeline.get('id', '')
                
        except Exception as e:
            print(f'Error sending data to UI websocket: {e}')
    
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
                        print(f'Error broadcasting to websocket: {e}')
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
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        
        print(f"UI endpoint started on port {self.port} (HTTP + WebSocket on /ws)")
        
        # Start broadcast task
        broadcast_task = asyncio.create_task(self.broadcast_updates())
        
        # Keep running
        await asyncio.Future()
    
    def run(self):
        """Run the server (blocking)"""
        asyncio.run(self.start_servers())


def start(port, config, running, state, commands, bad_crons, timeline):
    """Start the UI endpoint server"""
    server = UIEndpoint(port, config, running, state, commands, bad_crons, timeline)
    server.run()
