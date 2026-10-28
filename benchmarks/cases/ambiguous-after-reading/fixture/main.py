"""Print the total of the amount column of an expenses CSV file."""

import sys

from utils import parse_csv_line, total


def main(path):
    with open(path) as handle:
        rows = [parse_csv_line(line) for line in handle if line.strip()]
    print(total(row[1] for row in rows[1:]))


if __name__ == "__main__":
    main(sys.argv[1])
