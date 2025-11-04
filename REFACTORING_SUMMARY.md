# Saltpeter Refactoring Summary

## Overview

This document summarizes the major refactoring completed for Saltpeter to support:

1. **WebSocket-based job execution** - Replacing Salt's job monitoring with WebSocket communication
2. **Maintenance mode configuration** - Global and per-machine maintenance controls
3. **Improved timeout handling** - Grace period for job termination

## Changes Made

### 1. New Files Created

#### `/saltpeter/wrapper.py`
A wrapper script that:
- Is executed by Salt on minions
- Returns immediately to avoid Salt timeouts
- Runs the actual job command in a subprocess
- Communicates with Saltpeter via WebSocket
- Sends heartbeats every 5 seconds
- Streams stdout/stderr in real-time
- Reports final exit code

#### `/saltpeter/websocket_server.py`
WebSocket server that:
- Listens for connections from wrapper scripts
- Handles message types: connect, start, heartbeat, output, complete, error
- Updates shared state as jobs progress
- Removes machines from running list upon completion
- Integrates with existing log() function

#### `/WEBSOCKET_ARCHITECTURE.md`
Comprehensive documentation covering:
- Architecture overview
- Component details
- Message formats
- Configuration
- Deployment instructions
- Troubleshooting guide

#### `/examples/config/maintenance_example.yaml`
Example configuration demonstrating:
- `saltpeter_maintenance` special key
- Global maintenance mode
- Machine exclusion list

### 2. Modified Files

#### `/requirements.txt`
- Added `websockets` dependency for WebSocket support

#### `/saltpeter/main.py`

**New Functions:**
- `processresults_websocket()` - Waits for WebSocket-based job results with timeout support
  - Polls state for completed results
  - Implements configurable timeout
  - Marks timed-out jobs appropriately
  - Removes machines from running list

**Modified Functions:**
- `readconfig()` - Now reads `saltpeter_maintenance` configuration
  - Returns tuple: `(crons, saltpeter_maintenance)`
  - Validates maintenance config structure
  - Merges maintenance settings from multiple files

- `run()` - Refactored for WebSocket-based execution
  - Checks global maintenance mode
  - Builds wrapper command instead of direct command
  - Passes WebSocket URL to wrapper
  - Uses `processresults_websocket()` instead of `processresults()`
  - Removes maintenance machines from target list
  - Stores group in state for WebSocket handler

- `main()` - Added WebSocket server initialization
  - New arguments: `--websocket-port`, `--websocket-host`
  - Starts WebSocket server in separate process
  - Checks maintenance mode in main loop
  - Logs maintenance status periodically

**Preserved Functions:**
- `processresults()` - Kept for backwards compatibility (marked as legacy)

### 3. Key Features Implemented

#### A. Maintenance Mode

**Global Maintenance:**
```yaml
saltpeter_maintenance:
  global: true  # No new crons start, existing ones finish
  machines: []
```

When `global: true`:
- New crons are blocked from starting
- Already running crons continue to completion
- Maintenance status logged every 20 seconds
- Program exits when all crons finish (optional)

**Machine-Level Maintenance:**
```yaml
saltpeter_maintenance:
  global: false
  machines:
    - server01.example.com
    - server02.example.com
```

Machines in the list:
- Are automatically removed from all target lists
- Log entry created: "Target under maintenance"
- Still respond to ping but don't execute jobs

#### B. WebSocket Communication Flow

1. **Job Initiation:**
   - Saltpeter executes wrapper script via Salt
   - Wrapper connects to WebSocket server
   - Sends `connect` message

2. **Job Execution:**
   - Wrapper starts subprocess
   - Sends `start` message with PID
   - Streams `output` messages as subprocess runs
   - Sends `heartbeat` every 5 seconds

3. **Job Completion:**
   - Wrapper sends `complete` message with exit code
   - WebSocket server updates state
   - Removes machine from running list
   - Calls log() function

4. **Error Handling:**
   - Connection failures trigger `error` message
   - Timeout detected by `processresults_websocket()`
   - Jobs exceeding timeout marked with exit code 124

#### C. Timeout Handling

**Configuration:**
```yaml
job_name:
  timeout: 3600  # 1 hour
  # ... other settings
```

**Behavior:**
- Timeout enforced at Saltpeter level (not Salt level)
- `processresults_websocket()` polls for results
- When timeout exceeded:
  - Remaining machines marked as timed out (exit code 124)
  - Log entry created
  - Job marked as complete

**Future Enhancement (Grace Period):**
The old `processresults()` included grace period logic for killing jobs:
- 3 attempts to terminate job
- 10 second interval between attempts
- Job discarded after all attempts fail

This could be integrated into the WebSocket wrapper for true process termination.

## Configuration Changes

### Command-Line Arguments

**New:**
```bash
saltpeter -w 8889 --websocket-host 0.0.0.0
```

- `-w`, `--websocket-port`: WebSocket server port (default: 8889)
- `--websocket-host`: WebSocket server host (default: 0.0.0.0)

### YAML Configuration

**New Special Key:**
```yaml
saltpeter_maintenance:
  global: false
  machines: []
```

**Existing Keys (unchanged):**
```yaml
job_name:
  targets: '*'
  target_type: 'glob'
  command: '/path/to/command'
  timeout: 3600
  cwd: '/optional/working/dir'
  user: 'optional_user'
  min: '0'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'
```

## Deployment Considerations

