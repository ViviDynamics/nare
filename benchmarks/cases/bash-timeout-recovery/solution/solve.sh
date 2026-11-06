# Run slow.sh the way the task says, then record what it did.
timeout 5 sh ./slow.sh > /dev/null 2>&1
status=$?
if [ "$status" -eq 124 ]; then
  echo "slow.sh hung: under timeout 5 it timed out (status 124), and I did not wait for it." > notes.txt
else
  echo "slow.sh exited with status $status under timeout 5." > notes.txt
fi
