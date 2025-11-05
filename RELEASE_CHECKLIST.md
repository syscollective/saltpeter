# Release Checklist

Complete checklist for creating a new Saltpeter release with wrapper binary.

## Pre-Release

### 1. Code Preparation

- [ ] All features complete and tested
- [ ] No pending bug fixes
- [ ] Code reviewed
- [ ] Tests passing

### 2. Version Update

Update version in:
- [ ] `saltpeter/version.py` - Update `__version__`
- [ ] `setup.py` - Update `version=`
- [ ] `README.md` - Update version references

Example version change:
```python
# saltpeter/version.py
__version__ = '1.0.0'
```

### 3. Changelog

- [ ] Update `CHANGELOG.md` (or create if missing)
- [ ] Document all changes since last release
- [ ] Group by: Features, Bug Fixes, Breaking Changes

Example:
```markdown
## [1.0.0] - 2024-01-15

### Added
- Bidirectional WebSocket communication
- Per-machine kill functionality
- Wrapper binary builds via GitHub Action

### Fixed
- Heartbeat timeout bug
- Wrapper detachment using double-fork pattern

### Changed
- Wrapper usage now optional via use_wrapper config
```

### 4. Documentation

- [ ] Update `README.md` with new features
- [ ] Update `QUICKSTART.md` if needed
- [ ] Verify all `.md` files are current
- [ ] Add migration notes if breaking changes exist

### 5. Testing

Local testing:
```bash
# Test wrapper build locally
./build_wrapper.sh

# Verify binary works
./dist/linux-x86_64-ubuntu*/python*/sp_wrapper

# Test with actual Saltpeter setup
# (configure test environment)
```

### 6. Commit Changes

```bash
git add .
git commit -m "Prepare release v1.0.0"
git push origin async-agent  # or master
```

## Release Process

### 1. Update Version

Edit `saltpeter/version.py`:
```python
__version__ = '1.0.0'
```

### 2. Commit and Push

```bash
git add saltpeter/version.py
git commit -m "Release v1.0.0"
git push origin master  # or async-agent
```

**No need to create git tags manually!** The GitHub Action will:
- Detect the version from `version.py`
- Check if release `v1.0.0` exists
- If not, automatically create the release with tag `v1.0.0`

### 2. Monitor GitHub Action

```bash
# Open in browser
https://github.com/syscollective/saltpeter/actions

# Wait for "Build Wrapper Binary" workflow to complete
# Should take 3-5 minutes
```

Verify:
- [ ] All 6 builds successful (Ubuntu 20/22 × Python 3.9/3.10/3.11)
- [ ] Artifacts uploaded
- [ ] Release created automatically (if version changed)

### 3. Verify Release

Check release page:
```
https://github.com/syscollective/saltpeter/releases/tag/v1.0.0
```

Verify:
- [ ] All 6 binary archives attached
- [ ] Release notes present
- [ ] VERSION.txt in archives
- [ ] Binaries downloadable

### 4. Test Release Binaries

Download and test:
```bash
# Download a binary
wget https://github.com/syscollective/saltpeter/releases/download/v1.0.0/sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz

# Extract
tar -xzf sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz

# Verify
./sp_wrapper  # Should show usage/error
file sp_wrapper  # Should show: ELF 64-bit executable
ldd sp_wrapper  # Should show minimal dependencies
```

### 5. Update Release Notes (Optional)

If auto-generated notes need editing:
- [ ] Go to release page
- [ ] Click "Edit release"
- [ ] Update description
- [ ] Add examples, screenshots, etc.

## Post-Release

### 1. Announce Release

- [ ] Update project README with latest version
- [ ] Post to relevant channels/forums
- [ ] Update documentation sites if applicable

### 2. PyPI Release (Optional)

If publishing to PyPI:
```bash
# Build distribution
python setup.py sdist bdist_wheel

# Upload to PyPI
twine upload dist/*
```

### 3. Create Next Development Branch

```bash
# Bump version for next development cycle
# E.g., 1.0.0 -> 1.1.0-dev

# Update saltpeter/version.py
__version__ = '1.1.0-dev'

git add saltpeter/version.py
git commit -m "Bump version to 1.1.0-dev"
git push
```

## Deployment to Production

### 1. Backup Current Installation

```bash
# On Saltpeter server
sudo systemctl stop saltpeter
sudo cp -r /etc/saltpeter /etc/saltpeter.backup
sudo cp /usr/local/bin/saltpeter /usr/local/bin/saltpeter.backup
```

### 2. Deploy New Wrapper Binary

