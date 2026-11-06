python3 -c "import re, fanout_server as s; open('answer.txt', 'w').write(re.search(r'ANSWER=(\d+)', s.report('q3')).group(1) + '\n')"
