# GitHub Action: Wrapper Binary Build & Release

## Overview

Automated GitHub Action that builds the Saltpeter wrapper as a standalone binary and creates releases.

## What It Does

1. **Builds wrapper binary** using PyInstaller on multiple platforms/Python versions
2. **Uploads artifacts** for every push to master/async-agent branches
3. **Creates releases** automatically when you push a version tag (e.g., `v1.0.0`)

## Files Created

```
.github/workflows/build-wrapper.yml  - GitHub Action workflow
build_wrapper.sh                     - Local build script for testing
WRAPPER_BINARY.md                    - User documentation for binary
```

## How to Use

### For Development (Artifacts)

Push to `master` or `async-agent` branch:
```bash
git add .
git commit -m "Update wrapper"
git push origin async-agent
```

GitHub Action will:
- Build binaries for Ubuntu 20.04 and 22.04
- Build with Python 3.9, 3.10, and 3.11
- Upload as artifacts (available for 90 days)
- Download from: Actions → Build Wrapper Binary → Latest run → Artifacts

### For Release

Update the version in `saltpeter/version.py`:
```python
__version__ = '1.0.0'
```

Commit and push:
```bash
git add saltpeter/version.py
git commit -m "Bump version to 1.0.0"
git push origin master  # or async-agent
```

GitHub Action will:
- Detect the new version
- Check if a release with this version already exists
- If not, automatically build and create a GitHub Release
- Tag the release as `v1.0.0`
- Attach all binaries as release assets
- Include installation instructions

**Note:** Releases are only created from `master` or `async-agent` branches when the version in `version.py` changes.

### Local Testing

Test the build locally before pushing:
```bash
./build_wrapper.sh
```

This creates:
- `dist/linux-x86_64-ubuntuXX/pythonX.X/sp_wrapper` - Binary
- `sp_wrapper-linux-x86_64-ubuntuXX-pyX.X.tar.gz` - Release archive

## Matrix Build

The action builds for:

| OS | Python Versions | Platform Name |
|----|----------------|---------------|
| ubuntu-20.04 | 3.9, 3.10, 3.11 | linux-x86_64-ubuntu20 |
| ubuntu-22.04 | 3.9, 3.10, 3.11 | linux-x86_64-ubuntu22 |

Total: **6 binaries** per build

## Artifact Structure

Each artifact contains:
```
sp_wrapper          - Standalone binary (~15-20 MB)
VERSION.txt         - Build information
```

## Release Structure

Release includes:
- All 6 binary archives (`.tar.gz`)
- Release notes with installation instructions
- Platform compatibility information
- Usage examples

## Deployment Workflow

### 1. Build and Release
```bash
# Tag a release
git tag v1.0.0
git push origin v1.0.0

# Wait for GitHub Action to complete
# Check: https://github.com/syscollective/saltpeter/actions
```

### 2. Download and Test
```bash
# Download from release
wget https://github.com/syscollective/saltpeter/releases/download/v1.0.0/sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz

# Extract and test
tar -xzf sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz
chmod +x sp_wrapper
./sp_wrapper  # Should show error about missing env vars
```

### 3. Deploy to Salt File Server
```bash
# Copy to Salt file server
sudo cp sp_wrapper /srv/salt/sp_wrapper

# Deploy to all minions
salt '*' cp.get_file salt://sp_wrapper /usr/local/bin/sp_wrapper
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper'
```

### 4. Verify Deployment
```bash
# Check wrapper is executable on all minions
salt '*' cmd.run '/usr/local/bin/sp_wrapper' 2>/dev/null | grep -i error || echo "OK"
```

### 5. Update Saltpeter Config
```yaml
# /etc/saltpeter/your_jobs.yaml
my_job:
  command: /path/to/command
  targets: '*'
  target_type: glob
  use_wrapper: true
  wrapper_path: /usr/local/bin/sp_wrapper  # Point to new binary
  # ... rest of config
```

## Customizing the Build

### Add More Platforms

Edit `.github/workflows/build-wrapper.yml`:

```yaml
matrix:
  os: [ubuntu-20.04, ubuntu-22.04, ubuntu-24.04]  # Add Ubuntu 24.04
  python-version: ['3.9', '3.10', '3.11', '3.12']  # Add Python 3.12
  include:
    - os: ubuntu-20.04
      platform: linux-x86_64-ubuntu20
    - os: ubuntu-22.04
      platform: linux-x86_64-ubuntu22
    - os: ubuntu-24.04
      platform: linux-x86_64-ubuntu24
```

### Change Binary Name

```yaml
pyinstaller --onefile \
  --name saltpeter-wrapper \  # Change name here
  # ...
```

### Add Build Options

```yaml
pyinstaller --onefile \
  --name sp_wrapper \
  --strip \              # Strip symbols (smaller binary)
  --noupx \              # Don't use UPX compression
  --log-level WARN \     # Less verbose build
  saltpeter/wrapper.py
```

## Troubleshooting

### Action fails with "PyInstaller not found"

The action installs PyInstaller automatically. If it fails, check:
- Python version is supported by PyInstaller
- No network issues downloading packages

### Binary too large

Current size: ~15-20 MB (includes Python interpreter and websockets library)

To reduce:
- Use `--strip` flag
- Use `--exclude-module` for unused modules
- Use UPX compression (may cause issues on some systems)

### Binary won't run on target

Check:
- glibc version: Binary built on Ubuntu 22.04 requires glibc 2.31+
- Architecture: Binary is x86_64 only
- Use binary built on older platform for older targets

### Release not created

Ensure:
- Tag starts with `v` (e.g., `v1.0.0`)
- Tag is pushed to GitHub
- GITHUB_TOKEN has permission to create releases

## Benefits

### For Developers
- ✅ Automated builds on every push
- ✅ Test artifacts without manual releases
- ✅ Consistent builds across platforms

### For Operators
- ✅ Single binary - no Python dependencies
- ✅ Easy deployment via Salt
- ✅ Version tracking via tags
- ✅ Multiple platform support

### For Users
- ✅ Simple installation (download, chmod, run)
- ✅ Clear version information
- ✅ No compilation required

## Future Improvements

Potential enhancements:
- [ ] Add ARM64 builds (for ARM servers)
- [ ] Add RHEL/CentOS specific builds
- [ ] Add Alpine Linux builds (with musl libc)
- [ ] Add checksums (SHA256) to releases
- [ ] Add GPG signatures
- [ ] Add Docker image with wrapper pre-installed
- [ ] Add Homebrew/APT repository

## Security Notes

- Binary is built on GitHub-hosted runners (trusted environment)
- Source code is publicly available
- Can verify build by comparing local build with released binary
- Consider adding code signing for production use

## Documentation

- **User docs**: `WRAPPER_BINARY.md`
- **Build script**: `build_wrapper.sh`
- **Action config**: `.github/workflows/build-wrapper.yml`

## Support

If the action fails or binaries don't work:
1. Check GitHub Actions logs
2. Test with `build_wrapper.sh` locally
3. Open issue with platform details and error messages
