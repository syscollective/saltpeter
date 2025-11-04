# Bidirectional WebSocket Implementation Summary

## What Was Changed

### 1. WebSocket Server (`saltpeter/websocket_server.py`)

**Added bidirectional communication capability:**
- Added `commands` parameter to constructor to receive shared command queue from main process
- Implemented `check_commands()` background task that:
  - Polls the commands queue every 500ms
  - Detects `killcron` commands
  - Sends `kill` messages to all active wrapper connections for the specified job
  - Removes processed commands from queue
- Added `killed` message type handler for wrapper acknowledgments
- Modified `start_server()` to launch the command checking background task

**Key code:**
```python
async def check_commands(self):
    """Background task to check for kill commands and send them to wrappers"""
    while True:
        if self.commands:
            for cmd in list(self.commands):
                if 'killcron' in cmd:
                    # Send kill to all connections for this job
                    for client_id, conn_info in list(self.connections.items()):
                        if conn_info['job_name'] == job_name:
                            await conn_info['websocket'].send(json.dumps({
                                'type': 'kill',
                                ...
                            }))
```

### 2. Wrapper Script (`saltpeter/wrapper.py`)

**Added kill signal reception:**
- Modified main event loop to listen for incoming WebSocket messages
- Uses `asyncio.wait_for()` with 0.1s timeout for non-blocking message checks
- When `kill` message received:
  - Terminates subprocess with SIGTERM
  - Waits 5 seconds for graceful shutdown
  - If still running, sends SIGKILL
  - Sets `killed` flag
  - Breaks from main loop
- Appends "[Job terminated by user request]" to output when killed
- Uses exit code 143 (standard SIGTERM exit code) for killed jobs
- Added process cleanup in exception handler

**Key code:**
```python
# Check for incoming messages from server (non-blocking)
try:
    message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
    data = json.loads(message)
    
    if data.get('type') == 'kill':
        killed = True
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        break
except asyncio.TimeoutError:
    pass  # No message, continue
```

### 3. Main Process (`saltpeter/main.py`)

**Shared commands queue with WebSocket server:**
- Modified WebSocket server initialization to pass the `commands` Manager.list()
- This allows the API and WebSocket server to share the same command queue
- No other changes needed - existing kill command handling in API already adds to this queue

**Key code:**
```python
ws_server = multiprocessing.Process(
    target=websocket_server.start_websocket_server,
    args=(args.websocket_host, args.websocket_port, state, running, 
          statelocks, log, commands),  # Added commands parameter
    name='websocket_server'
)
```

## How It Works

### Normal Flow (No Kill)
1. Wrapper connects to WebSocket server
2. Wrapper sends: connect → start → output → heartbeat → output → complete
3. Server updates state based on messages
4. Job completes normally

### Kill Flow
1. **User Action**: User clicks "Kill" in UI or sends API request
2. **API**: Adds `{'killcron': 'job_name'}` to commands Manager.list()
3. **WebSocket Server**: Background task detects command
4. **WebSocket Server**: Sends `kill` message to all wrappers for that job
5. **Wrapper**: Receives kill message in event loop
6. **Wrapper**: Terminates subprocess (SIGTERM, then SIGKILL if needed)
7. **Wrapper**: Sends final output with termination message
8. **Wrapper**: Sends `complete` with exit code 143
9. **Server**: Updates state as normal completion
10. **UI**: Shows job as terminated with exit code 143

## Message Protocol

### Server → Wrapper (New)
```json
{
  "type": "kill",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699123456",
  "machine": "server01.example.com",
  "timestamp": "2025-11-04T10:30:45.123456+00:00"
}
```

### Wrapper → Server (Existing)
- `connect`, `start`, `output`, `heartbeat`, `complete`, `error`

## Exit Codes Reference

| Code | Meaning |
|------|---------|
| 0    | Success |
| 124  | Hard timeout exceeded |
| 143  | **Killed by user (SIGTERM)** |
| 253  | Heartbeat timeout (lost connection) |
| 255  | Error (unreachable, wrapper error) |

## Testing

### Prerequisites
```bash
# Ensure Saltpeter is running with API enabled
python3 -m saltpeter.main -c /etc/saltpeter -a -p 8888
```

### Test Scenario 1: Manual Kill via API
```python
# In Python shell or script
import websocket
import json

ws = websocket.create_connection("ws://localhost:8888/ws")
ws.send(json.dumps({'killCron': 'your_job_name'}))
ws.close()
```

### Test Scenario 2: Kill Long-Running Job
1. Create a test job that sleeps for 60 seconds:
   ```yaml
   long_sleep:
     min: '*'
     hour: '*'
     dom: '*'
     mon: '*'
     dow: '*'
     targets: '*'
     target_type: 'glob'
     command: 'sleep 60'
   ```

2. Wait for job to start
3. Send kill command
4. Verify:
   - Job terminates within 5 seconds
   - Output contains "[Job terminated by user request]"
   - Exit code is 143
   - Job removed from running list

### Expected Logs

**WebSocket Server:**
```
WebSocket: Kill command received for job long_sleep
WebSocket: Sent kill signal to long_sleep_1699123456:server01.example.com
WebSocket: Job completed - long_sleep_1699123456:server01.example.com (exit code: 143)
```

**Wrapper (stderr):**
```
Received kill signal from server
Terminating process 12345
```

## Backwards Compatibility

- All existing functionality preserved
- Wrappers without kill support will work normally (just won't respond to kill signals)
- API kill commands will still work via WebSocket even if old wrappers don't support it
- Graceful degradation: if wrapper connection lost, normal heartbeat timeout applies

## Known Limitations

1. **Kill applies to entire job**: Cannot kill individual machines, only all machines running the job
2. **Requires active connection**: If WebSocket connection lost, kill won't reach wrapper
3. **5-second grace period**: Fixed timeout between SIGTERM and SIGKILL
4. **No pause/resume**: Only full termination supported

## Future Enhancements

- [ ] Per-machine kill (kill specific instance)
- [ ] Configurable grace period for SIGTERM → SIGKILL
- [ ] Pause/resume functionality
- [ ] Custom signal support (SIGHUP, SIGUSR1, etc.)
- [ ] Kill confirmation in UI
- [ ] Kill all jobs button
- [ ] Selective kill (kill only failed machines)

## Files Modified

1. `saltpeter/websocket_server.py` - Added bidirectional message handling
2. `saltpeter/wrapper.py` - Added kill signal reception and process termination
3. `saltpeter/main.py` - Pass commands queue to WebSocket server
4. `BIDIRECTIONAL_WEBSOCKET.md` - Documentation (this file)
5. `test_kill_job.py` - Test utility for manual testing

## Deployment Notes

1. **Update wrapper on all minions**: New wrapper must be deployed to support kill signals
2. **Restart Saltpeter**: Main process must be restarted to use new WebSocket server
3. **No config changes needed**: Existing configuration files work as-is
4. **Backwards compatible**: Old wrappers will continue to work (just won't respond to kills)

## Security Considerations

- Kill commands flow through same authenticated API as other commands
- No new authentication/authorization needed
- WebSocket server validates job_instance and machine before processing messages
- Cannot kill jobs not owned by current Saltpeter instance