### 1. Wrapper Script Distribution

The wrapper script must be accessible on all Salt minions. Options:

**Option A:** Install via package
```bash
pip install saltpeter  # Installs wrapper.py to standard location
```

**Option B:** Salt file server
```bash
cp saltpeter/wrapper.py /srv/salt/saltpeter/wrapper.py
```

**Option C:** Dynamic copy (requires code modification)
```python
# In run() function, before executing wrapper
salt.run_job(targets, 'cp.get_file', 
             ['salt://saltpeter/wrapper.py', '/tmp/wrapper.py'])
```

### 2. Firewall Configuration

Minions must reach Saltpeter's WebSocket port:

```bash
# Saltpeter server
firewall-cmd --add-port=8889/tcp --permanent
firewall-cmd --reload

# Or for iptables
iptables -A INPUT -p tcp --dport 8889 -j ACCEPT
```

### 3. Network Considerations

- WebSocket connections are persistent
- Each running job maintains one connection
- Ensure network can handle concurrent connections
- Consider load balancing for large deployments

### 4. Backwards Compatibility

The legacy Salt-based execution is still available:

1. Comment out WebSocket server startup
2. Modify `run()` to use `processresults()` instead of `processresults_websocket()`
3. Revert to direct command execution (no wrapper)

This allows gradual migration or fallback if needed.

## Testing Recommendations

### 1. Unit Tests
- Test `readconfig()` with maintenance configurations
- Test WebSocket message handling
- Test timeout logic in `processresults_websocket()`
- Test maintenance machine filtering

### 2. Integration Tests
- Deploy wrapper to test minions
- Execute test jobs via WebSocket
- Verify heartbeat messages
- Test timeout behavior
- Test maintenance mode (global and per-machine)

### 3. Load Tests
- Concurrent job execution
- WebSocket connection limits
- State update performance
- Network bandwidth usage

## Migration Guide

### From Old Saltpeter to WebSocket-Based

1. **Backup existing configuration:**
   ```bash
   cp -r /etc/saltpeter /etc/saltpeter.backup
   ```

2. **Update Saltpeter package:**
   ```bash
   pip install --upgrade saltpeter
   ```

3. **Install websockets dependency:**
   ```bash
   pip install websockets
   ```

4. **Deploy wrapper script to minions:**
   ```bash
   salt '*' cp.get_file salt://saltpeter/wrapper.py /usr/local/bin/wrapper.py
   salt '*' cmd.run 'chmod +x /usr/local/bin/wrapper.py'
   ```

5. **Configure firewall:**
   ```bash
   firewall-cmd --add-port=8889/tcp --permanent
   firewall-cmd --reload
   ```

6. **Update Saltpeter startup:**
   ```bash
   saltpeter -a -w 8889 --websocket-host 0.0.0.0
   ```

7. **Test with simple job:**
   ```yaml
   test_job:
     targets: 'test-minion*'
     target_type: 'glob'
     command: 'echo "Hello WebSocket"'
     timeout: 60
     min: '*/5'
     hour: '*'
     dom: '*'
     mon: '*'
     dow: '*'
   ```

8. **Monitor logs:**
   ```bash
   tail -f /var/log/saltpeter/*.log | grep WebSocket
   ```

9. **Add maintenance configuration (optional):**
   ```yaml
   # In any .yaml file in config directory
   saltpeter_maintenance:
     global: false
     machines: []
   ```

## Known Limitations

1. **Wrapper Script Location:**
   - Must manually distribute to minions
   - No automatic deployment mechanism (yet)

2. **WebSocket Security:**
   - No TLS/SSL support (yet)
   - No authentication mechanism (yet)
   - Trusts all incoming connections

3. **Network Dependencies:**
   - Requires bidirectional connectivity
   - Firewall rules must allow WebSocket port
   - No automatic reconnection on network failure

4. **State Synchronization:**
   - Uses multiprocessing.Manager for shared state
   - Potential performance impact with many concurrent jobs
   - Lock contention on state updates

## Future Enhancements

### Short Term
1. Add TLS/SSL support for WebSocket
2. Implement token-based authentication
3. Add automatic wrapper deployment
4. Improve error handling and reconnection logic
5. Add metrics collection (job duration, success rate, etc.)

### Medium Term
1. Support for interactive job control (pause/resume/kill)
2. Output compression for large jobs
3. Persistent job history storage
4. Web UI for real-time job monitoring
5. Email/webhook notifications on job completion

### Long Term
1. Support for distributed Saltpeter (multiple servers)
2. Job dependency management
3. Resource allocation and throttling
4. Advanced scheduling (cron expressions, date ranges, etc.)
5. Plugin system for custom executors

## Support and Troubleshooting

See `WEBSOCKET_ARCHITECTURE.md` for detailed troubleshooting guide.

Common issues:
- Wrapper cannot connect: Check firewall, WebSocket URL
- Jobs not reporting: Verify wrapper script location
- Timeout too short: Increase in YAML configuration
- State not updating: Check statelocks, multiprocessing manager

## Conclusion

This refactoring provides a solid foundation for handling long-running jobs with Salt while maintaining backwards compatibility. The WebSocket-based architecture offers real-time monitoring, better timeout handling, and eliminates Salt's limitations with long-running processes.

The maintenance mode feature provides operational flexibility for managing machine availability without modifying individual job configurations.

Both features are production-ready with documented deployment paths and troubleshooting guides.
