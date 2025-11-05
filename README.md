# Saltpeter - Distributed Cron Scheduler

Saltpeter is a distributed cron implementation using SaltStack as the remote execution layer. It provides enterprise-grade scheduled job execution across large fleets of machines with real-time monitoring, WebSocket-based communication, and robust timeout handling.

## Key Features

- **🚀 Distributed Cron Execution** - Run scheduled jobs across thousands of machines simultaneously
- **📡 WebSocket Communication** - Real-time job monitoring with bidirectional control
- **💓 Heartbeat Monitoring** - Automatic detection of lost connections (15-second timeout)
- **🔒 Secure Configuration** - Environment variables prevent credential leakage in process listings
- **🛠️ Maintenance Mode** - Global and per-machine job control for operational safety
- **🎯 Flexible Targeting** - Leverage Salt's powerful targeting (glob, PCRE, compound, grains, etc.)
- **🌐 HTTP API** - Full REST API for job control and monitoring
- **⚡ Batch Execution** - Control concurrency across large server fleets
- **⏱️ Timeout Handling** - Configurable timeouts with graceful termination (SIGTERM → SIGKILL)
- **🔄 Job Control** - Kill running jobs from UI/API with immediate response
- **📊 Centralized Logging** - Optional Elasticsearch/OpenSearch integration

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
  - [Job Configuration](#job-configuration)
  - [Maintenance Mode](#maintenance-mode)
- [Architecture](#architecture)
  - [WebSocket Communication](#websocket-communication)
  - [Security Model](#security-model)
  - [Exit Codes](#exit-codes)
- [Command-Line Options](#command-line-options)
- [HTTP API](#http-api)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Development](#development)


## Installation

### Requirements

- Python 3.7+
- SaltStack (master and minions)
- websockets 10.0+

### Install from Git

Pip install the git repository:

```bash
python3 -m pip install git+https://github.com/syscollective/saltpeter.git
```

### System Service Setup

Create a systemd service file:

```bash
vim /etc/systemd/system/saltpeter.service
```

Add the following configuration:

```ini
[Unit]
Description=Saltpeter distributed scheduled execution tool
After=network.target salt-master.service

[Service]
User=root
ExecStart=/usr/local/bin/saltpeter -a -p 8888 -w 8889 --websocket-host 0.0.0.0 --wrapper-path /usr/local/bin/sp_wrapper.py
Restart=on-failure
KillSignal=SIGTERM
SyslogIdentifier=saltpeter

[Install]
WantedBy=multi-user.target
```

### Directory Setup

```bash
# Configuration and log directories
mkdir -p /etc/saltpeter /var/log/saltpeter

# Set permissions
chown root:root /etc/saltpeter /var/log/saltpeter
chmod 755 /etc/saltpeter /var/log/saltpeter
```

### Deploy Wrapper to Minions

The wrapper script must be available on all Salt minions:

```bash
# Copy wrapper to Salt file server
sudo cp /usr/local/lib/python*/site-packages/saltpeter/wrapper.py /srv/salt/saltpeter/sp_wrapper.py

# Or if installed from git
sudo cp saltpeter/wrapper.py /srv/salt/saltpeter/sp_wrapper.py

# Deploy to all minions
salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /usr/local/bin/sp_wrapper.py
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'

# Verify deployment
salt '*' cmd.run 'test -f /usr/local/bin/sp_wrapper.py && echo "OK" || echo "MISSING"'
```

### Firewall Configuration

Allow WebSocket connections from minions:

```bash
# For firewalld
sudo firewall-cmd --add-port=8889/tcp --permanent
sudo firewall-cmd --reload

# For iptables
sudo iptables -A INPUT -p tcp --dport 8889 -j ACCEPT
sudo iptables-save > /etc/sysconfig/iptables
```

### Start the Service

```bash
systemctl daemon-reload
systemctl enable saltpeter
systemctl start saltpeter
systemctl status saltpeter
```


## Quick Start

### Create Your First Cron Job

Create a configuration file:

```bash
vim /etc/saltpeter/my_first_job.yaml
```

Add a simple job:

```yaml
hello_world:
  # Schedule: Every 5 minutes
  min: '*/5'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'
  
  # Targeting
  targets: '*'
  target_type: 'glob'
  
  # Command to run
  command: 'echo "Hello from $(hostname) at $(date)"'
  user: 'root'
  cwd: '/tmp'
  
  # Timeout (in seconds)
  timeout: 300
```

Saltpeter will automatically detect the new configuration file and start scheduling the job.

### Check Job Status

View logs:

```bash
tail -f /var/log/saltpeter/saltpeter.log
```

Or use the HTTP API (if started with `-a`):

```bash
curl http://localhost:8888/status
```


## Configuration

### Job Configuration

Jobs are defined in YAML files in `/etc/saltpeter/` (or the directory specified with `-c`).

#### Complete Example

```yaml
backup_databases:
  # Unique job name
  
  # Schedule (cron syntax)
  year: '*'
  mon: '*'
  dow: '*'
  dom: '*'
  hour: '2'
  min: '0'
  sec: '0'
  
  # Command execution
  command: '/usr/local/bin/backup_script.sh --full'
  user: 'backup'
  cwd: '/var/backups'
  
  # Targeting
  targets: 'db-server*'
  target_type: 'glob'
  
  # Concurrency control
  number_of_targets: 0  # 0 = all targets, 1 = one random target, N = N random targets
  
  # Timeout handling
  timeout: 3600  # 1 hour
  
  # Optional: Custom wrapper path
  wrapper_path: '/opt/custom/sp_wrapper.py'
```

#### Schedule Fields

All fields use standard cron syntax with extensions:

- `year`: Year (e.g., `'2025'`, `'*'`)
- `mon`: Month (1-12 or `'*'`)
- `dow`: Day of week (0-6, 0=Sunday, or `'*'`)
- `dom`: Day of month (1-31 or `'*'`)
- `hour`: Hour (0-23 or `'*'`)
- `min`: Minute (0-59 or `'*/5'` for every 5 minutes)
- `sec`: Second (0-59 or `0,15,30,45` for specific seconds)

**Examples:**

```yaml
# Every 15 minutes
min: '*/15'
hour: '*'

# Every day at 2:30 AM
hour: '2'
min: '30'

# Business hours only (9 AM - 5 PM, Monday-Friday)
hour: '9-17'
dow: '1-5'

# First day of every month
dom: '1'
```

#### Targeting

Saltpeter uses Salt's targeting system. Available `target_type` values:

- **`glob`** - Bash-style globbing (default)
  ```yaml
  targets: 'web-server*'
  target_type: 'glob'
  ```

- **`pcre`** - Perl-compatible regular expressions
  ```yaml
  targets: 'db-server-[0-9]{3}'
  target_type: 'pcre'
  ```

- **`list`** - Explicit list of hosts
  ```yaml
  targets: 'server1,server2,server3'
  target_type: 'list'
  ```

- **`grain`** - Match based on Salt grains
  ```yaml
  targets: 'os:Ubuntu'
  target_type: 'grain'
  ```

- **`compound`** - Complex boolean expressions (recommended)
  ```yaml
  targets: 'G@os:Ubuntu and web-server* and not maintenance-*'
  target_type: 'compound'
  ```

See [Salt Targeting Documentation](https://docs.saltproject.io/en/latest/topics/targeting/) for details.

#### Timeout Behavior

When a job exceeds the `timeout` value:

1. Job is marked as timed out
2. Wrapper receives kill signal
3. Process receives SIGTERM (graceful shutdown)
4. After 5 seconds, process receives SIGKILL (force kill)
5. Job completes with exit code `124`

#### Wrapper Path Configuration

By default, jobs use `/usr/local/bin/sp_wrapper.py`. Override per-job if needed:

```yaml
special_job:
  wrapper_path: '/opt/custom/sp_wrapper.py'
  # ... rest of configuration
```

### Maintenance Mode

Control which machines can execute jobs using maintenance mode.

Create a configuration file (any name ending in `.yaml`):

```bash
vim /etc/saltpeter/maintenance.yaml
```

```yaml
saltpeter_maintenance:
  # Global maintenance - stops ALL new job execution
  global: false
  
  # Per-machine maintenance - excludes specific hosts
  machines:
    - server01.example.com
    - server02.example.com
    - db-primary.example.com
```

**Behavior:**

- **Global mode** (`global: true`): No new jobs start on any machine
- **Per-machine mode**: Listed machines are excluded from all job targets
- Running jobs continue to completion
- Maintenance machines are logged as "Target under maintenance"

**Dynamic Updates:**

Maintenance configuration is hot-reloaded - changes take effect immediately without restarting Saltpeter.


## Architecture

### WebSocket Communication

Saltpeter uses WebSocket-based communication to eliminate Salt's timeout limitations and provide real-time job monitoring.

#### How It Works

```
┌─────────────┐                ┌─────────────┐                ┌─────────────┐
│  Saltpeter  │                │ Salt Master │                │   Minion    │
│   Master    │                │             │                │             │
└──────┬──────┘                └──────┬──────┘                └──────┬──────┘
       │                              │                              │
       │ 1. Deploy wrapper via Salt   │                              │
       ├─────────────────────────────>│                              │
       │                              │ 2. Execute wrapper           │
       │                              ├─────────────────────────────>│
       │                              │                              │
       │                              │ 3. Return immediately        │
       │                              │<─────────────────────────────┤
       │                              │                              │
       │                 4. WebSocket connection (port 8889)         │
       │<──────────────────────────────────────────────────────────>│
       │                              │                              │
       │ 5. Stream: start, output, heartbeats, complete              │
       │<──────────────────────────────────────────────────────────┤
       │                              │                              │
       │ 6. Control: kill signal      │                              │
       ├──────────────────────────────────────────────────────────>│
       │                              │                              │
```

#### Message Types

**Wrapper → Server:**

1. **Connect** - Initial connection established
2. **Start** - Job started, includes PID
3. **Output** - Real-time stdout/stderr streaming
4. **Heartbeat** - Sent every 5 seconds to prove wrapper is alive
5. **Complete** - Job finished with exit code and full output
6. **Error** - Error occurred during execution

**Server → Wrapper:**

1. **Kill** - Terminate the running job

#### Heartbeat Monitoring

- Wrapper sends heartbeat every **5 seconds**
- Server expects heartbeat within **15 seconds** (3 missed beats)
- If heartbeat times out:
  - Job marked as failed with exit code `253`
  - Output appends: `[SALTPETER ERROR: Job lost connection - no heartbeat for X seconds]`
  - Job removed from running list

This provides automatic cleanup if wrappers crash or lose network connectivity.

#### Job Control (Kill Functionality)

Jobs can be terminated via HTTP API or UI:

```bash
# Kill a running job
curl -X POST http://localhost:8888/kill -d '{"job": "backup_databases"}'
```

**Kill Process:**

1. API adds kill command to shared queue
2. WebSocket server detects command
3. Server sends `kill` message to all wrapper connections for that job
4. Wrapper terminates subprocess:
   - Sends SIGTERM (graceful shutdown)
   - Waits 5 seconds
   - If still running, sends SIGKILL (force kill)
5. Wrapper sends completion message with exit code `143`
6. Job state updated and removed from running list

### Security Model

#### Environment Variables (Not Command-Line Args)

Saltpeter passes configuration to wrappers via **environment variables** instead of command-line arguments.

**Why this matters:**

```bash
# ❌ BAD: Password visible in process list
$ ps aux | grep wrapper
root  12345  python3 wrapper.py ws://server:8889 'mysqldump -pSecretPass123 ...'

# ✅ GOOD: No sensitive data exposed
$ ps aux | grep wrapper
root  12345  python3 /usr/local/bin/sp_wrapper.py
```

**Environment variables used:**

- `SP_WEBSOCKET_URL` - WebSocket server connection URL
- `SP_JOB_NAME` - Name of the cron job
- `SP_JOB_INSTANCE` - Unique instance identifier
- `SP_COMMAND` - **The actual command** (may contain passwords, API keys, etc.)
- `SP_MACHINE_ID` - Hostname/identifier
- `SP_CWD` - Working directory
- `SP_USER` - User to run as
- `SP_TIMEOUT` - Timeout in seconds

**Additional benefits:**

- No shell escaping issues
- No command-line length limits
- Easy to extend without changing interfaces
- Cleaner code

### Exit Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `0` | Success | Job completed successfully |
| `124` | Timeout | Job exceeded configured timeout |
| `143` | Killed by user | Job terminated via kill API/UI |
| `253` | Heartbeat timeout | Wrapper lost connection (no heartbeat for 15s) |
| `255` | Error | Target unreachable, wrapper error, or other failure |
| Other | Command exit code | Exit code from the actual command |


## Command-Line Options

```bash
saltpeter [options]
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `-c DIR`, `--configdir DIR` | Configuration directory | `/etc/saltpeter` |
| `-l DIR`, `--logdir DIR` | Log directory | `/var/log/saltpeter` |
| `-a`, `--api` | Start HTTP API server | Disabled |
| `-p PORT`, `--port PORT` | HTTP API port | `8888` |
| `-w PORT`, `--websocket-port PORT` | WebSocket server port | `8889` |
| `--websocket-host HOST` | WebSocket bind address | `0.0.0.0` |
| `--wrapper-path PATH` | Default wrapper path on minions | `/usr/local/bin/sp_wrapper.py` |
| `-e URL`, `--elasticsearch URL` | Elasticsearch host for logging | None |
| `-o URL`, `--opensearch URL` | OpenSearch host for logging | None |
| `-i NAME`, `--index NAME` | ES/OpenSearch index name | `saltpeter` |
| `-v`, `--version` | Print version and exit | - |

### Example

```bash
# Full production setup
saltpeter \
  -c /etc/saltpeter \
  -l /var/log/saltpeter \
  -a -p 8888 \
  -w 8889 --websocket-host 0.0.0.0 \
  --wrapper-path /usr/local/bin/sp_wrapper.py \
  -e http://elasticsearch:9200 \
  -i saltpeter-prod
```


## HTTP API

When started with `-a`, Saltpeter provides a REST API on port 8888 (configurable with `-p`).

### Endpoints

#### GET /status

Get current status of all jobs.

```bash
curl http://localhost:8888/status
```

**Response:**

```json
{
  "running": {
    "backup_databases_1699000000": {
      "started": "2025-11-04T02:00:00+00:00",
      "name": "backup_databases",
      "machines": ["db01.example.com", "db02.example.com"]
    }
  },
  "state": {
    "backup_databases": {
      "next_run": "2025-11-05T02:00:00+00:00",
      "last_run": "2025-11-04T02:00:00+00:00",
      "results": {
        "db01.example.com": {
          "starttime": "2025-11-04T02:00:05+00:00",
          "endtime": "2025-11-04T02:15:23+00:00",
          "ret": "Backup completed successfully\n",
          "retcode": 0
        }
      }
    }
  }
}
```

#### POST /kill

Kill a running job.

```bash
curl -X POST http://localhost:8888/kill \
  -H "Content-Type: application/json" \
  -d '{"job": "backup_databases"}'
```

**Response:**

```json
{
  "status": "ok",
  "message": "Kill signal sent to job: backup_databases"
}
```

#### WebSocket Endpoint

Connect to `ws://localhost:8888/ws` for real-time job updates.

**Client → Server:**

```json
{"subscribe": "backup_databases"}
```

**Server → Client (job updates):**

```json
{
  "type": "output",
  "job": "backup_databases",
  "machine": "db01.example.com",
  "data": "Backing up table users...\n"
}
```

**Kill a job via WebSocket:**

```json
{"killCron": "backup_databases"}
```


## Deployment

### Docker Deployment

See `examples/docker-compose.yml` for a complete Docker setup with Salt master and minions.

```bash
cd examples
docker-compose up
```

This starts:
- 1 container with Salt master and Saltpeter
- 2 Salt minion containers
- Automatic minion key acceptance
- Example jobs from `examples/config/`

### Production Deployment Checklist

- [ ] Install Saltpeter on Salt master
- [ ] Deploy wrapper script to all minions
- [ ] Configure firewall (port 8889)
- [ ] Create configuration directory `/etc/saltpeter`
- [ ] Create log directory `/var/log/saltpeter`
- [ ] Set up systemd service
- [ ] Configure log rotation
- [ ] Set up monitoring/alerting on logs
- [ ] Test with a simple job
- [ ] Configure Elasticsearch/OpenSearch (optional)
- [ ] Set up backup of configuration files

### High Availability

For HA setups:

1. **Multiple Saltpeter instances** - Run on different hosts with shared config
2. **Load balancer** - Distribute WebSocket connections
3. **Shared state** - Use Redis or similar for shared state (requires code changes)
4. **Failover** - Use keepalived or similar for automatic failover


## Troubleshooting

### Jobs Not Starting

**Check configuration syntax:**

```bash
python3 -c "import yaml; print(yaml.safe_load(open('/etc/saltpeter/job.yaml')))"
```

**Check logs:**

```bash
tail -f /var/log/saltpeter/saltpeter.log | grep ERROR
```

**Verify Salt connectivity:**

```bash
salt '*' test.ping
```

### Jobs Running Forever

**Check if wrapper is reachable:**

```bash
salt '*' cmd.run 'test -f /usr/local/bin/sp_wrapper.py && echo OK'
```

**Test WebSocket connectivity from minion:**

```bash
salt 'minion1' cmd.run 'timeout 5 bash -c "</dev/tcp/saltpeter-host/8889" && echo "Port open" || echo "Port closed"'
```

**Check for heartbeat in logs:**

```bash
grep "Heartbeat" /var/log/saltpeter/saltpeter.log
```

### WebSocket Connection Issues

**Verify firewall:**

```bash
# On Saltpeter server
firewall-cmd --list-ports | grep 8889

# Test from minion
telnet saltpeter-host 8889
```

**Check WebSocket server:**

```bash
netstat -tlnp | grep 8889
```

**Enable debug logging:**

Modify `websocket_server.py` to add more logging, or check existing logs:

```bash
grep "WebSocket" /var/log/saltpeter/saltpeter.log
```

### Kill Not Working

**Verify job is running:**

```bash
curl http://localhost:8888/status | jq '.running'
```

**Check if kill command sent:**

```bash
curl -X POST http://localhost:8888/kill -d '{"job": "job_name"}'
```

**Check wrapper received kill:**

Look for completion with exit code 143:

```bash
grep "retcode.*143" /var/log/saltpeter/job_name.log
```

### Heartbeat Timeout Issues

**Check heartbeat interval:**

Wrappers send heartbeat every 5 seconds. Check logs:

```bash
grep "Heartbeat from" /var/log/saltpeter/saltpeter.log | tail -20
```

**Verify timeout detection:**

Manually kill a wrapper process and watch logs:

```bash
# On minion
pkill -9 -f sp_wrapper.py

# On Saltpeter (wait 15 seconds)
grep "no heartbeat" /var/log/saltpeter/saltpeter.log
```

Should see job marked as failed with retcode 253.


## Development

### Development with Docker

Use the provided Docker environment:

```bash
cd examples
docker-compose up -d

# Watch logs
docker-compose logs -f saltpeter

# Execute commands in container
docker-compose exec saltpeter bash
```

### Running Tests

```bash
# Test WebSocket server
python3 test_websocket.py

# Test kill functionality
python3 test_kill_job.py <job_name>
```

### Project Structure

```
saltpeter/
├── __init__.py           # Package initialization
├── main.py               # Main orchestration, job scheduling
├── api.py                # HTTP API server
├── websocket_server.py   # WebSocket server for wrapper communication
├── wrapper.py            # Wrapper script (deployed to minions)
├── timeline.py           # Cron schedule calculations
└── version.py            # Version information
```

### Key Functions

**main.py:**
- `run()` - Execute a scheduled job
- `processresults_websocket()` - Wait for WebSocket results with timeout/heartbeat monitoring
- `main()` - Main event loop, schedule checking

**websocket_server.py:**
- `handle_client()` - Handle wrapper connections and messages
- `check_commands()` - Background task for kill commands

**wrapper.py:**
- `run_command_and_stream()` - Execute command and stream output
- Listen for kill signals while running

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with Docker environment
5. Submit a pull request

### Code Style

- Follow PEP 8
- Use type hints where appropriate
- Add docstrings to functions
- Keep functions focused and small


## License

See LICENSE file for details.


## Support

- GitHub Issues: https://github.com/syscollective/saltpeter/issues
- Documentation: See this README


## Changelog

### Version 2.0 (Current)

- ✅ WebSocket-based job execution (eliminates Salt timeouts)
- ✅ Bidirectional communication (kill signals from server)
- ✅ Heartbeat monitoring (15-second timeout detection)
- ✅ Environment variable configuration (security improvement)
- ✅ Maintenance mode (global and per-machine)
- ✅ Configurable wrapper paths
- ✅ Improved timeout handling (SIGTERM → SIGKILL)
- ✅ Real-time output streaming
- ✅ HTTP API for job control

### Version 1.x (Legacy)

- Salt-based polling execution
- Basic timeout handling
- Limited job control

