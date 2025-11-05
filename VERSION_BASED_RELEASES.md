# Version-Based Automatic Releases

## Overview

Saltpeter uses **version-based automatic releases** instead of manual git tags. The GitHub Action reads the version from `saltpeter/version.py` and automatically creates releases when the version changes.

## How It Works

### 1. Version Detection

The GitHub Action extracts the version from `saltpeter/version.py`:
```python
__version__ = '0.6.5'
```

### 2. Automatic Release Creation

On every push to `master` or `async-agent` branches:
1. **Build Phase**: Creates 6 wrapper binaries (2 Ubuntu versions × 3 Python versions)
2. **Upload Artifacts**: Stores binaries for 90 days
3. **Check Release**: Checks if release `v{version}` already exists
4. **Create Release** (if new version):
   - Creates git tag `v{version}`
   - Creates GitHub Release
   - Attaches all 6 binary archives
   - Includes detailed installation instructions

### 3. Release Workflow

```
Push to master/async-agent
         ↓
Extract version from version.py
         ↓
Build 6 wrapper binaries
         ↓
Upload as artifacts (always)
         ↓
Check: Does release v{version} exist?
         ↓
    ┌────┴────┐
   YES       NO
    ↓         ↓
  Skip    Create Release
           - Tag v{version}
           - Attach binaries
           - Add notes
```

## Creating a New Release

### Step 1: Update Version

Edit `saltpeter/version.py`:
```python
__version__ = '1.0.0'  # Changed from 0.6.5
```

### Step 2: Commit and Push

```bash
git add saltpeter/version.py
git commit -m "Bump version to 1.0.0"
git push origin master
```

### Step 3: Wait for Action

The GitHub Action will:
- ✅ Build wrapper binaries
- ✅ Create release `v1.0.0`
- ✅ Tag commit as `v1.0.0`
- ✅ Attach binaries to release

**That's it!** No manual tagging needed.

## Benefits

### ✅ Single Source of Truth
- Version defined in one place: `saltpeter/version.py`
- Used by both Python package and binary releases
- No confusion between git tags and code versions

### ✅ Automatic Release Creation
- Push version bump → automatic release
- No need to remember git tag commands
- Consistent release process

### ✅ Idempotent
- Pushing same version multiple times is safe
- Release created only once per version
- Rebuilds binaries but skips release if exists

### ✅ Development Friendly
- Every push creates artifacts (for testing)
- Only new versions create releases
- Easy to test changes without releasing

## Version Numbering

