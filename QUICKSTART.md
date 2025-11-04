# Quick Start Guide - WebSocket Refactoring

## Prerequisites

1. Python 3.7+ (tested with Python 3.11)
2. Salt master and minions configured
3. Saltpeter installed
4. websockets library 10.0+ (installed automatically via requirements.txt)

## Installation

### 1. Install/Update Dependencies

```bash
cd /home/marin/syscollective/saltpeter
pip install -r requirements.txt
```

This will install the new `websockets` dependency.

### 2. Verify Installation

```bash
python3 -c "import websockets; print('WebSocket support: OK')"
```

## Testing the WebSocket Server

### 1. Start Saltpeter with WebSocket Server

```bash
cd /home/marin/syscollective/saltpeter
python3 -m saltpeter.main -w 8889 --websocket-host 0.0.0.0 -a
```

You should see:
```
WebSocket server started on ws://0.0.0.0:8889
```

### 2. Test WebSocket Connectivity (in another terminal)

```bash
cd /home/marin/syscollective/saltpeter
python3 test_websocket.py ws://localhost:8889
```

Expected output:
```
============================================================
WebSocket Server Test Suite
============================================================
Testing WebSocket connection to ws://localhost:8889
✓ Connected to WebSocket server
✓ Sent connect message
✓ Sent start message
✓ Sent heartbeat message
✓ Sent output message
✓ Sent complete message

✓ All tests passed!
...
============================================================
All tests completed successfully!
============================================================
```

## Deploying the Wrapper Script

### Option 1: Manual Deployment

```bash
# Copy wrapper to a shared location
sudo cp saltpeter/wrapper.py /usr/local/bin/saltpeter-wrapper.py
sudo chmod +x /usr/local/bin/saltpeter-wrapper.py

# Deploy to all minions via Salt
salt '*' cp.get_file salt://saltpeter/wrapper.py /usr/local/bin/saltpeter-wrapper.py
salt '*' cmd.run 'chmod +x /usr/local/bin/saltpeter-wrapper.py'
```

### Option 2: Via Salt File Server

```bash
# 1. Copy to Salt file server
sudo mkdir -p /srv/salt/saltpeter
sudo cp saltpeter/wrapper.py /srv/salt/saltpeter/wrapper.py

# 2. Deploy to minions
salt '*' cp.get_file salt://saltpeter/wrapper.py /usr/local/bin/saltpeter-wrapper.py
salt '*' cmd.run 'chmod +x /usr/local/bin/saltpeter-wrapper.py'

# 3. Verify
salt '*' cmd.run 'ls -l /usr/local/bin/saltpeter-wrapper.py'
```

## Configuring Maintenance Mode

### 1. Create a Maintenance Configuration

```bash
cat > /etc/saltpeter/maintenance.yaml << 'EOF'
saltpeter_maintenance:
  global: false
  machines:
    - server01.example.com
EOF
```

### 2. Test Maintenance Mode

**Enable Global Maintenance:**
```bash
# Edit maintenance.yaml
saltpeter_maintenance:
  global: true
  machines: []
```

Saltpeter will:
- Not start new crons
- Allow running crons to finish
- Log "Maintenance mode active" every 20 seconds

**Add Specific Machines:**
```bash
saltpeter_maintenance:
  global: false
  machines:
    - minion1
    - minion2
```

These machines will be:
- Excluded from all cron job target lists
- Logged as "Target under maintenance"

## Testing a Job with WebSocket

### 1. Create a Test Job Configuration

```bash
cat > /etc/saltpeter/test.yaml << 'EOF'
test_websocket_job:
  targets: '*'
  target_type: 'glob'
  command: 'echo "Testing WebSocket"; sleep 10; echo "Done"'
  timeout: 60
  min: '*/5'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'
EOF
```

### 2. Monitor Logs

```bash
# In one terminal
tail -f /var/log/saltpeter/test_websocket_job.log

# In another terminal
tail -f /var/log/saltpeter/debug.log | grep -i websocket
```

### 3. Manually Trigger the Job (via API)

