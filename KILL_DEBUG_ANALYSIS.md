# Kill Issue Debug - WebSocket Connection Closed Prematurely

## Current Symptom

```
WebSocket: Kill command received for job test_streaming
WebSocket: Sent kill signal to test_streaming_1762279644:salt02.opsi.cl
WebSocket: Cleaning up connection - test_streaming_1762279644:salt02.opsi.cl
```

The connection is being cleaned up immediately after sending the kill signal, meaning the wrapper never sends a completion message.

## Possible Causes

1. **Wrapper crashes after receiving kill** - Python exception, signal handling issue
2. **WebSocket connection drops** - Network issue, timeout
3. **Wrapper process terminates** - Gets killed along with subprocess
4. **Exception in wrapper code** - Unhandled exception after break

## Debug Messages Added

The wrapper will now send debug output via WebSocket (since stderr is closed) at key points:

1. `[WRAPPER] Received kill signal` - Kill message received
2. `[WRAPPER] Process terminated, preparing to send completion` - Before breaking from loop
3. `[WRAPPER] Exited main loop, killed=True, retcode=X` - After exiting loop
4. `[WRAPPER] About to send completion message, retcode=143` - Before sending complete
5. `[WRAPPER] Completion message sent successfully` - After sending complete

## What to Look For

### Scenario 1: See "Received kill signal" but nothing else
→ Wrapper crashes during `process.terminate()` or `process.wait()`
→ Possible solution: Add try/except around terminate/wait

### Scenario 2: See "Process terminated" but not "Exited main loop"
→ Something wrong with the break or loop continuation
→ Should be impossible, but check for exception after break

### Scenario 3: See "Exited main loop" but not "About to send completion"
→ Exception in the code between loop and completion
→ Check communicate() call, killed flag handling

### Scenario 4: See "About to send completion" but not "Completion message sent"
→ WebSocket send() is failing or blocking
→ Connection might be closed by server or network

### Scenario 5: See "Completion message sent" but server doesn't log receiving it
→ Message sent but lost in transit
→ Server closed connection before processing message

## Testing

1. Deploy updated wrapper to minions
2. Start a long-running job
3. Kill it via API/UI
4. Check the job output for `[WRAPPER]` debug messages
5. Check Saltpeter logs for WebSocket server messages

## Quick Fix Options

### Option A: Send completion immediately after kill (before cleanup)
Move the completion message send to right after process.terminate(), before any cleanup code.

### Option B: Add try/finally to guarantee completion send
Wrap everything in try/finally and send completion in finally block.

### Option C: Don't wait for process termination
Send completion immediately when kill is received, let process clean up in background.

## Next Steps

1. Run with debug messages
2. See which message is the last one received
3. That tells us exactly where the wrapper is failing
4. Apply appropriate fix based on failure point
