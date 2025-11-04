# Debugging Kill Functionality - State Not Updating

## Problem
When a job is killed via the UI/API, the process terminates successfully but Saltpeter shows the job as running forever.

## Root Cause Analysis

The issue is likely related to how the wrapper handles the kill signal and sends the completion message.

### Potential Issues

1. **Pipe Blocking**: After `process.terminate()`, calling `process.communicate()` might block or fail because the pipes are in an undefined state
2. **Message Not Sent**: The wrapper might encounter an exception before sending the complete message
3. **State Not Updated**: The WebSocket server might not be updating the state correctly

## Changes Made for Debugging

### 1. Wrapper (`wrapper.py`)
- Close stdout/stderr pipes BEFORE terminating process to avoid blocking on communicate()
- Skip `communicate()` call if process was killed (pipes already closed)
- Added extensive logging (note: stderr is closed after fork, so logs won't appear)

### 2. Main Process (`main.py`)
- Added logging when targets are marked as completed
- Added logging when all targets complete and monitor loop exits

### 3. WebSocket Server (`websocket_server.py`)
- Added logging when complete message is received
- Added logging when state is updated with endtime

## How to Test

1. Start Saltpeter with a long-running job:
   ```yaml
   test_sleep:
     min: '*'
     hour: '*'
     dom: '*'
     mon: '*'
     dow: '*'
     targets: '*'
     target_type: 'glob'
     command: 'sleep 300'
   ```

2. Wait for job to start
3. Send kill command via API
4. Check logs for these messages:

   **Expected in WebSocket server logs:**
   ```
   WebSocket: Kill command received for job test_sleep
   WebSocket: Sent kill signal to test_sleep_XXX:hostname
   WebSocket: Received complete message from test_sleep_XXX:hostname, retcode=143
   WebSocket: Updated state for test_sleep[hostname] with endtime=2025-11-04..., retcode=143
   ```

   **Expected in main process logs:**
   ```
   WebSocket: Target hostname completed, removing from pending (retcode: 143)
   WebSocket: All targets completed for test_sleep, exiting monitor loop
   ```

## What to Look For

### If you see "Received complete message" but NOT "Updated state"
- State locking issue
- job_name mismatch
- statelocks not initialized

### If you see "Updated state" but NOT "Target completed"
- The endtime might be empty string instead of datetime object
- State locking preventing read
- Monitor loop not checking frequently enough

### If you don't see "Received complete message"
- Wrapper encountering exception before sending
- WebSocket connection closed prematurely
- Wrapper crash during kill handling

## Quick Fix to Try

If the issue is that `communicate()` is blocking after kill, the changes in this commit should fix it by:
1. Closing pipes before terminate
2. Skipping communicate() if killed
3. This prevents any blocking on dead pipes

## Alternative Approach

If the above doesn't work, we could:
1. Make wrapper send completion message immediately after kill (before cleanup)
2. Use a timeout on communicate() call
3. Send completion in a finally block to ensure it always happens
