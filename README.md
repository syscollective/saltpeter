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
- **⏱️ Timeout Handling** - Configurable timeouts with graceful termination (SIGTERM → SIGKILL)
- **🔄 Job Control** - Kill running jobs from UI/API with immediate response
- **📊 Centralized Logging** - Optional Elasticsearch/OpenSearch integration

## Table of Contents

- [Installation](#installation)
- [Saltpeter Configuration](#saltpeter-configuration)
- [Quick Start](#quick-start)
- [Job Configuration](#job-configuration)
- [Maintenance Mode](#maintenance-mode)
- [HTTP API](#http-api)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)


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
ExecStart=/usr/local/bin/saltpeter -c /etc/saltpeter
Restart=on-failure
KillSignal=SIGTERM
SyslogIdentifier=saltpeter

[Install]
WantedBy=multi-user.target
```

**Note:** Saltpeter now uses YAML-based configuration. Command-line options have been removed except for `-c` (config directory) and `-v` (version).

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


## Saltpeter Configuration

Create a `saltpeter_config` section in any YAML file in your config directory (e.g., `/etc/saltpeter/config.yaml`):

```yaml
saltpeter_config:
  # Logging
  logdir: '/var/log/saltpeter'
  debug: false  # Enable verbose debug logging (hot-reloadable)
  
  # UI WebSocket API
  api_ws: true
  api_ws_port: 8888
  api_ws_bind_addr: '0.0.0.0'
  
  # Machines WebSocket (for wrapper connections)
  machines_ws_port: 8889
  machines_ws_bind_addr: '0.0.0.0'
  default_saltpeter_server_host: 'saltpeter.example.com'  # Hostname wrappers connect to
  
  # Wrapper deployment
  default_wrapper_path: '/usr/local/bin/sp_wrapper.py'
  default_wrapper_loglevel: 'normal'  # normal, debug, or off
  default_wrapper_logdir: '/var/log/sp_wrapper'
  
  # Elasticsearch logging (optional)
  elasticsearch: 'http://elasticsearch:9200'
  elasticsearch_index: 'saltpeter'
  
  # OpenSearch logging (optional)
  opensearch: 'http://opensearch:9200'
  opensearch_index: 'saltpeter'
```

### Configuration Parameters

| Parameter | Description | Default | Reload |
|-----------|-------------|---------|--------|
| `logdir` | Directory for log files | `/var/log/saltpeter` | 🔄 Runtime |
| `debug` | Enable verbose debug logging | `false` | 🔄 Runtime |
| `api_ws` | Enable UI WebSocket API | `false` | 🚫 Startup |
| `api_ws_port` | UI API port | `8888` | 🚫 Startup |
| `api_ws_bind_addr` | UI API bind address | `0.0.0.0` | 🚫 Startup |
| `machines_ws_port` | Machines WebSocket port | `8889` | 🚫 Startup |
| `machines_ws_bind_addr` | Machines WebSocket bind address | `0.0.0.0` | 🚫 Startup |
| `default_saltpeter_server_host` | Hostname for wrappers to connect | System hostname | 🔄 Runtime |
| `default_wrapper_path` | Default wrapper path on minions | `/usr/local/bin/sp_wrapper.py` | 🔄 Runtime |
| `default_wrapper_loglevel` | Wrapper log level: `normal`, `debug`, `off` | `normal` | 🔄 Runtime |
| `default_wrapper_logdir` | Directory for wrapper log files on minions | `/var/log/sp_wrapper` | 🔄 Runtime |
| `elasticsearch` | Elasticsearch URL for logging | Empty (disabled) | 🚫 Startup |
| `elasticsearch_index` | Elasticsearch index prefix | `saltpeter` | 🔄 Runtime |
| `opensearch` | OpenSearch URL for logging | Empty (disabled) | 🚫 Startup |
| `opensearch_index` | OpenSearch index prefix | `saltpeter` | 🔄 Runtime |

**Debug Logging:**

With `debug: false` (default), only essential information is logged. With `debug: true`, additional verbose logging includes heartbeats, WebSocket message details, ACKs, and connection events. The debug flag can be changed at runtime without restart.

**Wrapper Logging:**

- `normal` (default): Logs job start, completion, and errors to `/var/log/sp_wrapper/{job_name}.log`
- `debug`: Logs everything including heartbeats and WebSocket details
- `off`: No wrapper logging (useful for high-frequency jobs)

Wrapper logging can be overridden per-job:

```yaml
my_job:
  # ... other config ...
  wrapper_loglevel: 'debug'
  wrapper_logdir: '/var/log/my_job'
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

Or use the HTTP API (if enabled):

```bash
curl http://localhost:8888/status
```


## Job Configuration

Jobs are defined in YAML files in `/etc/saltpeter/`.

### Complete Example

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
  
  # Custom environment variables (optional)
  env:
    BACKUP_TYPE: 'full'
    RETENTION_DAYS: '30'
    DEBUG: '1'
  
  # Targeting
  targets: 'db-server*'
  target_type: 'glob'
  
  # Timeout handling
  timeout: 3600  # 1 hour
  
  # Optional: Custom wrapper path
  wrapper_path: '/opt/custom/sp_wrapper.py'
```

### Schedule Fields

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

### Targeting

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

### Environment Variables

Custom environment variables can be passed to jobs using the `env` configuration:

```yaml
my_job:
  command: '/usr/local/bin/script.sh'
  env:
    DATABASE_URL: 'postgresql://localhost/mydb'
    API_KEY: 'secret-key-here'
    LOG_LEVEL: 'DEBUG'
    PYTHONUNBUFFERED: '1'  # Force Python unbuffered output
```

Saltpeter automatically sets several environment variables:
- `SP_WEBSOCKET_URL`, `SP_JOB_NAME`, `SP_JOB_INSTANCE`, `SP_COMMAND`, `SP_TIMEOUT`, etc.
- Custom variables from the `env` section

### Timeout Behavior

When a job exceeds the `timeout` value:

1. Process receives SIGTERM (graceful shutdown)
2. After 5 seconds, process receives SIGKILL (force kill)
3. Job completes with exit code `124`


## Maintenance Mode

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

### How It Works

1. Saltpeter deploys wrapper script to minions via Salt
2. Wrapper executes and returns immediately to Salt (no timeout)
3. Wrapper establishes WebSocket connection to Saltpeter server
4. Real-time streaming of start, output, heartbeats, and completion
5. Server can send kill signals to terminate jobs

### Heartbeat Monitoring

- Wrapper sends heartbeat every **5 seconds**
- Server expects heartbeat within **15 seconds** (3 missed beats)
- If heartbeat times out, job is marked as failed with exit code `253`

### Security

Saltpeter passes configuration via **environment variables** instead of command-line arguments, preventing credential leakage in process listings (`ps aux`). Commands containing passwords or API keys are never visible in the process list

### Exit Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `0` | Success | Job completed successfully |
| `124` | Timeout | Job exceeded configured timeout |
| `143` | Killed by user | Job terminated via kill API/UI |
| `253` | Heartbeat timeout | Wrapper lost connection (no heartbeat for 15s) |
| `255` | Error | Target unreachable, wrapper error, or other failure |
| Other | Command exit code | Exit code from the actual command |


## HTTP API

When `api_ws` is enabled in the configuration, Saltpeter provides a REST API (default port 8888).

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

**Enable debug logging:**

Set `debug: true` in `saltpeter_config` YAML, no restart needed.

### WebSocket Connection Issues

**Verify firewall and port:**

```bash
firewall-cmd --list-ports | grep 8889
netstat -tlnp | grep 8889
```

**Test from minion:**

```bash
telnet saltpeter-host 8889
```

