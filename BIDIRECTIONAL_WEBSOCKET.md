# Bidirectional WebSocket Communication

## Overview

The WebSocket communication between Saltpeter server and wrapper scripts is now fully bidirectional, allowing the server to send kill signals to running jobs.

## Architecture

### Components

1. **WebSocket Server** (`websocket_server.py`)
   - Receives status updates from wrappers (connect, start, output, heartbeat, complete)
   - Sends kill signals to wrappers when requested
   - Background task checks command queue for kill requests

2. **Wrapper Script** (`wrapper.py`)
   - Sends status updates to server
   - Listens for incoming kill messages while running
   - Terminates subprocess gracefully (SIGTERM) or forcefully (SIGKILL) on kill signal

3. **Main Process** (`main.py`)
   - Shares command queue with WebSocket server
   - API/UI adds kill commands to the queue

### Message Flow

#### Wrapper → Server (Existing)
- `connect`: Initial connection
- `start`: Job started with PID
- `output`: Streaming output (stdout/stderr)
- `heartbeat`: Periodic heartbeat (every 5 seconds)
- `complete`: Job finished with exit code
- `error`: Error occurred

#### Server → Wrapper (New)
- `kill`: Terminate the job

### Kill Process Flow

1. **UI/API Request**: User clicks "Kill Job" button
   - API adds `{'killcron': 'job_name'}` to commands queue

2. **WebSocket Server**: Background task checks commands queue
   - Finds kill command for job
   - Looks up all active connections for that job name
   - Sends `kill` message to each wrapper connection
   - Removes command from queue

3. **Wrapper**: Receives kill message
   - Terminates subprocess with SIGTERM
   - Waits 5 seconds for graceful shutdown
   - If still running, sends SIGKILL
   - Appends "[Job terminated by user request]" to output
   - Sends final completion message with exit code 143

4. **State Update**: Wrapper completion triggers normal flow
   - State updated with final output and exit code
   - Job removed from running list
   - UI shows job as terminated

## Exit Codes

- `0`: Success
- `124`: Job timeout (hard timeout exceeded)
- `143`: Job killed by user request (SIGTERM)
- `253`: Heartbeat timeout (wrapper lost connection)
- `255`: Error (target unreachable, wrapper error, etc.)

## Implementation Details

### WebSocket Server Changes

```python
class WebSocketJobServer:
    def __init__(self, ..., commands=None):
        self.commands = commands  # Shared command queue
        
    async def check_commands(self):
        """Background task checking for kill commands"""
        while True:
            for cmd in self.commands:
                if 'killcron' in cmd:
                    # Send kill to all connections for this job
                    for client_id, conn in self.connections.items():
                        if conn['job_name'] == cmd['killcron']:
                            await conn['websocket'].send(json.dumps({
                                'type': 'kill',
                                ...
                            }))
```

### Wrapper Changes

```python
async def run_command_and_stream(...):
    while True:
        # Listen for messages (non-blocking with timeout)
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
            data = json.loads(message)
            
            if data.get('type') == 'kill':
                # Terminate process
                process.terminate()
                process.wait(timeout=5)  # Grace period
                if process.poll() is None:
                    process.kill()  # Force kill
                break
        except asyncio.TimeoutError:
            pass  # No message, continue normally
```

### Main Process Changes

```python
# Pass commands queue to WebSocket server
ws_server = multiprocessing.Process(
    target=websocket_server.start_websocket_server,
    args=(..., commands),  # Shared Manager.list()
    ...
)
```

## Testing

### Manual Test

1. Start a long-running job through Saltpeter
2. Use the UI or send WebSocket message:
   ```javascript
   ws.send(JSON.stringify({killCron: 'job_name'}))
   ```
3. Verify:
   - Job terminates within 5 seconds
   - Output shows "[Job terminated by user request]"
   - Exit code is 143
   - Job removed from running list

### Test Script

```bash
python3 test_kill_job.py <job_name>
```

## Notes

- Kill signal is sent to ALL machines running the job
- Wrapper attempts graceful termination (SIGTERM) first
- After 5 seconds, wrapper sends SIGKILL if process still alive
- WebSocket connection must be active for kill to work
- If connection is lost, heartbeat timeout will eventually mark job as failed (retcode 253)

## Future Enhancements

- Per-machine kill (kill specific instance, not entire job)
- Pause/resume functionality
- Custom signals (not just SIGTERM/SIGKILL)
- Kill timeout configuration
- UI confirmation dialog before killing
