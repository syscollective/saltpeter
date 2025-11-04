### Description:

Saltpeter is a distributed cron implementation using salt as the remote execution layer.

It reads configuration from yaml files in a folder specified by -c (default /etc/saltpeter).

**NEW:** Saltpeter now supports WebSocket-based job execution for handling long-running processes without Salt timeouts, plus maintenance mode for operational control.


### Key Features:

- **Distributed Cron Execution:** Run scheduled jobs across multiple machines via Salt
- **WebSocket Communication:** Real-time job monitoring with heartbeats and output streaming
- **Maintenance Mode:** Global and per-machine maintenance controls
- **Flexible Targeting:** Use Salt's powerful targeting system (glob, pcre, compound, etc.)
- **HTTP API:** Control and monitor jobs via REST API
- **Batch Execution:** Run jobs in batches across large machine fleets
- **Timeout Handling:** Configurable timeouts with graceful termination


### Installation:

Pip install the git repository:
```
python3 -m pip install git+https://github.com/syscollective/saltpeter.git
```
Create a new service with `vim /etc/systemd/system/saltpeter.service` and add the following lines:
```
[Unit]
Description=Saltpeter distributed scheduled execution tool

[Service]
User=root
#WorkingDirectory=/opt/saltpeter/
ExecStart=/usr/local/bin/saltpeter -a -p 8888 -w 8889 --websocket-host 0.0.0.0
Restart=on-failure
KillSignal=SIGTERM
SyslogIdentifier=saltpeter

[Install]
WantedBy=multi-user.target
```
Create the following folders:
```
mkdir /etc/saltpeter
mkdir /var/log/saltpeter
```

**Deploy the wrapper script to all minions:**
```
# Copy wrapper to Salt file server
sudo cp /path/to/saltpeter/wrapper.py /srv/salt/saltpeter/wrapper.py

# Deploy to minions
salt '*' cp.get_file salt://saltpeter/wrapper.py /usr/local/bin/saltpeter-wrapper.py
salt '*' cmd.run 'chmod +x /usr/local/bin/saltpeter-wrapper.py'
```

**Configure firewall:**
```
# Allow WebSocket port
sudo firewall-cmd --add-port=8889/tcp --permanent
sudo firewall-cmd --reload
```

Start the service:
```
service saltpeter start
```


### Developing using Docker:

There is an example docker-compose file in examples which starts 1 container with salt-master and saltpeter and 2 additional salt-minion containers.

Just do:

```
cd examlples; docker-compose up
```

The master should come up and automatically accept the 2 minions (or however many) when they come up. Saltpeter will attempt to run the jobs it finds in examples/config and produce logs on stdout and in examples/logs/*.log .


### Config syntax:

#### Regular Cron Jobs:

```
example:
#name of the cron, has to be unique system-wide


    year: '*'
    mon: '*'
    dow: '*'
    dom: '*'
    hour: '1-23'
    min: '*/2'
    sec: 0,15,30,45
#time to run, same syntax as cron

    command: '/usr/bin/touch /tmp/example_cron; hostname -f'
#command to run
    user: 'root'
#user to run the command as on the target machine

    cwd: '/'
#change to this dir before executing

    targets: 'lon123*'
#expression to match target machines

    target_type: glob
#targeting type, can be one of the following:
#    glob - Bash glob completion - Default
#    pcre - Perl style regular expression
#    list - Python list of hosts
#    grain - Match based on a grain comparison
#    grain_pcre - Grain comparison with a regex
#    pillar - Pillar data comparison
#    pillar_pcre - Pillar data comparison with a regex
#    nodegroup - Match on nodegroup
#    range - Use a Range server for matching
#    compound - Pass a compound match string
#    ipcidr - Match based on Subnet (CIDR notation) or IPv4 address.
#Compound matching is best:
#https://docs.saltstack.com/en/latest/topics/targeting/compound.html

    number_of_targets: 0
#number of machines to execute on, optional, 0 means all;
#if set to 1, it will execute on only 1 machine randomly chosen from the available targets

    timeout: 3600
# Timeout in seconds; job will be marked as timed out if it exceeds this duration
# With WebSocket mode, timeout is enforced at the Saltpeter level

