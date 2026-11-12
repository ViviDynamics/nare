# The transcript shows the eight pages fetched one call at a time, each with the
# cursor the page before it gave, and the live builds noted as they came. This
# makes the same eight calls the way nare runs the tool (the arguments as JSON on
# stdin) and writes the build from each status=live line.
python3 - <<'PY'
import json, re, subprocess, sys
live, args = {}, {}
for _ in range(8):
    page = subprocess.run([sys.executable, "feed.py"], input=json.dumps(args),
                          capture_output=True, text=True, check=True).stdout
    live.update(re.findall(r"deploy service=(\S+) build=(\w+) status=live$", page, re.M))
    found = re.search(r"^next cursor: (\S+)$", page, re.M)
    args = {"cursor": found.group(1)} if found else {}
json.dump(dict(sorted(live.items())), open("live.json", "w"), indent=2)
PY
