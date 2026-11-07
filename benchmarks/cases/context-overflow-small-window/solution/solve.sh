# The transcript shows the eight reads as eight calls. This runs the same eight
# commands, one step, and writes the sentinels each one printed.
python3 - <<'EOF'
import json, re, subprocess, sys
found = []
for n in range(1, 9):
    out = subprocess.run([sys.executable, "log.py", str(n)], capture_output=True,
                         text=True, check=True).stdout
    found.append(re.search(r"SENTINEL_\d+=confirmed", out).group(0))
json.dump(found, open("findings.json", "w"))
EOF
