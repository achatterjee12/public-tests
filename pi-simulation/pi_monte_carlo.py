import random
import sys


def main():
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    inside = 0
    for i in range(1, iterations + 1):
        x, y = random.random(), random.random()
        if x * x + y * y <= 1.0:
            inside += 1
        print(f"{i} {4 * inside / i:.10f}")


if __name__ == "__main__":
    main()
