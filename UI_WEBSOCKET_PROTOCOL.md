# UI WebSocket Protocol Documentation

## Overview

The UI WebSocket endpoint provides real-time job status updates with incremental output streaming. It uses position-based tracking to efficiently send only new output chunks, reducing bandwidth and improving performance.

## Connection

**URL**: `ws://<host>:<port>/ws`

**Default Port**: 8081

### Initial Handshake

```javascript
// Connect to WebSocket
const ws = new WebSocket('ws://localhost:8081/ws');

ws.onopen = () => {
    console.log('WebSocket connected');
};
```

## Message Types

### Client → Server

#### 1. Subscribe to Job Updates

Subscribe to receive updates for specific cron jobs.

```javascript
ws.send(JSON.stringify({
    subscribe: ['job1', 'job2', 'job3']
}));
```

**Fields:**
- `subscribe`: Array of cron job names to subscribe to

#### 2. Unsubscribe from Job Updates

Stop receiving updates for specific cron jobs.

```javascript
ws.send(JSON.stringify({
    unsubscribe: ['job1']
}));
```

**Fields:**
- `unsubscribe`: Array of cron job names to unsubscribe from

#### 3. Acknowledge Output Chunk

Acknowledge receipt of output chunk (optional - helps with flow control).

```javascript
ws.send(JSON.stringify({
    ack: {
        cron: 'backup_job',
        machine: 'server1.example.com',
        position: 1024
    }
}));
```

**Fields:**
- `ack.cron`: Job name
- `ack.machine`: Machine identifier
- `ack.position`: Byte position acknowledged

### Server → Client

#### 1. Full Status Update

Sent when job state changes (start, complete) or configuration changes. Excludes full output to save bandwidth.

```javascript
{
    type: 'status',
    cron: 'backup_job',
    machine: 'server1.example.com',
    status: 'running',  // or 'success', 'failed', 'pending'
    retcode: null,      // exit code when complete
    starttime: '2025-11-26T10:30:00Z',
    endtime: '',        // empty while running
    output_length: 1024,  // total bytes of output
    instance: 'abc123'
}
```

**Fields:**
- `type`: Always `'status'`
- `cron`: Job name
- `machine`: Machine identifier
- `status`: Current job state
  - `'pending'`: Not started yet
  - `'running'`: Currently executing
  - `'success'`: Completed with retcode 0
  - `'failed'`: Completed with non-zero retcode
- `retcode`: Exit code (null while running)
- `starttime`: ISO timestamp when job started
- `endtime`: ISO timestamp when job completed (empty string if running)
- `output_length`: Total bytes of output available
- `instance`: Unique job instance identifier

#### 2. Output Chunk

Incremental output data - only new bytes since last position.

```javascript
{
    type: 'output_chunk',
    cron: 'backup_job',
    machine: 'server1.example.com',
    chunk: 'Starting backup...\n',
    position: 0,
    total_length: 1024,
    is_complete: false
}
```

**Fields:**
- `type`: Always `'output_chunk'`
- `cron`: Job name
- `machine`: Machine identifier
- `chunk`: New output data (string)
- `position`: Byte offset where this chunk starts
- `total_length`: Total bytes of output available
- `is_complete`: True if job has finished

#### 3. Configuration Update

Sent when cron configuration changes.

```javascript
{
    type: 'config',
    config: {
        serial: 'config-v123',
        jobs: {
            'backup_job': {
                schedule: '0 2 * * *',
                command: '/usr/local/bin/backup.sh',
                // ... other job config
            }
        }
    }
}
```

**Fields:**
- `type`: Always `'config'`
- `config`: Full configuration object
- `config.serial`: Configuration version identifier

#### 4. Timeline Update

Sent when job execution timeline changes.

```javascript
{
    type: 'timeline',
    timeline: {
        id: 'timeline-v456',
        jobs: {
            'backup_job': {
                'server1.example.com': {
                    status: 'running',
                    starttime: '2025-11-26T10:30:00Z',
                    // ...
                }
            }
        }
    }
}
```

**Fields:**
- `type`: Always `'timeline'`
- `timeline`: Full timeline object
- `timeline.id`: Timeline version identifier

## Implementation Guide

### Basic Connection and Subscription

```javascript
class SaltpeterUI {
    constructor(wsUrl = 'ws://localhost:8081/ws') {
        this.ws = null;
        this.wsUrl = wsUrl;
        this.subscriptions = new Set();
        this.outputPositions = {};  // {cron: {machine: position}}
        this.connect();
    }

    connect() {
        this.ws = new WebSocket(this.wsUrl);
        
        this.ws.onopen = () => {
            console.log('Connected to Saltpeter');
            // Resubscribe to jobs
            if (this.subscriptions.size > 0) {
                this.subscribe([...this.subscriptions]);
            }
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        this.ws.onclose = () => {
            console.log('Disconnected, reconnecting...');
            setTimeout(() => this.connect(), 2000);
        };
    }

    subscribe(jobs) {
        jobs.forEach(job => this.subscriptions.add(job));
        this.ws.send(JSON.stringify({ subscribe: jobs }));
    }

    unsubscribe(jobs) {
        jobs.forEach(job => this.subscriptions.delete(job));
        this.ws.send(JSON.stringify({ unsubscribe: jobs }));
    }

    handleMessage(data) {
        switch(data.type) {
            case 'status':
                this.handleStatus(data);
                break;
            case 'output_chunk':
                this.handleOutputChunk(data);
                break;
            case 'config':
                this.handleConfig(data);
                break;
            case 'timeline':
                this.handleTimeline(data);
                break;
        }
    }
}
```

### Handling Output Chunks

