"""乾坤镜 Mingjing — `python -m mingjing` entry point."""

import sys


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("乾坤镜 Mingjing v0.11.11")
        print()
        print("Usage:")
        print("  python -m mingjing demo     Emit a test event")
        print("  python -m mingjing health   Check archiver status")
        print("  python -m mingjing --version  Show version")
        print()
        print("Or use the `ming` CLI:")
        print("  ming start    Start archiver")
        print("  ming web      Start Web dashboard")
        print("  ming dx list  View diagnostics")
        return

    if args[0] == "--version":
        from ming import VERSION

        print(f"mingjing {VERSION}")
        return

    if args[0] == "demo":
        from demo import emit_test_event

        emit_test_event()
        return

    if args[0] == "health":
        from cli_health import check_health

        check_health()
        return

    print(f"Unknown command: {args[0]}")
    sys.exit(1)


if __name__ == "__main__":
    main()