Follow [Semantic Versioning](https://semver.org/):

```
MAJOR.MINOR.PATCH

1.0.0 → 1.0.1  (patch: bug fixes)
1.0.1 → 1.1.0  (minor: new features)
1.1.0 → 2.0.0  (major: breaking changes)
```

### Examples

**Bug fix release:**
```python
# Before
__version__ = '1.0.0'

# After
__version__ = '1.0.1'
```

**New feature release:**
```python
# Before
__version__ = '1.0.1'

# After
__version__ = '1.1.0'
```

**Breaking change release:**
```python
# Before
__version__ = '1.1.0'

# After
__version__ = '2.0.0'
```

## Development Versions

For development branches, use `-dev` suffix:

```python
__version__ = '1.1.0-dev'
```

This prevents accidental releases during development:
- Builds artifacts normally
- Does NOT create releases (semantic versions only)
- Clear indicator of non-release version

When ready to release:
```python
__version__ = '1.1.0'  # Remove -dev suffix
```

## Checking Current Version

### From Code
```bash
python -c "from saltpeter.version import __version__; print(__version__)"
```

### From Installed Package
```bash
saltpeter --version  # If CLI supports it
```

### From Git Tags
```bash
git tag | sort -V | tail -1
```

### From Latest Release
```bash
gh release list | head -1
```

## Hotfix Workflow

For urgent production fixes:

### 1. Branch from Release Tag
```bash
git checkout v1.0.0
git checkout -b hotfix-1.0.1
```

### 2. Make Fix
```python
# saltpeter/version.py
__version__ = '1.0.1'
```

### 3. Commit and Push
```bash
git add .
git commit -m "Fix critical bug (v1.0.1)"
git push origin hotfix-1.0.1
```

### 4. Merge to Master
```bash
git checkout master
git merge hotfix-1.0.1
git push origin master  # Triggers release
```

### 5. Merge to Development Branch
```bash
git checkout async-agent
git merge hotfix-1.0.1
git push origin async-agent
```

## FAQ

### Q: What if I push the same version twice?
**A:** The action checks if the release exists. If yes, it skips release creation but still builds artifacts.

### Q: Can I manually create a git tag?
**A:** Yes, but not recommended. The action creates tags automatically based on `version.py`.

### Q: What if version.py has invalid format?
**A:** The action will fail. Ensure format is: `__version__ = 'X.Y.Z'`

### Q: Can I create releases from feature branches?
**A:** No. Releases are only created from `master` or `async-agent` branches.

### Q: How do I delete a release?
**A:** Use GitHub web UI or:
```bash
gh release delete v1.0.0
git tag -d v1.0.0
git push origin :refs/tags/v1.0.0
```

### Q: What happens if the build fails?
**A:** Release is NOT created. Fix the issue, push again.

### Q: Can I create pre-releases (beta, rc)?
**A:** Yes, use version suffixes:
```python
__version__ = '1.0.0-beta.1'
__version__ = '1.0.0-rc.1'
```

Currently treated as normal releases. Could be enhanced to mark as pre-release.

## Troubleshooting

### Release Not Created

Check:
- [ ] Version in `version.py` changed
- [ ] Pushed to `master` or `async-agent` branch
- [ ] GitHub Action completed successfully
- [ ] Release with this version doesn't already exist

### Version Extraction Failed

Ensure `version.py` format:
```python
__version__ = '1.0.0'  # ✅ Correct

__version__ = "1.0.0"  # ❌ Wrong: double quotes
VERSION = '1.0.0'      # ❌ Wrong: different variable name
__version__='1.0.0'    # ⚠️  Works but use spaces
```

### Permission Denied

The action uses `GITHUB_TOKEN`. If it fails:
1. Check repository settings → Actions → General
2. Ensure "Read and write permissions" enabled
3. Ensure "Allow GitHub Actions to create and approve pull requests" enabled

## Migration from Tag-Based Releases

If you previously used manual git tags:

### 1. Sync version.py with latest tag
```bash
LATEST_TAG=$(git describe --tags --abbrev=0)
VERSION=${LATEST_TAG#v}  # Remove 'v' prefix
echo "__version__ = '$VERSION'" > saltpeter/version.py
```

### 2. Commit the change
```bash
git add saltpeter/version.py
git commit -m "Sync version.py with git tags"
```

### 3. From now on, update version.py only
No more manual tagging needed!

## Implementation Details

### GitHub Action Steps

1. **Version Extraction**
   ```bash
   VERSION=$(grep -oP "__version__ = '\K[^']+" saltpeter/version.py)
   ```

2. **Release Check**
   ```bash
   gh release view "v$VERSION" &>/dev/null
   ```

3. **Release Creation**
   ```yaml
   uses: softprops/action-gh-release@v1
   with:
     tag_name: v${{ steps.get_version.outputs.version }}
   ```

### Files Modified

- `.github/workflows/build-wrapper.yml` - Main workflow
- `saltpeter/version.py` - Source of truth for version
- `setup.py` - Should read from version.py

### Recommended setup.py

Ensure `setup.py` reads version dynamically:

```python
import os
import re

def get_version():
    version_file = os.path.join('saltpeter', 'version.py')
    with open(version_file) as f:
        content = f.read()
        match = re.search(r"__version__ = ['\"]([^'\"]+)['\"]", content)
        if match:
            return match.group(1)
    raise RuntimeError("Unable to find version string.")

setup(
    name='saltpeter',
    version=get_version(),
    # ... rest of setup
)
```

This ensures PyPI and GitHub releases have the same version.
