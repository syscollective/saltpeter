# Heartbeat Timeout Bug Fix

## The Bug

The heartbeat timeout monitoring was **completely broken** and would never trigger.

### Root Cause

In `processresults_websocket()`, the code was updating the heartbeat timer like this:

```python
# Update last heartbeat time if we have activity
if result.get('ret') or result.get('starttime'):
    last_heartbeat[tgt] = time.time()
```

**Problem**: This resets the heartbeat timer whenever there's ANY output or a starttime in the state. Since jobs always have output after they start, the timer was being reset on every check (every 1 second), even if no actual heartbeat messages were being sent!

This meant the heartbeat timeout would **never trigger**, even if the wrapper completely stopped sending heartbeats.

## The Fix

### 1. WebSocket Server - Store Heartbeat Time in State

When a heartbeat message is received, store the timestamp in the state:

```python
elif msg_type == 'heartbeat':
    # ... existing code ...
    
    # Update state with last heartbeat time
    if job_name in self.state and self.statelocks and job_name in self.statelocks:
        with self.statelocks[job_name]:
            tmpstate = self.state[job_name].copy()
            if 'results' not in tmpstate:
                tmpstate['results'] = {}
            if machine not in tmpstate['results']:
                tmpstate['results'][machine] = {}
            tmpstate['results'][machine]['last_heartbeat'] = timestamp
            self.state[job_name] = tmpstate
```

### 2. Monitoring Process - Read Actual Heartbeat Time

Read the `last_heartbeat` timestamp from state (set by WebSocket server):

```python
# Update last heartbeat time from state (set by WebSocket server)
if 'last_heartbeat' in result and result['last_heartbeat']:
    # Convert ISO timestamp to Unix timestamp
    try:
        hb_time = result['last_heartbeat']
        if isinstance(hb_time, str):
            hb_time = datetime.fromisoformat(hb_time)
        if isinstance(hb_time, datetime):
            last_heartbeat[tgt] = hb_time.timestamp()
    except:
        pass

# Initialize heartbeat timer on first check if we have a starttime
if tgt not in last_heartbeat and result.get('starttime'):
    try:
        start = result['starttime']
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(start, datetime):
            last_heartbeat[tgt] = start.timestamp()
    except:
        last_heartbeat[tgt] = time.time()
```

## How It Works Now

1. **Wrapper sends heartbeat** every 5 seconds
2. **WebSocket server receives it** and stores timestamp in `state[job]['results'][machine]['last_heartbeat']`
3. **Monitoring process reads** the `last_heartbeat` from state
4. **Checks if** `time.time() - last_heartbeat > 15` seconds
5. **If yes**, marks job as failed with retcode 253

## Testing

### Test Heartbeat Timeout

1. Start a long-running job
2. Kill the wrapper process (not via API, just kill the process)
   ```bash
   # On the minion
   ps aux | grep sp_wrapper
   kill -9 <pid>
   ```
3. Wait 15 seconds
4. Job should be marked as failed with retcode 253
5. Output should contain: `[SALTPETER ERROR: Job lost connection - no heartbeat for X seconds]`

### Test Normal Heartbeat

1. Start a long-running job
2. Don't kill it
3. Job should continue running normally
4. Heartbeats should appear in logs every 5 seconds: `WebSocket: Heartbeat from job_XXX:hostname`

## Why This Also Helps with Kill Issue

When we kill a job and the wrapper crashes before sending completion:

1. Wrapper stops sending heartbeats
2. After 15 seconds, monitoring detects heartbeat timeout
3. Job is marked as failed with retcode 253
4. Job is removed from running list
5. **UI shows job as completed (even if with error)**

So even if the kill completion message never arrives, the job will be cleaned up within 15 seconds.

## Exit Codes

- `143` - Killed successfully by user (wrapper sends completion)
- `253` - Heartbeat timeout (wrapper crashed, monitoring detects it)
- `124` - Hard timeout exceeded
- `0` - Success
- `255` - Error

## Related Files

- `saltpeter/websocket_server.py` - Stores heartbeat time in state
- `saltpeter/main.py` - Reads heartbeat time and checks timeout
- `saltpeter/wrapper.py` - Sends heartbeat every 5 seconds
