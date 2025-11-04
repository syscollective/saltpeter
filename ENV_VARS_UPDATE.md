# Environment Variables Update - Summary

## Change Made

Updated the wrapper script to use **environment variables** instead of command-line arguments for passing configuration parameters.

## Why This Change?

### 1. **Security** 🔒
Command-line arguments are visible in process listings (`ps aux`, `top`, `/proc/*/cmdline`), which can expose sensitive information like:
- Passwords
- API keys
- Database credentials
- Secret tokens

Environment variables are NOT visible in standard process listings.

### 2. **No Escaping Issues** ✨
Command-line arguments require careful shell escaping/quoting:
```bash
# Complex escaping required
wrapper.py ... 'command with "quotes" and $variables and \backslashes'
```

Environment variables don't have this problem:
```bash
export SP_COMMAND='any $special "characters" work fine'
```

### 3. **No Length Limits** 📏
Command lines have system limits (typically ~2MB on Linux), though rarely hit. Environment variables are more flexible for very long commands.

### 4. **Easier to Extend** 🚀
Adding new parameters is trivial - just define a new env var. No need to change positional arguments or parsing logic.

## Files Modified

1. **`saltpeter/wrapper.py`**
   - Changed `main()` to read from `os.environ` instead of `sys.argv`
   - Added validation for required environment variables
   - Updated docstring with env var documentation

2. **`saltpeter/main.py`**
   - Modified `run()` function to build `wrapper_env` dict
   - Passes env vars via Salt's `cmd.run` `env` parameter
   - Cleaner code without shell escaping

3. **Documentation Updates:**
   - `WEBSOCKET_ARCHITECTURE.md` - Updated wrapper usage section
   - `QUICKSTART.md` - Updated testing examples
   - `REFACTORING_SUMMARY.md` - Added note about env vars
   - `IMPLEMENTATION_SUMMARY.md` - Added note about env vars
   - `README.md` - Added security feature highlight

4. **New Documentation:**
   - `ENV_VARS_APPROACH.md` - Detailed comparison and benefits

## Environment Variables Defined

### Required
- `SP_WEBSOCKET_URL` - WebSocket server URL (e.g., `ws://saltpeter:8889`)
- `SP_JOB_NAME` - Name of the cron job
- `SP_JOB_INSTANCE` - Unique instance identifier
- `SP_COMMAND` - The actual command to execute

### Optional
- `SP_MACHINE_ID` - Machine hostname/identifier (defaults to system hostname)
- `SP_CWD` - Working directory for command execution
- `SP_USER` - User to run the command as
- `SP_TIMEOUT` - Command timeout in seconds

## Example: Before and After

### Before (Command-Line Args)
```bash
# In main.py
wrapper_cmd = f"python3 {wrapper_path} {websocket_url} {name} {procname} $(hostname) '{command}'"
if cwd:
    wrapper_cmd += f" '{cwd}'"
if user:
    wrapper_cmd += f" '{user}'"

# Visible in ps:
python3 wrapper.py ws://server:8889 backup backup_123 host1 'mysqldump -pSECRET db' '/backup' 'root'
                                                                          ^^^^^^
                                                                      EXPOSED!
```

### After (Environment Variables)
```python
# In main.py
wrapper_env = {
    'SP_WEBSOCKET_URL': websocket_url,
    'SP_JOB_NAME': name,
    'SP_JOB_INSTANCE': procname,
    'SP_COMMAND': data['command'],
}
cmdargs = ['python3 /usr/local/bin/sp_wrapper.py']
cmdargs.append('env=' + str(wrapper_env))

# Visible in ps:
python3 /usr/local/bin/sp_wrapper.py
                   ^^^^^^^
              CLEAN - no sensitive data!
```

## Testing

```bash
# Test wrapper manually with env vars
export SP_WEBSOCKET_URL="ws://localhost:8889"
export SP_JOB_NAME="test_job"
export SP_JOB_INSTANCE="test_$(date +%s)"
export SP_COMMAND="echo 'Hello World'; sleep 5"

python3 /usr/local/bin/sp_wrapper.py
```

## Migration Impact

### For End Users
**No changes required!** 

Users continue to write YAML configs exactly as before:
```yaml
my_job:
  command: '/usr/local/bin/my-script.sh --password secret'
  # Automatically passed via SP_COMMAND env var
```

### For Developers/Maintainers
If you need to add new wrapper parameters:
1. Add to `wrapper_env` dict in `main.py`
2. Read from `os.environ` in `wrapper.py`
3. Done! No argument parsing changes needed

## Security Benefits

### Real-World Example

Imagine a job that backs up a database:

**Insecure (old approach):**
```bash
$ ps aux | grep wrapper
root  12345  python3 wrapper.py ... 'mysqldump -u root -pMyPassword123 ...'
                                                        ^^^^^^^^^^^^^^
                                           Any user can see this password!
```

**Secure (new approach):**
```bash
$ ps aux | grep wrapper
root  12345  python3 /usr/local/bin/sp_wrapper.py
                                           ^^^^^^^
                              Clean! Password hidden in environment
```

Even if someone is monitoring processes on the system, they cannot see:
- Database passwords
- API keys
- Authentication tokens
- Sensitive command arguments

## Backwards Compatibility

❌ **Not maintained** - The old command-line argument interface is removed.

All new deployments must use the environment variable approach. This is a breaking change if you were calling the wrapper manually, but:
- Saltpeter's `main.py` automatically uses the new approach
- More secure by default
- Best practice for production systems

## Conclusion

This change represents a **security improvement** and **best practice** for handling sensitive configuration. By using environment variables instead of command-line arguments, we:

✅ Hide sensitive data from process listings  
✅ Eliminate shell escaping complexity  
✅ Make the interface more flexible and extensible  
✅ Follow security best practices  

---

**Updated:** November 4, 2025  
**Impact:** Security improvement, cleaner code  
**Breaking Change:** Yes (for manual wrapper invocation only)  
**User Impact:** None (transparent to YAML config users)
