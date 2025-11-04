# Wrapper Path Configuration

## Overview

The wrapper script path is now configurable to support different deployment scenarios while maintaining a sensible default location.

## Default Location

**Default Path:** `/usr/local/bin/sp_wrapper.py`

This is a standard location for system-wide executables and is automatically used if no custom path is specified.

## Configuration Options

### 1. Command-Line Argument (Global Default)

Set the default wrapper path for all jobs when starting Saltpeter:

```bash
python3 -m saltpeter.main --wrapper-path /custom/path/to/wrapper.py -a
```

This sets the global default that applies to all jobs unless overridden in the YAML config.

### 2. YAML Configuration (Per-Job Override)

Override the wrapper path for a specific job in its YAML configuration:

```yaml
jobs:
  backup_database:
    targets: 'db-*'
    command: '/usr/local/bin/backup.sh'
    wrapper_path: '/opt/saltpeter/custom_wrapper.py'  # Custom path for this job
```

**Priority Order:**
1. Job-specific `wrapper_path` in YAML (highest priority)
2. `--wrapper-path` command-line argument
3. Default: `/usr/local/bin/sp_wrapper.py` (if neither is specified)

## Deployment Methods

### Standard Deployment (Recommended)

Deploy to the default location on all minions:

```bash
# Copy wrapper to Salt file server
sudo cp saltpeter/wrapper.py /srv/salt/saltpeter/sp_wrapper.py

# Deploy to minions
salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /usr/local/bin/sp_wrapper.py
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'

# Verify
salt '*' cmd.run 'ls -l /usr/local/bin/sp_wrapper.py'
```

Then start Saltpeter without `--wrapper-path` (uses default):

```bash
python3 -m saltpeter.main -a -w 8889
```

### Custom Location Deployment

Deploy to a custom location:

```bash
# Deploy to custom path
salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /opt/myapp/wrapper.py
salt '*' cmd.run 'chmod +x /opt/myapp/wrapper.py'

# Start with custom default
python3 -m saltpeter.main --wrapper-path /opt/myapp/wrapper.py -a -w 8889
```

### Mixed Environment

Use different wrappers for different job types:

```yaml
# Production backup jobs use hardened wrapper
backup_prod:
  targets: 'prod-*'
  command: '/backup/prod.sh'
  wrapper_path: '/opt/secure/wrapper.py'

# Development jobs use standard wrapper
dev_tasks:
  targets: 'dev-*'
  command: '/tasks/dev.sh'
  # Uses default /usr/local/bin/sp_wrapper.py
```

## Use Cases

### 1. Standard Production Environment

**Scenario:** All minions have wrapper in standard location

**Configuration:**
- Deploy wrapper to `/usr/local/bin/sp_wrapper.py` on all minions
- Start Saltpeter without `--wrapper-path` argument
- No `wrapper_path` needed in YAML configs

**Benefits:**
- Simple, consistent deployment
- No extra configuration needed
- Clear standard location

### 2. Multiple Wrapper Versions

**Scenario:** Testing new wrapper version alongside stable version

**Configuration:**
```bash
# Deploy stable version
salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /usr/local/bin/sp_wrapper.py

# Deploy beta version
salt '*' cp.get_file salt://saltpeter/sp_wrapper_beta.py /usr/local/bin/sp_wrapper_beta.py
```

```yaml
# Stable jobs use default
production_job:
  targets: 'prod-*'
  command: '/prod/job.sh'
  # Uses /usr/local/bin/sp_wrapper.py

# Test jobs use beta
test_job:
  targets: 'test-*'
  command: '/test/job.sh'
  wrapper_path: '/usr/local/bin/sp_wrapper_beta.py'
```

### 3. Heterogeneous Environment

**Scenario:** Different OS types with different paths

**Configuration:**
```yaml
# Linux jobs
linux_backup:
  targets: 'G@os:Ubuntu'
  command: '/backup/linux.sh'
  wrapper_path: '/usr/local/bin/sp_wrapper.py'

# FreeBSD jobs
bsd_backup:
  targets: 'G@os:FreeBSD'
  command: '/backup/bsd.sh'
  wrapper_path: '/usr/local/libexec/sp_wrapper.py'
```

### 4. Containerized Deployment

**Scenario:** Wrapper embedded in container image at custom location