```bash
# Download wrapper
cd /tmp
wget https://github.com/syscollective/saltpeter/releases/download/v1.0.0/sp_wrapper-linux-x86_64-ubuntu22-py3.11.tar.gz
tar -xzf sp_wrapper-*.tar.gz

# Copy to Salt file server
sudo cp sp_wrapper /srv/salt/sp_wrapper

# Distribute to all minions
salt '*' cp.get_file salt://sp_wrapper /usr/local/bin/sp_wrapper
salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper'

# Verify deployment
salt '*' cmd.run '/usr/local/bin/sp_wrapper 2>&1' | grep -E "SP_COMMAND|error"
```

### 3. Update Saltpeter Server

```bash
# Pull latest code
cd /path/to/saltpeter
git fetch --tags
git checkout v1.0.0

# Install/upgrade Python dependencies
pip install --upgrade -r requirements.txt

# Update systemd service if needed
sudo cp examples/saltpeter.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 4. Update Configuration

```bash
# Edit job configs if needed for new features
sudo vim /etc/saltpeter/jobs.yaml

# Example: Add use_wrapper to jobs
# my_job:
#   use_wrapper: true
#   wrapper_path: /usr/local/bin/sp_wrapper
```

### 5. Restart Services

```bash
# Restart Saltpeter
sudo systemctl restart saltpeter

# Check status
sudo systemctl status saltpeter

# Check logs
sudo journalctl -u saltpeter -f
```

### 6. Verify Functionality

```bash
# Trigger a test job manually
# Check WebSocket connections
# Verify job execution
# Test kill functionality
```

## Rollback Procedure

If issues occur:

### 1. Quick Rollback

```bash
# Stop Saltpeter
sudo systemctl stop saltpeter

# Restore backup
sudo cp /usr/local/bin/saltpeter.backup /usr/local/bin/saltpeter
sudo cp -r /etc/saltpeter.backup/* /etc/saltpeter/

# Checkout previous version
cd /path/to/saltpeter
git checkout v0.9.0  # Previous working version

# Restart
sudo systemctl start saltpeter
```

### 2. Rollback Wrapper on Minions

```bash
# If old wrapper is still in Salt file server
salt '*' cp.get_file salt://sp_wrapper.old /usr/local/bin/sp_wrapper

# Or remove wrapper and fall back to Python-based execution
salt '*' cmd.run 'rm /usr/local/bin/sp_wrapper'
# Set use_wrapper: false in configs
```

## Version Numbering Scheme

Follow Semantic Versioning (semver.org):

- **Major** (X.0.0): Breaking changes
  - API changes
  - Config format changes
  - Removed features

- **Minor** (x.Y.0): New features, backward compatible
  - New features
  - New config options (with defaults)
  - Deprecations

- **Patch** (x.y.Z): Bug fixes only
  - Bug fixes
  - Performance improvements
  - Documentation updates

Examples:
- `1.0.0` → `1.0.1`: Bug fixes
- `1.0.1` → `1.1.0`: New features
- `1.1.0` → `2.0.0`: Breaking changes

## Hotfix Process

For urgent fixes to production:

```bash
# Create hotfix branch from release tag
git checkout -b hotfix-1.0.1 v1.0.0

# Make fixes
# ... edit files ...

# Commit
git commit -m "Fix critical bug in wrapper"

# Tag hotfix
git tag -a v1.0.1 -m "Hotfix v1.0.1 - Fix wrapper crash"

# Push
git push origin hotfix-1.0.1
git push origin v1.0.1

# Merge back to main branches
git checkout master
git merge hotfix-1.0.1
git push origin master
```

## Release Types

### Alpha Release (v1.0.0-alpha.1)
- Early testing
- May have bugs
- API may change
- Not for production

### Beta Release (v1.0.0-beta.1)
- Feature complete
- API frozen
- Testing phase
- Use in staging only

### Release Candidate (v1.0.0-rc.1)
- No known critical bugs
- Final testing
- Ready for production if no issues found

### Stable Release (v1.0.0)
- Production ready
- Fully tested
- Documented
- Supported

## Checklist Summary

Quick reference:

```
Pre-Release:
☐ Update version numbers
☐ Update changelog
☐ Update documentation
☐ Test locally
☐ Commit changes

Release:
☐ Create and push tag
☐ Monitor GitHub Action
☐ Verify release created
☐ Test release binaries

Post-Release:
☐ Announce release
☐ Deploy to production
☐ Verify deployment
☐ Bump dev version

Production Deployment:
☐ Backup current installation
☐ Deploy wrapper to minions
☐ Update server code
☐ Update configuration
☐ Restart services
☐ Verify functionality
```
