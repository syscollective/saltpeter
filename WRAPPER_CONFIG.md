# Wrapper Configuration Guide

## Overview

Saltpeter supports two modes of job execution:

1. **Wrapper Mode** (default, recommended) - Uses WebSocket-based wrapper for real-time monitoring
2. **Legacy Mode** - Direct Salt execution without wrapper (fallback for compatibility)

## Configuration Options

### `use_wrapper` (boolean, default: `true`)

Controls whether to use the WebSocket wrapper or legacy Salt-only execution.

**Example:**
```yaml
my_job:
  command: /path/to/script.sh
  targets: '*'
  target_type: glob
  min: '*/5'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'
  use_wrapper: true  # Use WebSocket wrapper (default)
```

### When to Use Legacy Mode (`use_wrapper: false`)

Use legacy mode when:
- Wrapper cannot be installed on target minions
- Network restrictions prevent WebSocket connections
- Testing or debugging Salt integration
- Compatibility with older minion versions

**Example:**
```yaml
legacy_job:
  command: /path/to/script.sh
  targets: 'old-minion*'
  target_type: glob
  min: '0'
  hour: '*/2'
  dom: '*'
  mon: '*'
  dow: '*'
  use_wrapper: false  # Use legacy Salt-only mode
  timeout: 300        # Timeout handled by Salt in legacy mode
```

## Immediate Failure Detection

### Wrapper Mode

When using the wrapper, Saltpeter now detects immediate failures such as:
- **Wrapper script not found** - Returns error immediately with exit code and error message
- **Permission errors** - Detected within 2 seconds
- **Salt execution errors** - Any error from `cmd.run` is captured immediately

**Example Error Output:**
```
Salt execution error:
/usr/local/bin/sp_wrapper.py: No such file or directory
```

**Exit Code:** Same as returned by Salt (usually 127 for "command not found", 126 for "permission denied")

### Legacy Mode

In legacy mode, failures are detected through standard Salt mechanisms:
- Timeout after configured timeout period
- Standard Salt error handling
- Exit code 255 for non-responding targets

## Failure Modes and Exit Codes

| Exit Code | Meaning | When It Occurs |
|-----------|---------|----------------|
| 0 | Success | Job completed successfully |
| 1-123 | Command failure | Command returned error (application-specific) |
| 124 | Timeout | Job exceeded configured timeout |
| 126 | Permission denied | Cannot execute wrapper/command (permissions) |
| 127 | Command not found | Wrapper script or command not found |
| 143 | Killed by user | Job terminated via kill command |
| 253 | Heartbeat timeout | Wrapper lost connection (15 seconds no heartbeat) |
| 255 | Generic error | Target didn't respond or unknown error |

## Heartbeat Monitoring (Wrapper Mode Only)

When using wrapper mode:
- Wrapper sends heartbeat every 5 seconds
- If no heartbeat for 15 seconds (3 missed beats), job marked as failed with exit code 253
- Heartbeat timeout provides safety net even if WebSocket connection fails

**Not applicable in legacy mode** - legacy mode uses Salt's timeout mechanism only.

## WebSocket Retry Logic (Wrapper Mode Only)

The wrapper continuously retries WebSocket connections while the job runs:
- Retry interval: 2 seconds
- Job subprocess runs independently of WebSocket state
- Messages queued when disconnected, sent when reconnected
- Completion retried for up to 60 seconds after job finishes

**Benefits:**
- Jobs complete even if WebSocket server temporarily unavailable
- No data loss during network issues
- Resilient to server restarts

## Best Practices

### Recommended Approach
1. **Default to wrapper mode** for all new jobs
2. **Use legacy mode** only when wrapper installation is impossible
3. **Monitor logs** for immediate failure detection
4. **Set appropriate timeouts** based on expected job duration

### Mixed Environment
You can use both modes simultaneously:

```yaml
# Wrapper mode for modern infrastructure
modern_job:
  command: /opt/app/task.sh
  targets: 'server*'
  target_type: glob
  use_wrapper: true
  min: '*/10'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'

# Legacy mode for legacy systems
legacy_system_job:
  command: /usr/bin/old_script
  targets: 'legacy-host'
  target_type: list
  use_wrapper: false
  timeout: 600
  min: '0'
  hour: '3'
  dom: '*'
  mon: '*'
  dow: '*'
```

### Troubleshooting

**Symptom:** Job fails immediately with "No such file or directory"
- **Cause:** Wrapper not installed on minion
- **Solution:** Install wrapper or set `use_wrapper: false`

**Symptom:** Job fails after 15 seconds with exit code 253
- **Cause:** Heartbeat timeout - wrapper can't reach WebSocket server
- **Solution:** Check network connectivity, firewall rules, WebSocket server status

**Symptom:** Jobs stuck "running forever"
- **Legacy Mode:** Check Salt job status, minion connectivity
- **Wrapper Mode:** Should timeout after 15 seconds with heartbeat failure

## Migration from Legacy to Wrapper

To migrate existing jobs:

1. **Install wrapper** on all target minions:
   ```bash
   salt '*' cp.get_file salt://sp_wrapper.py /usr/local/bin/sp_wrapper.py
   salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'
   ```

2. **Test with one job** first:
   ```yaml
   test_job:
     use_wrapper: true  # Add this line
     # ... rest of config unchanged
   ```

3. **Monitor for issues** in logs and output

4. **Gradually migrate** remaining jobs

5. **Keep `use_wrapper: false`** for any systems where wrapper can't be installed

## Configuration Examples

See `examples/config/` directory for complete examples:
- `example.yaml` - Wrapper mode examples
- `example2.yaml` - Mixed wrapper and legacy modes
- `example3.yaml` - Advanced configurations
