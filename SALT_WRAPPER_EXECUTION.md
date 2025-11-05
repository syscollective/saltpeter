# Salt Wrapper Execution and Monitoring

## Overview

Saltpeter now uses a two-phase approach for monitoring wrapper-based jobs to handle both Salt execution failures and job execution properly.

## Two-Phase Monitoring

### Phase 1: Salt Execution Confirmation (Indefinite Wait)

**Purpose:** Wait for Salt to confirm the wrapper script was executed successfully on each minion.

**Behavior:**
- Waits indefinitely for Salt to return execution status
- No timeout during this phase (handles busy Salt masters)
- Checks `retcode` from Salt's `cmd.run`:
  - `retcode == 0`: Wrapper started successfully (fork succeeded)
  - `retcode != 0`: Wrapper failed to execute (file not found, permission error, etc.)

**Why indefinite wait?**
- Salt master might be busy processing other jobs
- Network latency varies
- We should never mark a job as failed if Salt hasn't confirmed execution yet
- Better to wait for Salt than to incorrectly fail a job that might be running

**Detected Errors (Phase 1):**
- Wrapper script not found (`/usr/local/bin/sp_wrapper.py: No such file or directory`)
- Permission denied (wrapper not executable)
- Python not available on minion
- Any other Salt execution error

**Example Output:**
```
Waiting for Salt to confirm wrapper execution for job 20231105123456789...
Salt confirmed wrapper started on salt01.example.com
Wrapper execution failed on salt02.example.com (retcode=127): /usr/local/bin/sp_wrapper.py: No such file or directory
Monitoring WebSocket results for 1 confirmed target(s)...
```

### Phase 2: WebSocket Job Monitoring (With Timeout)

**Purpose:** Monitor actual job execution via WebSocket for targets where wrapper started successfully.

**Behavior:**
- Only monitors targets that passed Phase 1 (Salt confirmed execution)
- Heartbeat timer starts AFTER Salt confirmation (not before)
- Applies configured timeout to actual job execution
- Monitors for:
  - Heartbeat timeout (15 seconds)
  - Job completion via WebSocket
  - Configured job timeout

**Why start timer after confirmation?**
- Avoids false timeouts due to busy Salt master
- Heartbeat timeout only applies to actual job execution
- Fair timeout measurement (from when job actually started, not when submitted)

## Execution Flow

```
1. Submit job to Salt
   └─> salt.run_job(targets, 'cmd.run', [wrapper_path], ...)

2. PHASE 1: Wait for Salt confirmation (indefinite)
   ├─> Poll salt.get_cli_returns(job_id, targets, timeout=5)
   ├─> For each target:
   │   ├─> retcode == 0: Wrapper started ✓
   │   │   └─> Add to confirmed_targets
   │   │   └─> Initialize heartbeat timer
   │   └─> retcode != 0: Wrapper failed ✗
   │       └─> Log error with actual error message
   │       └─> Remove from pending targets
   └─> Continue until all targets confirmed or failed

3. PHASE 2: Monitor WebSocket (with timeout)
   ├─> Only monitor confirmed_targets
   ├─> Start job execution timeout clock
   ├─> Monitor for:
   │   ├─> WebSocket completion messages
   │   ├─> Heartbeat timeout (15s from last heartbeat)
   │   └─> Job timeout (from job start, not Salt submission)
   └─> Exit when all confirmed targets complete
```

## Exit Codes

| Exit Code | Source | Meaning |
|-----------|--------|---------|
| 0 | Job | Success |
| 1-123 | Job | Application-specific errors |
| 124 | Saltpeter | Job timeout (exceeded configured timeout) |
| 126 | Salt | Permission denied (wrapper not executable) |
| 127 | Salt | Command not found (wrapper missing) |
| 143 | Wrapper | Killed by user request |
| 253 | Saltpeter | Heartbeat timeout (wrapper lost connection) |
| 255 | Salt/Saltpeter | Generic error (target didn't respond) |

## Benefits

### 1. No False Failures from Busy Salt Master
- Jobs won't be marked as failed just because Salt master is slow
- Indefinite wait in Phase 1 ensures we get actual execution status
- Heartbeat timer only starts when job is actually running

### 2. Immediate Failure Detection
- Wrapper missing: Detected in seconds (when Salt returns error)
- No waiting 15 seconds for heartbeat timeout
- Actual error message from Salt shown to user

### 3. Fair Timeout Handling
- Timeout measured from actual job start, not submission
- Long Salt queue times don't count against job timeout
- Job gets full timeout duration to complete

### 4. Clear Error Messages
```
# Before (heartbeat timeout):
[SALTPETER ERROR: Job lost connection - no heartbeat for 15 seconds]

# After (immediate detection):
Wrapper execution failed:
/usr/local/bin/sp_wrapper.py: No such file or directory
```

## Configuration

No configuration changes needed. The two-phase approach is automatic when using wrapper mode:

```yaml
my_job:
  command: /path/to/script.sh
  use_wrapper: true   # Two-phase monitoring enabled
  timeout: 3600       # Timeout applies to Phase 2 only
  # ...other config
```

To use legacy mode (skip both phases):
```yaml
legacy_job:
  command: /path/to/script.sh
  use_wrapper: false  # Legacy Salt-only mode
  timeout: 3600
  # ...other config
```

## Troubleshooting

### Symptom: Job waits forever, never starts
**Cause:** Salt master not responding or minion down
**Check:** 
```bash
salt 'minion-name' test.ping
salt-run jobs.list_jobs
```

### Symptom: Wrapper execution failed with retcode 127
**Cause:** Wrapper script not found on minion
**Solution:**
```bash
# Deploy wrapper
salt 'minion-name' cp.get_file salt://sp_wrapper.py /usr/local/bin/sp_wrapper.py
salt 'minion-name' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'

# Or use legacy mode
use_wrapper: false
```

### Symptom: Wrapper execution failed with retcode 126
**Cause:** Wrapper not executable
**Solution:**
```bash
salt 'minion-name' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'
```

### Symptom: Heartbeat timeout after job starts
**Cause:** Network issue between wrapper and WebSocket server
**Check:**
- Firewall rules (WebSocket port open?)
- WebSocket server running?
- Network connectivity from minion to master

## Logs

### Phase 1 - Salt Execution
```
Firing test_job_1762346049!
Waiting for Salt to confirm wrapper execution for job 20231105123456789...
Salt confirmed wrapper started on salt01.example.com
Salt confirmed wrapper started on salt03.example.com
Wrapper execution failed on salt02.example.com (retcode=127): /usr/local/bin/sp_wrapper.py: No such file or directory
Monitoring WebSocket results for 2 confirmed target(s)...
```

### Phase 2 - WebSocket Monitoring
```
WebSocket: Client connected - 1762346049:salt01.example.com
WebSocket: Job started - 1762346049:salt01.example.com (PID: 12345)
WebSocket: Heartbeat from 1762346049:salt01.example.com at 2023-11-05T12:35:00+00:00
WebSocket: Job completed - 1762346049:salt01.example.com (exit code: 0)
WebSocket: All targets completed for test_job, exiting monitor loop
Deleting process test_job_1762346049 as it must have finished
```

## Migration from Previous Version

No changes needed! The new two-phase approach is backward compatible:
- Existing jobs continue to work
- Wrapper failures detected faster
- No configuration changes required
- Better handling of busy Salt masters

The only visible difference is faster failure detection and more accurate error messages.
