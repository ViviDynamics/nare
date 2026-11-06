python3 -c "import json; json.dump([f'SENTINEL_{n}=confirmed' for n in range(1, 9)], open('findings.json', 'w'))"
