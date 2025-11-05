# Saltpeter Wrapper Binary

Standalone binary for the Saltpeter wrapper, built with PyInstaller. This allows you to deploy the wrapper to target machines without requiring Python to be installed.

## Quick Start

### Download

Download the latest release from the [Releases page](https://github.com/syscollective/saltpeter/releases).

Choose the appropriate binary for your platform:
- **Ubuntu 22.04 / Debian 11+ / RHEL 8+**: `sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz` (recommended)
- **Ubuntu 20.04 / Debian 10**: `sp_wrapper-linux-x86_64-ubuntu20-py3.11.tar.gz`

### Install

```bash
# Download (replace with actual release URL)
wget https://github.com/syscollective/saltpeter/releases/download/vX.Y.Z/sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz

# Extract
tar -xzf sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz

# Make executable
chmod +x sp_wrapper

# Install system-wide
sudo mv sp_wrapper /usr/local/bin/

# Verify
/usr/local/bin/sp_wrapper
# Should show: Error: SP_WEBSOCKET_URL environment variable not set
```

### Deploy via Salt

```bash
# Copy to Salt file server
sudo cp sp_wrapper /srv/salt/sp_wrapper

# Deploy to all minions
salt '*' cp.get_file salt://sp_wrapper /usr/local/bin/sp_wrapper
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper'

# Verify on all minions
salt '*' cmd.run '/usr/local/bin/sp_wrapper' 2>/dev/null || true
```

## Configuration

Update your Saltpeter job configuration to use the wrapper:

```yaml
my_job:
  command: /path/to/your/command.sh
  targets: '*'
  target_type: glob
  min: '*/5'
  hour: '*'
  dom: '*'
  mon: '*'
  dow: '*'
  use_wrapper: true                        # Enable wrapper (default: true)
  wrapper_path: /usr/local/bin/sp_wrapper  # Path to wrapper binary
```

## Environment Variables

The wrapper is configured via environment variables (set automatically by Saltpeter):

- `SP_WEBSOCKET_URL` - WebSocket server URL (required)
- `SP_JOB_NAME` - Name of the cron job (required)
- `SP_JOB_INSTANCE` - Unique instance identifier (required)
- `SP_COMMAND` - Command to execute (required)
- `SP_MACHINE_ID` - Machine hostname/identifier (optional, defaults to hostname)
- `SP_CWD` - Working directory (optional)
- `SP_USER` - User to run command as (optional)
- `SP_TIMEOUT` - Command timeout in seconds (optional)

## How It Works

1. Salt runs the wrapper via `cmd.run`
2. Wrapper immediately forks to background and returns success to Salt
3. Background process executes the actual command
4. Output is streamed to Saltpeter server via WebSocket
5. Heartbeats sent every 5 seconds
6. Completion status reported when job finishes

## Features

- **Standalone binary** - No Python installation required on targets
- **Fire and forget** - Salt sees immediate success, job runs in background
- **Real-time monitoring** - Output streamed via WebSocket
- **Heartbeat detection** - Jobs timeout if connection lost (15 seconds)
- **Kill support** - Jobs can be killed from Saltpeter UI
- **Continuous retry** - WebSocket reconnects every 2 seconds if disconnected

## Platform Compatibility

### Tested On
- Ubuntu 20.04, 22.04, 24.04
- Debian 10, 11, 12
- RHEL/Rocky/Alma 8, 9
- Other Linux distributions with glibc 2.31+

### Not Supported
- Windows
- macOS
- Alpine Linux (musl libc)

For Alpine or other musl-based systems, you'll need to run the Python script directly with Python installed.

## Troubleshooting

### Binary won't run

```bash
# Check if binary is executable
ls -l /usr/local/bin/sp_wrapper

# Check for missing libraries
ldd /usr/local/bin/sp_wrapper

# Check glibc version (needs 2.31+)
ldd --version
```

### WebSocket connection issues

```bash
# Test connectivity from minion to master
telnet saltpeter-host 8889

# Check firewall
sudo iptables -L -n | grep 8889
```

### Jobs not appearing in Saltpeter

Check wrapper is being invoked:
```bash
# On master, check Saltpeter config
grep wrapper_path /etc/saltpeter/*.yaml

# On minion, test wrapper manually
export SP_WEBSOCKET_URL="ws://master:8889"
export SP_JOB_NAME="test"
export SP_JOB_INSTANCE="test123"
export SP_COMMAND="echo hello"
/usr/local/bin/sp_wrapper
```

## Building from Source

If you need to build the wrapper yourself:

```bash
# Install dependencies
pip install pyinstaller websockets

# Build
pyinstaller --onefile --name sp_wrapper saltpeter/wrapper.py

# Binary will be in dist/sp_wrapper
```

## Upgrading

To upgrade to a new version:

```bash
# Download new version
wget https://github.com/syscollective/saltpeter/releases/download/vX.Y.Z/sp_wrapper-....tar.gz

# Extract
tar -xzf sp_wrapper-....tar.gz

# Deploy via Salt
salt '*' cp.get_file salt://sp_wrapper /usr/local/bin/sp_wrapper
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper'
```

Running jobs will continue to use the old wrapper. New jobs will use the new wrapper.

## Security Considerations

- Wrapper runs with same privileges as Salt minion (typically root)
- If `SP_USER` is set, command runs as that user (requires root)
- WebSocket traffic is not encrypted by default (use reverse proxy with TLS)
- No authentication on WebSocket (rely on network security)

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1-123 | Command-specific errors |
| 124 | Timeout |
| 143 | Killed by user |
| 253 | Heartbeat timeout |
| 255 | Generic error |

## Support

- GitHub Issues: https://github.com/syscollective/saltpeter/issues
- Documentation: https://github.com/syscollective/saltpeter

## License

Same as Saltpeter main project.