**Configuration:**
```yaml
container_job:
  targets: 'docker-*'
  command: '/app/process.sh'
  wrapper_path: '/app/bin/sp_wrapper.py'  # Inside container
```

## Systemd Service Configuration

Include `--wrapper-path` in service file if using non-default location:

```ini
[Service]
ExecStart=/usr/local/bin/saltpeter -a -p 8888 -w 8889 \
    --websocket-host 0.0.0.0 \
    --wrapper-path /usr/local/bin/sp_wrapper.py
```

**Note:** If using default location, you can omit the `--wrapper-path` argument.

## Verification

### Check Current Configuration

1. **View command-line arguments:**
```bash
ps aux | grep saltpeter
```

2. **Test wrapper path resolution:**
```bash
# Check if wrapper exists on minion
salt 'minion1' cmd.run 'ls -l /usr/local/bin/sp_wrapper.py'

# Test wrapper execution
salt 'minion1' cmd.run 'python3 /usr/local/bin/sp_wrapper.py --help'
```

3. **Review job configuration:**
```bash
# Check what path will be used for a specific job
grep -A5 "job_name:" /etc/saltpeter/config.yaml
```

## Troubleshooting

### Wrapper Not Found

**Error:** `FileNotFoundError: /usr/local/bin/sp_wrapper.py`

**Solutions:**
1. Verify wrapper is deployed:
   ```bash
   salt 'minion*' cmd.run 'ls -l /usr/local/bin/sp_wrapper.py'
   ```

2. Check permissions:
   ```bash
   salt 'minion*' cmd.run 'test -x /usr/local/bin/sp_wrapper.py && echo OK || echo FAIL'
   ```

3. Deploy if missing:
   ```bash
   salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /usr/local/bin/sp_wrapper.py
   salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper.py'
   ```

### Wrong Wrapper Path Used

**Symptom:** Job uses wrong wrapper version

**Debug:**
1. Check job config for `wrapper_path` override
2. Check Saltpeter startup arguments for `--wrapper-path`
3. Verify priority: Job YAML > CLI arg > Default

**Fix:** Update configuration at appropriate level (job YAML or CLI arg)

### Inconsistent Deployment

**Symptom:** Some minions have wrapper, others don't

**Solutions:**
```bash
# Find minions missing wrapper
salt '*' cmd.run 'test -f /usr/local/bin/sp_wrapper.py && echo PRESENT || echo MISSING'

# Deploy to all missing
salt '*' cp.get_file salt://saltpeter/sp_wrapper.py /usr/local/bin/sp_wrapper.py
```

## Migration from Hard-Coded Path

**Old code:**
```python
wrapper_path = os.path.join(os.path.dirname(__file__), 'wrapper.py')
```

**New code:**
```python
wrapper_path = data.get('wrapper_path', args.wrapper_path)
# args.wrapper_path defaults to '/usr/local/bin/sp_wrapper.py'
```

**Migration steps:**
1. Deploy wrapper to `/usr/local/bin/sp_wrapper.py` on all minions
2. Update Saltpeter to version with configurable wrapper path
3. Restart Saltpeter (will use default path)
4. Optionally add `wrapper_path` to job configs if custom paths needed

## Best Practices

1. **Use default location** unless you have a specific reason not to
2. **Deploy consistently** - all minions of the same type should have wrapper at same path
3. **Version separately** - if testing new wrapper, use different filename (e.g., `sp_wrapper_v2.py`)
4. **Document overrides** - add comments in YAML when using custom `wrapper_path`
5. **Verify deployment** - always check wrapper exists and is executable after deployment
6. **Use Salt grain targeting** - match wrapper path to OS/platform using grains in YAML

## Security Considerations

- Wrapper should only be writable by root/admin
- Use standard system paths (`/usr/local/bin`) to prevent privilege escalation
- Avoid user-writable directories for wrapper location
- Verify wrapper integrity after deployment:
  ```bash
  salt '*' cmd.run 'sha256sum /usr/local/bin/sp_wrapper.py'
  ```

## Related Documentation

- [README.md](README.md) - Main documentation with wrapper deployment
- [QUICKSTART.md](QUICKSTART.md) - Quick start guide with wrapper setup
- [MIGRATION_CHECKLIST.md](MIGRATION_CHECKLIST.md) - Migration steps including wrapper deployment
- [WEBSOCKET_ARCHITECTURE.md](WEBSOCKET_ARCHITECTURE.md) - Technical details of wrapper operation
