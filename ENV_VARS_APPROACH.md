# Environment Variables vs Command-Line Arguments

## Summary of Change

The wrapper script has been updated to use **environment variables** instead of command-line arguments for configuration. This provides better security, avoids escaping issues, and makes the interface more flexible.

## Comparison

### Old Approach (Command-Line Arguments)

```bash
# How it was called
python3 wrapper.py ws://saltpeter:8889 backup_job backup_job_123 server01 \
  '/usr/local/bin/backup.sh --password "secret123"' \
  '/backup' 'backup_user'
```

**Problems:**
1. **Security**: All arguments visible in `ps aux` output
   ```bash
   $ ps aux | grep wrapper
   root  12345  python3 wrapper.py ws://... '/usr/local/bin/backup.sh --password "secret123"' ...
   # ^^^ Password exposed!
   ```

2. **Escaping Complexity**: Had to properly escape quotes, spaces, special characters
   ```bash
   wrapper.py ... 'command with "quotes" and $variables'
   ```

3. **Length Limits**: Command lines have system limits (~128KB on Linux)
   ```bash
   getconf ARG_MAX  # 2097152 on most systems, but still a limit
   ```

4. **Difficult to Extend**: Adding new parameters requires changing argument parsing logic

### New Approach (Environment Variables)

```bash
# How it's called now
export SP_WEBSOCKET_URL="ws://saltpeter:8889"
export SP_JOB_NAME="backup_job"
export SP_JOB_INSTANCE="backup_job_123"
export SP_COMMAND='/usr/local/bin/backup.sh --password "secret123"'
export SP_CWD="/backup"
export SP_USER="backup_user"

python3 wrapper.py
```

**Benefits:**
1. **Secure**: Environment variables not visible in process listings
   ```bash
   $ ps aux | grep wrapper
   root  12345  python3 wrapper.py
   # Clean! No sensitive data exposed
   ```

2. **No Escaping Issues**: Shell doesn't interpret env var contents
   ```bash
   export SP_COMMAND='any $special "characters" work fine'
   ```

3. **No Length Limits**: Env vars can be much larger
   ```bash
   # Can handle very long commands without issues
   ```

4. **Easy to Extend**: Just add new env vars, no code changes needed
   ```bash
   export SP_NEW_FEATURE="value"  # Wrapper can check for it
   ```

## Implementation Details

### Salt Integration

Salt's `cmd.run` supports passing environment variables via the `env` parameter:

```python
# In main.py run() function
wrapper_env = {
    'SP_WEBSOCKET_URL': websocket_url,
    'SP_JOB_NAME': name,
    'SP_JOB_INSTANCE': procname,
    'SP_COMMAND': data['command'],
}

cmdargs = ['python3 /usr/local/bin/sp_wrapper.py']
cmdargs.append('env=' + str(wrapper_env))

job = salt.run_job(targets, 'cmd.run', cmdargs, tgt_type='list')
```

### Environment Variables Defined

**Required:**
- `SP_WEBSOCKET_URL` - WebSocket server URL
- `SP_JOB_NAME` - Cron job name
- `SP_JOB_INSTANCE` - Unique instance ID
- `SP_COMMAND` - Command to execute

**Optional:**
- `SP_MACHINE_ID` - Machine identifier (defaults to hostname)
- `SP_CWD` - Working directory
- `SP_USER` - User to run as
- `SP_TIMEOUT` - Command timeout in seconds

### Wrapper Validation

The wrapper validates required environment variables on startup:

```python
def main():
    # Read configuration from environment variables
    websocket_url = os.environ.get('SP_WEBSOCKET_URL')
    job_name = os.environ.get('SP_JOB_NAME')
    job_instance = os.environ.get('SP_JOB_INSTANCE')
    command = os.environ.get('SP_COMMAND')
    
    # Validate required parameters
    if not websocket_url:
        print("Error: SP_WEBSOCKET_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    # ... more validation
```

## Security Improvements

### Example: Database Backup Job

**Old (Insecure):**
```bash
# Command visible in ps output:
python3 wrapper.py ... 'mysqldump -u root -p"MySecret123" mydb > /backup/db.sql'

$ ps aux | grep mysqldump
# Shows password in plain text!
```

**New (Secure):**
```bash
export SP_COMMAND='mysqldump -u root -p"MySecret123" mydb > /backup/db.sql'
python3 wrapper.py

$ ps aux | grep wrapper
# Shows only: python3 wrapper.py
# Password is hidden!
```

### Example: API Key in Command

**Old (Insecure):**
```bash
wrapper.py ... 'curl -H "Authorization: Bearer sk_live_51abc123..." https://api.example.com'
# API key visible in process list
```

**New (Secure):**
```bash
export SP_COMMAND='curl -H "Authorization: Bearer sk_live_51abc123..." https://api.example.com'
python3 wrapper.py
# API key hidden from process list
```

## Migration Notes

### For Users

No changes needed to YAML configuration files. The environment variable approach is transparent to users - they continue to specify commands in YAML as before:

```yaml
backup_job:
  command: '/usr/local/bin/backup.sh --password "secret"'
  # This is automatically passed via SP_COMMAND environment variable
```

### For Developers

If extending the wrapper, simply:
1. Add new environment variable to `wrapper_env` dict in `main.py`
2. Read it in wrapper's `main()` function
3. Pass to `run_command_and_stream()` if needed

```python
# In main.py
wrapper_env['SP_NEW_OPTION'] = data.get('new_option', 'default')

# In wrapper.py
new_option = os.environ.get('SP_NEW_OPTION', 'default')
```

## Testing

### Test Wrapper Manually

```bash
# Set environment variables
export SP_WEBSOCKET_URL="ws://localhost:8889"
export SP_JOB_NAME="test_job"
export SP_JOB_INSTANCE="test_instance_$(date +%s)"
export SP_COMMAND="echo 'Testing wrapper'; sleep 5; echo 'Done'"

# Run wrapper
python3 /usr/local/bin/sp_wrapper.py
```

### Test via Salt

```bash
# Test on a single minion
salt 'minion1' cmd.run 'python3 /usr/local/bin/sp_wrapper.py' \
  env='{
    "SP_WEBSOCKET_URL": "ws://saltpeter:8889",
    "SP_JOB_NAME": "test",
    "SP_JOB_INSTANCE": "test_123",
    "SP_COMMAND": "echo hello && sleep 10"
  }'
```

## Backwards Compatibility

The old command-line interface is **not maintained**. All deployments should use the environment variable approach.

If you have a custom wrapper or scripts that call the wrapper directly, update them to use environment variables.

## Conclusion

Using environment variables for wrapper configuration provides:
- ✅ Better security (no sensitive data in process listings)
- ✅ No shell escaping problems
- ✅ No length limitations
- ✅ Easier to extend with new parameters
- ✅ Cleaner code and interface

This is a best practice for handling sensitive configuration data in subprocess execution.