```javascript
handleOutputChunk(data) {
    const { cron, machine, chunk, position, total_length, is_complete } = data;
    
    // Initialize tracking
    if (!this.outputPositions[cron]) {
        this.outputPositions[cron] = {};
    }
    if (!this.outputPositions[cron][machine]) {
        this.outputPositions[cron][machine] = 0;
    }

    // Verify position matches expected
    const expected = this.outputPositions[cron][machine];
    if (position !== expected) {
        console.warn(`Position mismatch: expected ${expected}, got ${position}`);
        // Could request full output refresh here
    }

    // Update position
    this.outputPositions[cron][machine] = position + chunk.length;

    // Append chunk to display
    this.appendOutput(cron, machine, chunk);

    // Optional: Send ACK
    this.ws.send(JSON.stringify({
        ack: {
            cron: cron,
            machine: machine,
            position: this.outputPositions[cron][machine]
        }
    }));

    if (is_complete) {
        console.log(`Job ${cron} on ${machine} completed`);
    }
}
```

### Handling Status Updates

```javascript
handleStatus(data) {
    const { cron, machine, status, retcode, starttime, endtime, output_length } = data;
    
    // Update UI with job status
    this.updateJobStatus(cron, machine, {
        status: status,
        retcode: retcode,
        starttime: starttime,
        endtime: endtime,
        outputLength: output_length
    });

    // Reset position tracking when job starts
    if (status === 'running' && !endtime) {
        if (!this.outputPositions[cron]) {
            this.outputPositions[cron] = {};
        }
        this.outputPositions[cron][machine] = 0;
    }
}
```

### Handling Configuration Updates

```javascript
handleConfig(data) {
    const { config } = data;
    console.log('Configuration updated:', config.serial);
    
    // Update job list in UI
    this.updateJobList(config.jobs);
}
```

### Handling Timeline Updates

```javascript
handleTimeline(data) {
    const { timeline } = data;
    console.log('Timeline updated:', timeline.id);
    
    // Update job execution timeline
    this.updateTimeline(timeline.jobs);
}
```

## Message Flow Examples

### Subscribing and Receiving Updates

```
Client → Server:  {"subscribe": ["backup_job"]}

Server → Client:  {"type": "status", "cron": "backup_job", ...}
Server → Client:  {"type": "output_chunk", "cron": "backup_job", "chunk": "...", "position": 0, ...}
Server → Client:  {"type": "output_chunk", "cron": "backup_job", "chunk": "...", "position": 100, ...}

Client → Server:  {"ack": {"cron": "backup_job", "machine": "server1", "position": 200}}
```

### Job Execution Flow

1. **Job Starts**
   ```javascript
   {
       type: 'status',
       cron: 'backup_job',
       machine: 'server1',
       status: 'running',
       retcode: null,
       starttime: '2025-11-26T10:30:00Z',
       endtime: '',
       output_length: 0
   }
   ```

2. **Output Chunks** (sent as output arrives)
   ```javascript
   {
       type: 'output_chunk',
       cron: 'backup_job',
       machine: 'server1',
       chunk: 'Starting backup...\n',
       position: 0,
       total_length: 19,
       is_complete: false
   }
   ```

3. **Job Completes**
   ```javascript
   {
       type: 'status',
       cron: 'backup_job',
       machine: 'server1',
       status: 'success',
       retcode: 0,
       starttime: '2025-11-26T10:30:00Z',
       endtime: '2025-11-26T10:35:00Z',
       output_length: 1024
   }
   ```

   ```javascript
   {
       type: 'output_chunk',
       cron: 'backup_job',
       machine: 'server1',
       chunk: 'Backup complete.\n',
       position: 1008,
       total_length: 1024,
       is_complete: true
   }
   ```

## Position Tracking

Output is sent incrementally using byte positions:

- **Position 0**: Start of output
- **Position N**: Byte offset from start
- Client tracks `last_position` per job/machine
- Server sends only `output[last_position:]` as new chunks
- `total_length` shows complete output size
- `is_complete` indicates job has finished

### Handling Position Mismatches

If position doesn't match expected:
1. Log warning
2. Consider requesting full refresh (unsubscribe + resubscribe)
3. Or adjust local position and continue

## Broadcast Interval

Server broadcasts updates every **2 seconds** to all connected clients. Only changed data is sent:
- Config: Only when serial changes
- Timeline: Only when id changes  
- Output chunks: Only new output since last position

## Connection Management

### Reconnection

```javascript
ws.onclose = () => {
    console.log('Connection lost, reconnecting...');
    setTimeout(() => {
        this.connect();
        // Resubscribe to jobs after reconnect
        if (this.subscriptions.size > 0) {
            this.subscribe([...this.subscriptions]);
        }
    }, 2000);
};
```

### Cleanup on Unload

```javascript
window.addEventListener('beforeunload', () => {
    if (this.ws) {
        this.ws.close();
    }
});
```

## Performance Considerations

1. **Incremental streaming**: Only new output chunks sent, not full output every time
2. **Position tracking**: Client maintains position per job/machine independently
3. **Selective updates**: Config/timeline only sent when changed
4. **Subscription filtering**: Only receive updates for subscribed jobs
5. **ACK optional**: Sending ACKs is optional, helps with flow control but not required

## Error Handling

- **Connection loss**: Auto-reconnect with exponential backoff
- **Position mismatch**: Log and optionally refresh
- **Invalid JSON**: Log and skip message
- **Missing fields**: Validate and use defaults

## Security Notes

- WebSocket runs on same host as HTTP API
- No authentication in protocol (handle at network/proxy level)
- Validate all incoming data
- Sanitize output before displaying in HTML
