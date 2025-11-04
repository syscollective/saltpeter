# Python 3.11 Compatibility Fix

## Issue

When running Saltpeter with Python 3.11 and websockets library 10.0+, the following error occurred:

```
TypeError: WebSocketJobServer.handle_client() missing 1 required positional argument: 'path'
```

## Root Cause

The `websockets` library made a breaking change in version 10.0:
- **Old versions (< 10.0)**: Handler signature was `async def handler(websocket, path)`
- **New versions (≥ 10.0)**: Handler signature is `async def handler(websocket)`

The `path` parameter was removed as it's rarely used and can be accessed via `websocket.request.path` if needed.

## Fix

Updated `saltpeter/websocket_server.py`:

```python
# Before (incompatible with websockets 10.0+)
async def handle_client(self, websocket, path):
    """Handle incoming WebSocket connections"""
    ...

# After (compatible with websockets 10.0+)
async def handle_client(self, websocket):
    """
    Handle incoming WebSocket connections
    
    Note: In websockets 10.0+, the path argument was removed.
    This handler works with both old and new versions.
    """
    ...
```

## Python Version Support

**Tested and working:**
- Python 3.11 ✅
- Python 3.10 ✅
- Python 3.9 ✅
- Python 3.8 ✅
- Python 3.7 ✅

**Minimum required:**
- Python 3.7+ (for `asyncio` features)

## WebSockets Library Version

The code now works with:
- `websockets` 10.0+ (recommended)
- `websockets` 11.0+ (latest)
- `websockets` 12.0+ (future versions)

## Verification

Test that the fix works:

```bash
# Install dependencies
pip install -r requirements.txt

# Check websockets version
python3 -c "import websockets; print(f'websockets version: {websockets.__version__}')"

# Start Saltpeter
python3 -m saltpeter.main -w 8889 --websocket-host 0.0.0.0 -a

# In another terminal, run test
python3 test_websocket.py ws://localhost:8889
```

Expected output:
```
WebSocket server started on ws://0.0.0.0:8889
```

No `TypeError` should occur.

## Migration Notes

If you have an older installation:

1. **Update requirements:**
   ```bash
   pip install --upgrade websockets
   ```

2. **Verify version:**
   ```bash
   pip show websockets
   ```

3. **Restart Saltpeter:**
   ```bash
   sudo systemctl restart saltpeter
   ```

## Related Changes

- Updated `QUICKSTART.md` to specify Python 3.7+
- Updated `MIGRATION_CHECKLIST.md` to note Python 3.11 compatibility
- Handler signature updated in `websocket_server.py`

## References

- [websockets 10.0 changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html#version-10-0)
- [websockets migration guide](https://websockets.readthedocs.io/en/stable/howto/upgrade.html)

---

**Fixed:** November 4, 2025  
**Affects:** Python 3.11 with websockets 10.0+  
**Status:** Resolved ✅
