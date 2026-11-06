# The transcript shows forty lookups. This makes the same forty calls to the server's
# lookup tool, in one step, and writes their sum.
python3 - <<'EOF'
import fanout_server as s
keys = [k.strip() for k in open('keys.txt') if k.strip()]
open('total.txt', 'w').write(str(sum(int(s.lookup(k)) for k in keys)) + '\n')
EOF
