# Buffer Retention Strategy

## Overview

The wrapper now retains output data in `output_buffer` until the server explicitly acknowledges receipt via ACK messages. This enables message retransmission if the server loses data or requests earlier sequences.

## Architecture

### Data Structures

1. **`output_buffer`**: List of `(timestamp, line)` tuples containing raw output
2. **`seq_to_buffer_map`**: Maps sequence number to absolute buffer position (how much data was included up to that seq)
3. **`buffer_cleared_up_to`**: Absolute position tracking how many buffer items have been cleared after ACK

### Buffer Lifecycle

```
1. Output arrives → added to output_buffer
2. Time/size trigger → convert buffer to messages with seq numbers
3. Track seq → buffer_position mapping
4. Send first message, wait for ACK
5. Receive ACK → clear buffer up to acked position
6. Repeat
```

## Implementation Details

### Creating Messages

When sending buffered output:
```python
# Combine all buffered output
combined_output = ''.join(line for _, line in output_buffer)

# Create messages (may be chunked)
output_messages, next_seq = create_output_messages(combined_output, next_seq)

# Map each sequence to buffer end position
current_buffer_end = buffer_cleared_up_to + len(output_buffer)
for msg in output_messages:
    seq_to_buffer_map[msg['seq']] = current_buffer_end
```

### Clearing Buffer After ACK

```python
# Find highest buffer position covered by acked sequences
clear_up_to = buffer_cleared_up_to
for seq in sorted([s for s in seq_to_buffer_map.keys() if s <= acked_seq]):
    if seq in seq_to_buffer_map:
        clear_up_to = max(clear_up_to, seq_to_buffer_map[seq])

# Clear buffer items
if clear_up_to > buffer_cleared_up_to:
    items_to_clear = clear_up_to - buffer_cleared_up_to
    output_buffer = output_buffer[items_to_clear:]
    buffer_cleared_up_to = clear_up_to
    # Adjust sequence map indices
    seq_to_buffer_map = {s: idx - items_to_clear for s, idx in seq_to_buffer_map.items() if s > acked_seq}
```

## Retransmission Support

The wrapper maintains enough state to reconstruct messages if needed:

1. **Buffer retention**: Raw output kept until ACK
2. **Sequence mapping**: Tracks which buffer data corresponds to which sequences
3. **Pending messages**: Already-created messages available in `pending_messages`

If server requests earlier sequence (via sync_response with lower last_seq), the wrapper can:
- Use existing messages in `pending_messages`
- Reconstruct messages from `output_buffer` if needed (buffer position tracked via seq_to_buffer_map)

## Special Cases

### Kill Message Flush
When receiving kill signal, buffer is flushed immediately:
```python
# Track mapping even for kill flush
current_buffer_end = buffer_cleared_up_to + len(output_buffer)
seq_to_buffer_map[next_seq] = current_buffer_end
# Clear after kill (won't need retransmission)
output_buffer = []
```

### Final Flush
At process completion, remaining buffer is flushed:
```python
# Track mapping for final flush
current_buffer_end = buffer_cleared_up_to + len(output_buffer)
for msg in output_messages:
    seq_to_buffer_map[msg['seq']] = current_buffer_end
# Clear after final flush
output_buffer = []
```

## Benefits

1. **Data Loss Prevention**: Output kept locally until server confirms receipt
2. **Retransmission**: Can re-send earlier sequences if server loses data
3. **Resilience**: Survives temporary connection issues without losing output
4. **Integrity**: Server can detect gaps and request missing sequences
