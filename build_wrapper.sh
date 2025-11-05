#!/bin/bash
# Local build script for testing wrapper binary creation
# This mimics what the GitHub Action does

set -e

echo "=== Saltpeter Wrapper Binary Builder ==="
echo ""

# Check if pyinstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
fi

# Check if websockets is installed
python3 -c "import websockets" 2>/dev/null || {
    echo "websockets module not found. Installing..."
    pip install websockets
}

# Extract version from version.py
VERSION=$(grep -oP "__version__ = '\K[^']+" saltpeter/version.py)
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d. -f1-2)

echo "Saltpeter Version: $VERSION"
echo "Python: $PYTHON_VERSION"
echo ""

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.spec

# Build the wrapper
echo "Building wrapper binary..."
pyinstaller --onefile \
    --name sp_wrapper \
    --distpath dist \
    --clean \
    saltpeter/wrapper.py

# Create version file
echo "Creating version info..."
cat > dist/VERSION.txt << EOF
Saltpeter Wrapper Binary
Version: $VERSION
Platform: linux-x86_64 (glibc $(ldd --version 2>/dev/null | head -1 | grep -oP '\d+\.\d+$' || echo "unknown"))
Python: $PYTHON_VERSION (embedded)
Built: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
Git Commit: $(git rev-parse HEAD 2>/dev/null || echo "unknown")
Git Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
Built by: $(whoami)@$(hostname)

Compatible with:
- Ubuntu 20.04+ / Debian 11+ / RHEL 8+
- Any Linux x86_64 with glibc 2.31 or newer
EOF

# Make executable
chmod +x dist/sp_wrapper

# Test the binary
echo ""
echo "=== Testing binary ==="
dist/sp_wrapper 2>&1 | head -5 || true

# Show file info
echo ""
echo "=== Binary info ==="
ls -lh dist/sp_wrapper
file dist/sp_wrapper
echo "Dependencies:"
ldd dist/sp_wrapper | grep -E "libc|libpthread" || true

# Create tarball
echo ""
echo "=== Creating release archive ==="
ARCHIVE_NAME="sp_wrapper.tar.gz"
tar -czf "$ARCHIVE_NAME" -C dist .
ls -lh "$ARCHIVE_NAME"

echo ""
echo "=== Build complete! ==="
echo "Binary: dist/sp_wrapper"
echo "Archive: $ARCHIVE_NAME"
echo ""
echo "To install locally:"
echo "  sudo cp dist/sp_wrapper /usr/local/bin/"
echo ""
echo "To deploy via Salt:"
echo "  sudo cp dist/sp_wrapper /srv/salt/sp_wrapper"
echo "  salt '*' cp.get_file salt://sp_wrapper /usr/local/bin/sp_wrapper"
echo "  salt '*' cmd.run 'chmod +x /usr/local/bin/sp_wrapper'"
