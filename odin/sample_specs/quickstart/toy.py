import sys

STEPS = ["load", "process", "save"]


def run():
    for step in STEPS:
        print(f"done: {step}")


if __name__ == "__main__":
    run()
