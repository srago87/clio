#!/usr/bin/env bash
# Resets the manual test workspace to a known state.
# Run this before starting a test session.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$SCRIPT_DIR/workspace"

rm -rf "$WORKSPACE"
mkdir -p "$WORKSPACE/subdir"

# hello.py — Python file with two functions
cat > "$WORKSPACE/hello.py" << 'EOF'
def greet(name):
    return f"Hello, {name}!"

def farewell(name):
    return f"Goodbye, {name}!"

if __name__ == "__main__":
    print(greet("world"))
EOF

# notes.txt — plain text with known content
cat > "$WORKSPACE/notes.txt" << 'EOF'
Meeting notes - Project Alpha

- Discussed timeline for Q3 deliverables
- Assigned backend work to the engineering team
- Frontend review scheduled for next week
- Budget approved for additional tooling
EOF

# config.json — JSON file with known values
cat > "$WORKSPACE/config.json" << 'EOF'
{
  "app_name": "TestApp",
  "version": "1.0.0",
  "debug": false,
  "max_retries": 3
}
EOF

# subdir/readme.txt — nested file for directory listing test
cat > "$WORKSPACE/subdir/readme.txt" << 'EOF'
This is a subdirectory used for testing list_directory and find_files.
EOF

echo "Workspace reset at: $WORKSPACE"
echo ""
echo "Contents:"
find "$WORKSPACE" -type f | sort
