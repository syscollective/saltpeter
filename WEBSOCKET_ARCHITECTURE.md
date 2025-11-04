# WebSocket-Based Job Execution Architecture

## Overview

Saltpeter has been refactored to use WebSocket communication for job execution and monitoring. This addresses issues with Salt's handling of long-running processes and provides real-time job monitoring capabilities.

## Architecture Components

### 1. Wrapper Script (`saltpeter/wrapper.py`)

The wrapper script is executed by Salt minions and performs the following functions:

- **Immediate Return**: Exits successfully immediately after starting, preventing Salt timeouts
- **Subprocess Execution**: Runs the actual job command in a subprocess
- **WebSocket Communication**: Connects to Saltpeter's WebSocket server
- **Heartbeat**: Sends heartbeat messages every 5 seconds while the job is running
- **Real-time Streaming**: Streams stdout and stderr output in real-time
- **Exit Reporting**: Reports the process exit code when the job completes

#### Wrapper Usage

```bash
python3 wrapper.py <websocket_url> <job_name> <job_instance> <machine_id> <command> [cwd] [user]
```

**Parameters:**
- `websocket_url`: WebSocket server URL (e.g., `ws://saltpeter:8889`)
- `job_name`: Name of the cron job
- `job_instance`: Unique instance identifier for this job run
- `machine_id`: Hostname or identifier of the machine
- `command`: The actual command to execute
- `cwd`: (Optional) Working directory for the command
- `user`: (Optional) User to run the command as

### 2. WebSocket Server (`saltpeter/websocket_server.py`)

The WebSocket server handles incoming connections from wrapper scripts and processes job events.

#### Message Types

##### 1. Connect
```json
{
  "type": "connect",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "timestamp": "2025-11-04T10:00:00+00:00"
}
```

##### 2. Start
```json
{
  "type": "start",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "pid": 12345,
  "timestamp": "2025-11-04T10:00:01+00:00"
}
```

##### 3. Heartbeat
```json
{
  "type": "heartbeat",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "timestamp": "2025-11-04T10:00:05+00:00"
}
```

##### 4. Output
```json
{
  "type": "output",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "stream": "stdout",
  "data": "Backing up file 1 of 100\n",
  "timestamp": "2025-11-04T10:00:02+00:00"
}
```

##### 5. Complete
```json
{
  "type": "complete",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "retcode": 0,
  "output": "Complete job output...",
  "timestamp": "2025-11-04T10:15:00+00:00"
}
```

##### 6. Error
```json
{
  "type": "error",
  "job_name": "backup_job",
  "job_instance": "backup_job_1699000000",
  "machine": "server01",
  "error": "Connection failed",
  "timestamp": "2025-11-04T10:00:10+00:00"
}
```

### 3. Modified Job Execution (`main.py`)

The `run()` function has been refactored to:

1. Deploy the wrapper script to minions (via Salt)
2. Execute the wrapper instead of the actual command
3. Wait for WebSocket-based results instead of polling Salt
4. Handle timeouts at the Saltpeter level

#### New Function: `processresults_websocket()`

This replaces the old `processresults()` function and:

- Waits for WebSocket messages indicating job completion
- Implements configurable timeout handling
- Marks jobs as timed out if they exceed the specified duration
- Updates the shared state as results arrive

## Configuration

### Command-line Arguments

New arguments for WebSocket functionality:

```bash
saltpeter -w 8889 --websocket-host 0.0.0.0
```

- `-w`, `--websocket-port`: Port for WebSocket server (default: 8889)
- `--websocket-host`: Host interface for WebSocket server (default: 0.0.0.0)

### YAML Configuration

Job timeout is still configured in the YAML cron definition:

```yaml
backup_job:
  targets: '*'
  target_type: 'glob'
  command: '/usr/local/bin/backup.sh'
  timeout: 3600  # 1 hour timeout
  min: '0'
  hour: '2'
  dom: '*'
  mon: '*'
  dow: '*'
```