```

#### Maintenance Mode Configuration:

**NEW:** Use the special `saltpeter_maintenance` key to control maintenance mode:

```yaml
saltpeter_maintenance:
  # Global maintenance mode
  # If true, no new cron jobs will start (running jobs continue to completion)
  global: false
  
  # List of machines to exclude from all cron jobs
  # These machines will be automatically removed from all target lists
  machines:
    - server01.example.com
    - server02.example.com
```

**Notes:**
- The `saltpeter_maintenance` key is NOT a cron job
- It can be in any `.yaml` file in the config directory
- Settings are merged if multiple files contain this key
- Machines in the maintenance list will be logged as "Target under maintenance"


### Command-Line Options:

```bash
saltpeter [options]

Options:
  -c, --configdir DIR      Configuration directory (default: /etc/saltpeter)
  -l, --logdir DIR         Log directory (default: /var/log/saltpeter)
  -a, --api                Start HTTP API server
  -p, --port PORT          HTTP API port (default: 8888)
  -w, --websocket-port     WebSocket server port (default: 8889)
  --websocket-host HOST    WebSocket server host (default: 0.0.0.0)
  -e, --elasticsearch URL  Elasticsearch host for logging
  -o, --opensearch URL     OpenSearch host for logging
  -i, --index NAME         Index name for ES/OpenSearch (default: saltpeter)
  -v, --version            Print version and exit
```


### WebSocket Architecture:

Saltpeter now uses WebSocket communication for job execution, which:

- **Eliminates Salt timeouts** for long-running jobs
- **Streams output in real-time** from stdout/stderr
- **Sends heartbeats** every 5 seconds to confirm job is still running
- **Reports exit codes** immediately upon job completion
- **Reduces Salt overhead** by using a lightweight wrapper

**How it works:**
1. Saltpeter sends a wrapper script to minions via Salt
2. Wrapper executes immediately and returns success to Salt
3. Wrapper runs the actual command in a subprocess
4. Wrapper connects to Saltpeter's WebSocket server
5. Wrapper streams output, heartbeats, and final result
6. Saltpeter monitors jobs via WebSocket instead of polling Salt

See `WEBSOCKET_ARCHITECTURE.md` for complete details.


### Documentation:

- **QUICKSTART.md** - Quick start guide for new installations
- **WEBSOCKET_ARCHITECTURE.md** - Complete WebSocket architecture documentation
- **REFACTORING_SUMMARY.md** - Summary of all changes and features
- **MIGRATION_CHECKLIST.md** - Step-by-step migration guide
- **IMPLEMENTATION_SUMMARY.md** - Complete implementation details

### Config syntax:

```
example:
#name of the cron, has to be unique system-wide


    year: '*'
    mon: '*'
    dow: '*'
    dom: '*'
    hour: '1-23'
    min: '*/2'
    sec: 0,15,30,45
#time to run, same syntax as cron

    command: '/usr/bin/touch /tmp/example_cron; hostname -f'
#command to run
    user: 'root'
#user to run the command as on the target machine

    cwd: '/'
#change to this dir before executing

    targets: 'lon123*'
#expression to match target machines

    target_type: glob
#targeting type, can be one of the following:
#    glob - Bash glob completion - Default
#    pcre - Perl style regular expression
#    list - Python list of hosts
#    grain - Match based on a grain comparison
#    grain_pcre - Grain comparison with a regex
#    pillar - Pillar data comparison
#    pillar_pcre - Pillar data comparison with a regex
#    nodegroup - Match on nodegroup
#    range - Use a Range server for matching
#    compound - Pass a compound match string
#    ipcidr - Match based on Subnet (CIDR notation) or IPv4 address.
#Compound matching is best:
#https://docs.saltstack.com/en/latest/topics/targeting/compound.html

    number_of_targets: 0
#number of machines to execute on, optional, 0 means all;
#if set to 1, it will execute on only 1 machine randomly chosen from the available targets

    soft_timeout: 120
    hard_timeout: 240
# timeout values; soft_timeout is used only for monitoring purposes, it will only produce a line in the log so far
# hard_timeout will actually kill the job when exceeded

```