If API is enabled:
```bash
curl -X POST http://localhost:8888/runnow/test_websocket_job
```

### 4. Verify WebSocket Communication

You should see in the logs:
```
WebSocket: Client connected - test_websocket_job_1699000000:minion1
WebSocket: Job started - test_websocket_job_1699000000:minion1 (PID: 12345)
WebSocket: Heartbeat from test_websocket_job_1699000000:minion1
WebSocket: Output from minion1 (stdout): Testing WebSocket
WebSocket: Heartbeat from test_websocket_job_1699000000:minion1
WebSocket: Output from minion1 (stdout): Done
WebSocket: Job completed - test_websocket_job_1699000000:minion1 (exit code: 0)
```

## Troubleshooting

### WebSocket Connection Refused

**Check if server is running:**
```bash
netstat -an | grep 8889
# Should show LISTEN on port 8889
```

**Check firewall:**
```bash
sudo firewall-cmd --list-ports
# Should include 8889/tcp

# If not:
sudo firewall-cmd --add-port=8889/tcp --permanent
sudo firewall-cmd --reload
```

### Wrapper Not Found on Minions

**Verify wrapper location:**
```bash
salt '*' cmd.run 'which python3'
salt '*' cmd.run 'ls -l /usr/local/bin/saltpeter-wrapper.py'
```

**Test wrapper manually:**
```bash
# On a minion, set environment variables and run
salt 'minion1' cmd.run 'export SP_WEBSOCKET_URL=ws://saltpeter:8889 SP_JOB_NAME=test SP_JOB_INSTANCE=test_inst SP_COMMAND="echo hello" && python3 /usr/local/bin/saltpeter-wrapper.py' shell=/bin/bash
```

### Jobs Not Reporting Back

**Check WebSocket URL in run() function:**
```python
# In main.py, verify:
websocket_url = f"ws://{args.websocket_host}:{args.websocket_port}"
```

**Ensure minions can reach Saltpeter:**
```bash
salt '*' cmd.run 'telnet saltpeter_hostname 8889'
```

### Timeout Issues

**Check timeout configuration:**
```yaml
timeout: 3600  # Should be longer than job duration
```

**Monitor timeout in logs:**
```bash
grep -i timeout /var/log/saltpeter/*.log
```

## Next Steps

1. **Review Configuration:**
   - Check all YAML files in `/etc/saltpeter/`
   - Verify timeout values are appropriate
   - Configure maintenance settings if needed

2. **Monitor Production:**
   - Watch WebSocket connections: `netstat -an | grep 8889 | wc -l`
   - Monitor log sizes: `du -h /var/log/saltpeter/`
   - Check for errors: `grep -i error /var/log/saltpeter/*.log`

3. **Optimize:**
   - Adjust heartbeat interval if needed (currently 5 seconds)
   - Configure log rotation
   - Set up monitoring/alerting

4. **Enhance Security:**
   - Add TLS/SSL to WebSocket (future enhancement)
   - Implement authentication (future enhancement)
   - Restrict WebSocket port via firewall rules

## Useful Commands

```bash
# Check Saltpeter status
ps aux | grep saltpeter

# Check WebSocket connections
netstat -an | grep 8889

# Restart Saltpeter
pkill -f saltpeter
python3 -m saltpeter.main -w 8889 --websocket-host 0.0.0.0 -a

# View all logs
tail -f /var/log/saltpeter/*.log

# Test WebSocket server
python3 test_websocket.py

# Check maintenance mode
grep -r saltpeter_maintenance /etc/saltpeter/

# Manually trigger job via API
curl -X POST http://localhost:8888/runnow/<job_name>

# View running jobs
curl http://localhost:8888/running

# View job state
curl http://localhost:8888/state
```

## Support

For detailed architecture information, see:
- `WEBSOCKET_ARCHITECTURE.md` - Complete architecture documentation
- `REFACTORING_SUMMARY.md` - Summary of all changes
- `examples/config/maintenance_example.yaml` - Maintenance configuration example