## Deployment

### 1. Install Dependencies

```bash
pip install websockets
```

Or:

```bash
pip install -r requirements.txt
```

### 2. Deploy Wrapper Script

The wrapper script needs to be available on all Salt minions. Options:

**Option A: Salt File Server**
```bash
# Place wrapper.py in Salt's file_roots
cp saltpeter/wrapper.py /srv/salt/saltpeter/wrapper.py
```

**Option B: Package Installation**
Install Saltpeter package on all minions with the wrapper script included.

**Option C: Dynamic Distribution**
Saltpeter can copy the wrapper to minions before execution (requires modification).

### 3. Configure Firewall

Ensure minions can reach Saltpeter's WebSocket port:

```bash
# On Saltpeter server
firewall-cmd --add-port=8889/tcp --permanent
firewall-cmd --reload
```

### 4. Start Saltpeter with WebSocket

```bash
saltpeter -a -w 8889 --websocket-host 0.0.0.0
```

## Benefits

### 1. Long-Running Process Support
- Salt's timeout issues are eliminated
- Jobs can run for hours or days
- No need for complex Salt job monitoring

### 2. Real-Time Monitoring
- Live output streaming from jobs
- Heartbeat mechanism confirms jobs are still running
- Immediate notification of job completion

### 3. Better Timeout Handling
- Saltpeter-level timeout management
- Graceful timeout with proper logging
- No orphaned Salt jobs

### 4. Reduced Salt Load
- Wrapper exits immediately
- No long-running Salt job processes
- Reduced Salt master/minion overhead

## Troubleshooting

### Wrapper Cannot Connect to WebSocket

**Check connectivity:**
```bash
telnet saltpeter_host 8889
```

**Check firewall:**
```bash
firewall-cmd --list-ports
```

**Check WebSocket server logs:**
```bash
grep "WebSocket" /var/log/saltpeter/*.log
```

### Jobs Not Reporting Results

**Verify wrapper script location:**
```bash
salt '*' cmd.run 'ls -l /path/to/wrapper.py'
```

**Test wrapper manually:**
```bash
python3 /path/to/wrapper.py ws://saltpeter:8889 test_job test_instance $(hostname) 'echo hello'
```

**Check WebSocket connections:**
```bash
netstat -an | grep 8889
```

### Timeout Issues

**Verify timeout configuration in YAML:**
```yaml
timeout: 3600  # Should be sufficient for job duration
```

**Check logs for timeout messages:**
```bash
grep "timeout" /var/log/saltpeter/*.log
```

## Migration from Legacy Salt-Based Execution

The legacy `processresults()` function is still available for backwards compatibility. To use it:

1. Comment out WebSocket server startup in `main()`
2. Modify `run()` to use `processresults()` instead of `processresults_websocket()`
3. Remove wrapper script deployment

However, this is not recommended for production use with long-running jobs.

## Future Enhancements

Potential improvements to the WebSocket architecture:

1. **Encryption**: Add TLS/SSL support for WebSocket connections
2. **Authentication**: Implement token-based authentication for wrappers
3. **Compression**: Compress large output streams
4. **Reconnection**: Handle network interruptions with reconnection logic
5. **Metrics**: Track job performance metrics via WebSocket
6. **Interactive Jobs**: Support for interactive job control (pause/resume)

## API Changes

### Shared State Structure

Jobs now store additional metadata in the shared state:

```python
state[job_name] = {
    'next_run': datetime,
    'last_run': datetime,
    'overlap': bool,
    'group': str,  # New: stored for WebSocket handler
    'targets': [str],
    'results': {
        'machine1': {
            'starttime': datetime,
            'endtime': datetime,
            'ret': str,  # Complete output
            'retcode': int
        }
    }
}
```

### Running Jobs Structure

```python
running[job_instance] = {
    'started': datetime,
    'name': str,
    'machines': [str]  # List of machines still running
}
```

Machines are removed from this list as they complete (reported via WebSocket).
